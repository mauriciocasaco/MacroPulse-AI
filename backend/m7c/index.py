"""Local E5 inference and a transactional SQLite vector cache."""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from hashlib import file_digest
from importlib.metadata import PackageNotFoundError, version
import math
from pathlib import Path
import sqlite3
from struct import Struct

from backend.news.embeddings import E5Small
from .corpus import Corpus, digest, json_bytes

SPEC = E5Small.spec
WEIGHTS_SHA256 = "1a55775f53449dac10a2bcbc312469fac40b96d53198c407081a831f81c98477"
FILES = (
    "model.safetensors", "config.json", "modules.json", "1_Pooling/config.json",
    "sentence_bert_config.json", "sentencepiece.bpe.model", "special_tokens_map.json",
    "tokenizer_config.json", "tokenizer.json",
)
FORMAT = 1
APP_ID = 0x4D375243
VECTOR = Struct(f"<{SPEC.dimensions}f")


class ModelUnavailable(ValueError):
    """The local pinned model or inference runtime is unavailable."""


def cache_fingerprints(cache: Path) -> dict[str, str]:
    snapshot = cache / ("models--" + SPEC.model_id.replace("/", "--")) / "snapshots" / SPEC.revision
    if any(not (snapshot / name).is_file() for name in FILES):
        raise ModelUnavailable(
            f"Missing complete local E5 cache for {SPEC.model_id} at {SPEC.revision}. "
            "Pass --model-cache pointing to an existing Hugging Face cache."
        )
    hashes = {}
    for name in FILES:
        with (snapshot / name).open("rb") as stream:
            hashes[name] = file_digest(stream, "sha256").hexdigest()
    if hashes["model.safetensors"] != WEIGHTS_SHA256:
        raise ModelUnavailable("Cached E5 weights do not match the SHA-256 recorded by the news pilot.")
    return hashes


class LocalE5:
    """Use the existing E5Small adapter with offline weights and its own prefixes."""

    def __init__(self, cache: Path):
        files = cache_fingerprints(cache)
        try:
            packages = {name: version(name) for name in ("torch", "transformers", "sentence-transformers", "tokenizers", "numpy")}
        except PackageNotFoundError as exc:
            raise ModelUnavailable(f"Missing local inference dependency: {exc.name}. See docs/m7c/README.md.") from None
        try:
            self.model = E5Small(cache)
        except (ImportError, OSError, RuntimeError, ValueError) as exc:
            raise ModelUnavailable(f"Could not load the pinned E5 snapshot offline ({type(exc).__name__}).") from None
        self.provenance = {
            "model_id": SPEC.model_id, "revision": SPEC.revision, "dimensions": SPEC.dimensions,
            "device": "cpu", "cache_files_sha256": files, "packages": packages, "format": FORMAT,
        }
        self.fingerprint = digest(json_bytes(self.provenance))

    def encode(self, text: str, *, query: bool) -> bytes:
        prefix = "query: " if query else "passage: "
        count = len(self.model.tokenizer(prefix + text, truncation=False)["input_ids"])
        if count > min(512, self.model.tokenizer.model_max_length):
            raise ValueError("E5 input exceeds the model token budget")
        values = self.model.embed(text, query=query)
        if len(values) != SPEC.dimensions or not all(math.isfinite(v) for v in values):
            raise ValueError("Invalid E5 vector")
        norm = math.hypot(*values)
        if norm <= 0:
            raise ValueError("E5 vector has zero norm")
        return VECTOR.pack(*(value / norm for value in values))


def decode(raw: bytes, checksum: str) -> tuple[float, ...]:
    if len(raw) != VECTOR.size or digest(raw) != checksum:
        raise ValueError("Corrupt vector length or fingerprint")
    vector = VECTOR.unpack(raw)
    if not math.isclose(math.hypot(*vector), 1.0, rel_tol=1e-6):
        raise ValueError("Invalid vector norm")
    return vector


@dataclass(frozen=True)
class BuildResult:
    passages: int
    generated: int
    reused: int
    removed: int
    corpus_sha256: str
    model_sha256: str
    vectors_sha256: str


def aggregate_hash(rows: dict[str, tuple[bytes, str]]) -> str:
    return digest(json_bytes({key: value[1] for key, value in rows.items()}))


