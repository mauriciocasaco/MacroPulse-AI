"""Bounded GKG 2.1 archive adapter. GDELT fields are untrusted metadata."""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import re
from urllib.parse import unquote, unquote_plus, urlsplit, urlunsplit
from zipfile import ZipFile


SOURCE_ID = "gdelt_gkg"
SOURCE_NAME = "GDELT Project"
TERMS_URL = "https://www.gdeltproject.org/about.html"
ARCHIVE_ROOT = "https://data.gdeltproject.org/gdeltv2/"
ADAPTER_VERSION = "gkg-2.1-v1"
_MAX_COMPRESSED = 16 * 1024 * 1024
_MAX_EXPANDED = 128 * 1024 * 1024
_MAX_ROW = 2 * 1024 * 1024
_RECORD_ID = re.compile(r"[0-9]{14}-T?[0-9]{1,12}")
_ARCHIVE = re.compile(r"([0-9]{14})\.gkg\.csv\.zip")
_PRECISE_TAG = re.compile(r"<PAGE_PRECISEPUBTIMESTAMP>([^<>]{1,80})</PAGE_PRECISEPUBTIMESTAMP>")
_TRACKING = {"fbclid", "gclid", "mc_cid", "mc_eid"}
_SENSITIVE = {"key", "auth", "authorization", "session", "sessionid", "code", "jwt", "sig", "email", "phone", "username"}


@dataclass(frozen=True)
class Record:
    external_id: str
    source_url: str
    canonical_url: str
    publisher: str
    observed_at: datetime
    retrieved_at: datetime
    provenance_ref: str
    published_at: datetime | None
    published_on: date | None
    publication_raw: str | None
    themes: tuple[str, ...]
    entities: tuple[str, ...]


@dataclass(frozen=True)
class Batch:
    provenance_ref: str
    archive_sha256: str
    selection: tuple[str, ...]
    processed: int
    records: tuple[Record, ...]
    rejected: dict[str, int]
    duplicates: int


def _utc_timestamp(value: str) -> datetime:
    if not re.fullmatch(r"[0-9]{14}", value):
        raise ValueError("invalid GKG timestamp")
    return datetime.strptime(value, "%Y%m%d%H%M%S").replace(tzinfo=UTC)


def utc(value: datetime) -> datetime:
    if value.utcoffset() is None:
        raise ValueError("timestamp requires an explicit timezone")
    return value.astimezone(UTC)


def canonicalize_url(value: str) -> str:
    """Keep path, scheme, port and meaningful query bytes; remove tracking and fragments."""
    if len(value) > 8192 or any(c.isspace() or ord(c) < 32 for c in value) or "\\" in value:
        raise ValueError("invalid URL")
    try:
        url = urlsplit(value)
        host = url.hostname
        url.port  # Validate the port without changing explicit ports in the identity.
    except ValueError:
        raise ValueError("invalid URL") from None
    if url.scheme not in ("https", "http") or not host or url.username is not None or "%" in url.netloc:
        raise ValueError("invalid URL")
    for item in re.split(r"[&;]", url.query):
        key = unquote_plus(item.partition("=")[0]).lower()
        if key in _SENSITIVE or any(
            part in key for part in ("token", "secret", "password", "api_key", "apikey", "credential", "signature")
        ):
            raise ValueError("sensitive URL parameter")
    query = []
    for item in url.query.split("&") if url.query else ():
        key = unquote_plus(item.partition("=")[0]).lower()
        if not key.startswith("utm_") and key not in _TRACKING:
            query.append(item)
    # The caller persists the original query for provenance, but never the fragment.
    return urlunsplit((url.scheme, url.netloc.lower(), url.path or "/", "&".join(query), ""))


def _publication(extra: str) -> tuple[datetime | None, date | None, str | None]:
    values = set(_PRECISE_TAG.findall(extra))
    if len(values) != 1:
        return None, None, None
    raw = values.pop().strip()
    # Explicit offsets can be converted. GKG's optional precise tag is documented in UTC.
    try:
        if re.fullmatch(r"[0-9]{8}", raw):
            return None, datetime.strptime(raw, "%Y%m%d").date(), raw
        if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", raw):
            return None, date.fromisoformat(raw), raw
        if re.fullmatch(r"[0-9]{14}", raw):
            return _utc_timestamp(raw), None, raw
        if re.fullmatch(r"[0-9TZ:+. -]{16,35}", raw):
            instant = datetime.fromisoformat(raw)
            return utc(instant), None, raw
    except ValueError:
        pass
    return None, None, None