def assert_format(db: sqlite3.Connection, *, initialize: bool = False) -> None:
    app_id = db.execute("PRAGMA application_id").fetchone()[0]
    schema_version = db.execute("PRAGMA user_version").fetchone()[0]
    if app_id == APP_ID and schema_version == FORMAT:
        return
    if not initialize or app_id or schema_version or db.execute("SELECT 1 FROM sqlite_schema LIMIT 1").fetchone():
        raise ValueError("File is not a supported M7.C index")
    db.execute("CREATE TABLE metadata (name TEXT PRIMARY KEY, value TEXT NOT NULL)")
    db.execute("""CREATE TABLE vectors (
        content_sha256 TEXT PRIMARY KEY, embedding BLOB NOT NULL, vector_sha256 TEXT NOT NULL
    )""")
    db.execute(f"PRAGMA application_id = {APP_ID}")
    db.execute(f"PRAGMA user_version = {FORMAT}")


def build_index(path: Path, corpus: Corpus, engine: LocalE5) -> BuildResult:
    """Update atomically. A failed build leaves the previous complete version usable."""
    path.parent.mkdir(parents=True, exist_ok=True)
    inputs = {passage.content_sha256: passage.text for passage in corpus.passages.values()}
    with closing(sqlite3.connect(path, timeout=15)) as db, db:
        db.execute("PRAGMA trusted_schema = OFF")
        db.execute("BEGIN IMMEDIATE")
        assert_format(db, initialize=True)
        old = dict(db.execute("SELECT name, value FROM metadata"))
        existing = {key: (blob, checksum) for key, blob, checksum in db.execute("SELECT * FROM vectors")}
        removed = len(existing.keys() - inputs.keys())
        reused = generated = 0
        if old.get("model_sha256") != engine.fingerprint:
            db.execute("DELETE FROM vectors")
            existing = {}
        else:
            db.executemany("DELETE FROM vectors WHERE content_sha256 = ?",
                           ((key,) for key in existing.keys() - inputs.keys()))
        rows = {}
        for key, text in sorted(inputs.items()):
            if key in existing:
                try:
                    decode(*existing[key])
                except ValueError:
                    pass
                else:
                    rows[key] = existing[key]
                    reused += 1
                    continue
            blob = engine.encode(text, query=False)
            checksum = digest(blob)
            decode(blob, checksum)
            db.execute("INSERT OR REPLACE INTO vectors VALUES (?, ?, ?)", (key, blob, checksum))
            rows[key] = blob, checksum
            generated += 1
        vectors_sha256 = aggregate_hash(rows)
        db.executemany("INSERT OR REPLACE INTO metadata VALUES (?, ?)", (
            ("corpus_sha256", corpus.fingerprint),
            ("model_sha256", engine.fingerprint),
            ("vectors_sha256", vectors_sha256),
        ))
    return BuildResult(len(corpus.passages), generated, reused, removed,
                       corpus.fingerprint, engine.fingerprint, vectors_sha256)


def read_vectors(path: Path, corpus: Corpus, engine: LocalE5) -> dict[str, tuple[float, ...]]:
    if not path.is_file():
        raise ValueError("Missing M7.C index. Run the build command first.")
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("PRAGMA trusted_schema = OFF")
        db.execute("BEGIN")
        assert_format(db)
        metadata = dict(db.execute("SELECT name, value FROM metadata"))
        if metadata.get("corpus_sha256") != corpus.fingerprint or metadata.get("model_sha256") != engine.fingerprint:
            raise ValueError("Index corpus or model fingerprint changed. Rebuild before querying.")
        rows = {key: (blob, checksum) for key, blob, checksum in db.execute("SELECT * FROM vectors")}
    if set(rows) != {item.content_sha256 for item in corpus.passages.values()}:
        raise ValueError("Vector inventory does not match the approved passages")
    if metadata.get("vectors_sha256") != aggregate_hash(rows):
        raise ValueError("Vector inventory fingerprint differs")
    vectors = {key: decode(*pair) for key, pair in rows.items()}
    return {key: vectors[item.content_sha256] for key, item in corpus.passages.items()}