def _labels(value: str) -> tuple[str, ...]:
    result = []
    seen = set()
    for item in value.split(";"):
        label = " ".join(item.split()).strip()
        if not label or len(label) > 120 or any(ord(c) < 32 for c in label):
            continue
        key = label.casefold()
        if key not in seen:
            result.append(label)
            seen.add(key)
        if len(result) == 128:
            break
    return tuple(result)


def parse_row(fields: list[str], *, archive_ref: str, retrieved_at: datetime) -> Record:
    # Use the legacy theme and organization fields; enhanced fields contain offsets.
    # Individual names, quotations and other publisher content are outside this pilot.
    if len(fields) != 27 or not _RECORD_ID.fullmatch(fields[0]):
        raise ValueError("malformed row")
    if fields[2] != "1":
        raise ValueError("unsupported source type")
    observed = _utc_timestamp(fields[1])
    retrieved = utc(retrieved_at)
    if observed > retrieved:
        raise ValueError("observation after retrieval")
    publisher = fields[3].strip().lower()
    if not re.fullmatch(r"[a-z0-9.-]{1,253}", publisher):
        raise ValueError("invalid publisher")
    raw_url = fields[4].strip().partition("#")[0]
    canonical = canonicalize_url(raw_url)
    published_at, published_on, raw = _publication(fields[26])
    if published_at is not None and published_at > observed:
        published_at = None
    if published_on is not None and published_on > observed.date():
        published_on = None
    return Record(
        fields[0], raw_url, canonical, publisher, observed, retrieved, f"{archive_ref}#{fields[0]}",
        published_at, published_on, raw, _labels(fields[7]), _labels(fields[13]),
    )


def read_batch(path: Path, record_ids: list[str], retrieved_at: datetime) -> Batch:
    retrieved_at = utc(retrieved_at)
    match = _ARCHIVE.fullmatch(path.name)
    if match is None:
        raise ValueError("invalid GKG archive name")
    stamp = match[1]
    _utc_timestamp(stamp)
    selection = tuple(sorted(set(record_ids)))
    if not 1 <= len(selection) <= 1000 or any(
        not _RECORD_ID.fullmatch(record_id) or not record_id.startswith(stamp + "-") for record_id in selection
    ):
        raise ValueError("select 1–1000 record IDs from this archive")
    ids = {record_id.encode("ascii") for record_id in selection}
    archive_ref = ARCHIVE_ROOT + path.name
    with path.open("rb") as archive_file:
        archive_bytes = archive_file.read(_MAX_COMPRESSED + 1)
    if len(archive_bytes) > _MAX_COMPRESSED:
        raise ValueError("compressed archive exceeds limit")
    processed = duplicates = expanded = 0
    records: dict[str, Record] = {}
    rejected: dict[str, int] = {}
    seen = set()
    with ZipFile(BytesIO(archive_bytes)) as archive:
        members = archive.infolist()
        if len(members) != 1 or members[0].filename != path.name.removesuffix(".zip"):
            raise ValueError("unexpected GKG archive member")
        if members[0].file_size > _MAX_EXPANDED:
            raise ValueError("expanded archive exceeds limit")
        with archive.open(members[0]) as rows:
            while raw := rows.readline(_MAX_ROW + 1):
                expanded += len(raw)
                if len(raw) > _MAX_ROW or expanded > _MAX_EXPANDED:
                    raise ValueError("expanded GKG data exceeds limit")
                first, _, _ = raw.partition(b"\t")
                if first not in ids:
                    continue
                seen.add(first)
                processed += 1
                try:
                    fields = raw.decode("utf-8").rstrip("\r\n").split("\t")
                    record = parse_row(fields, archive_ref=archive_ref, retrieved_at=retrieved_at)
                except (ValueError, UnicodeError) as exc:
                    reason = "invalid UTF-8" if isinstance(exc, UnicodeError) else str(exc)
                    rejected[reason] = rejected.get(reason, 0) + 1
                    continue
                previous = records.get(record.external_id)
                if previous:
                    if previous != record:
                        raise ValueError("conflicting rows for one provider ID")
                    duplicates += 1
                else:
                    records[record.external_id] = record
    if missing := ids - seen:
        rejected["selected record missing"] = len(missing)
    return Batch(archive_ref, sha256(archive_bytes).hexdigest(), selection, processed, tuple(records.values()), rejected, duplicates)


def url_terms(record: Record) -> str:
    return unquote(urlsplit(record.canonical_url).path).replace("-", " ").replace("_", " ")
