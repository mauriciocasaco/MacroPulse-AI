"""Bounded acquisition of institutional prose and GKG discovery metadata."""
import argparse
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from time import monotonic, sleep
from urllib.parse import urljoin, urlsplit
from zipfile import BadZipFile

from bs4 import BeautifulSoup
import requests

from backend.market_data.catalog import ASSET_SOURCES

from . import gdelt
from .assets import associate

ROOT = Path(__file__).resolve().parents[2] / "data" / "historical-corpus"
EIA = "https://www.eia.gov"
FED = "https://www.federalreserve.gov"
SOURCES = {
    "eia": ("U.S. Energy Information Administration", EIA + "/TODAYINENERGY/about.php"),
    "fed": ("Board of Governors of the Federal Reserve System", FED + "/disclaimer.htm"),
}
WINDOWS = {
    "covid": (date(2020, 2, 1), date(2020, 7, 1)),
    "ukraine": (date(2022, 2, 1), date(2022, 7, 1)),
    "genai": (date(2022, 11, 1), date(2025, 1, 1)),
}
MATCH = {
    "covid": r"covid|coronavirus|pandemic|negative oil|oil futures|wti|cushing",
    "ukraine": r"ukrain|russia|invasion",
    "genai": r"artificial intelligence|generative[ _-]ai|chatgpt",
}
SNAPSHOTS = {
    "covid": "20200420210000", "ukraine": "20220224180000", "genai": "20240110230000",
}
# Institutional series with official release dates for report sections without local datelines.
REPORTS = {
    "covid": ("2020-may-financial-stability-report", "2020-05-15", FED + "/releases/h2/20200516/default.htm"),
    "ukraine": ("2022-may-financial-stability-report", "2022-05-09", FED + "/publications/brainard-statement-20220509.htm"),
}
REPORT_SECTIONS = ("asset-valuation", "funding", "near-term-risks", "overview")
SEEDS = (
    ("covid", "43495", "2020-04-27"),
)
EIA_COVID = r"covid|petroleum products|crude oil (inventories|prices|production to fall)|refiner|liquid fuels inventory"
EIA_UKRAINE = r"russia|ukrain|geopolitical|strategic petroleum reserve|crude oil prices higher"
EIA_GENAI = r"data center|artificial intelligence"
FED_TITLES = {
    "covid": r"covid|coronavirus|pandemic|economic outlook|economic recovery|monetary policy",
    "ukraine": r"ukraine|economic outlook|monetary policy|inflation",
    "genai": MATCH["genai"],
}
EXTERNAL_PROSE = re.compile(
    r"all rights reserved|reproduced (?:with|by) permission|guest (?:author|contributor)|"
    r"reprinted (?:with|by) permission|copyright\s*(?:©|\(c\)|\d{4})|©", re.I,
)


@dataclass(frozen=True)
class Candidate:
    source: str
    event: str
    url: str
    title: str | None
    day: date
    discovered_from: str
    release_url: str | None = None


class PublicHTTP:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "MarketPulse-AI/1.0 (bounded historical research pilot)"
        self.last = 0.0
        self.deferred = set()

    def get(self, url: str, *, archive=False) -> bytes:
        parts = urlsplit(url)
        path = parts.path
        allowed = (
            parts.netloc == "www.eia.gov"
            and re.fullmatch(r"/todayinenergy/(?:archive|detail)\.php", path)
            and re.fullmatch(r"(?:my=20(?:20|22|23|24)|id=\d{1,6})", parts.query)
            or parts.netloc == "www.federalreserve.gov"
            and re.fullmatch(r"/(?:newsevents/(?:20\d{2}-speeches|speech/[a-z0-9]+)|publications/20[a-z0-9-]+)\.htm", path)
            and not parts.query
            or archive and parts.netloc == "data.gdeltproject.org"
            and re.fullmatch(r"/gdeltv2/\d{14}\.gkg\.csv\.zip", path) and not parts.query
        )
        if parts.scheme != "https" or not allowed or parts.fragment:
            raise ValueError("URL outside approved acquisition scope")
        if parts.netloc in self.deferred:
            raise ValueError("host deferred after HTTP rate limit or service outage")
        limit = 16 * 1024 * 1024 if archive else 2 * 1024 * 1024
        for attempt in range(2):
            sleep(max(0, 0.7 - (monotonic() - self.last)))
            self.last = monotonic()
            try:
                with self.session.get(url, timeout=(6, 12), stream=True, allow_redirects=False) as response:
                    if response.status_code in (429, 503):
                        self.deferred.add(parts.netloc)
                        raise ValueError(f"HTTP {response.status_code}: stop and retry in a later run")
                    if response.status_code != 200:
                        raise ValueError(f"HTTP {response.status_code}")
                    expected = ("application/zip", "application/octet-stream") if archive else ("text/html",)
                    if not response.headers.get("Content-Type", "").startswith(expected):
                        raise ValueError("unexpected media type")
                    payload = bytearray()
                    for chunk in response.iter_content(65536):
                        if monotonic() - self.last > 35:
                            raise ValueError("download deadline exceeded")
                        payload.extend(chunk)
                        if len(payload) > limit:
                            raise ValueError("download exceeds byte limit")
                    return bytes(payload)
            except (requests.Timeout, requests.ConnectionError):
                if attempt:
                    raise
                sleep(2)
        raise AssertionError("retry loop exhausted")


def write_json(path: Path, value) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, default=str, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def markup(data: bytes) -> BeautifulSoup:
    return BeautifulSoup(data, "html.parser", from_encoding="utf-8")


def clean(element) -> str:
    return " ".join(element.get_text(" ", strip=True).split())


def stable_id(url: str) -> str:
    return sha256(url.encode()).hexdigest()


def in_window(event: str, day: date) -> bool:
    start, end = WINDOWS[event]
    return start <= day < end


def official_candidates(http: PublicHTTP, errors: list) -> list[Candidate]:
    candidates = {}
    for event, (start, end) in WINDOWS.items():
        last_year = (end - timedelta(days=1)).year
        for year in range(start.year, last_year + 1):
            for source in SOURCES:
                index = (
                    f"{EIA}/todayinenergy/archive.php?my={year}" if source == "eia"
                    else f"{FED}/newsevents/{year}-speeches.htm"
                )
                try:
                    page = markup(http.get(index))
                except (ValueError, requests.RequestException) as exc:
                    errors.append({"url": index, "error": str(exc)})
                    continue
                if source == "eia":
                    selector = "li:has(> .date) > h2 > a"
                    pattern = {"covid": EIA_COVID, "ukraine": EIA_UKRAINE, "genai": EIA_GENAI}[event]
                else:
                    selector = ".eventlist__event p:first-child > a"
                    pattern = FED_TITLES[event]
                matches = []
                for link in page.select(selector):
                    if source == "eia":
                        dateline = link.find_parent("li").select_one(".date")
                        fmt = "%B %d, %Y"
                    else:
                        dateline = link.find_parent(class_="eventlist__event").parent.select_one("time")
                        fmt = "%m/%d/%Y"
                    if dateline is None:
                        continue
                    try:
                        day = datetime.strptime(clean(dateline), fmt).date()
                        url = gdelt.canonicalize_url(urljoin(index, link["href"]))
                    except (KeyError, ValueError):
                        continue
                    if not in_window(event, day):
                        continue
                    title = clean(link)
                    if urlsplit(url).netloc != urlsplit(index).netloc:
                        continue
                    path = urlsplit(url).path
                    if (source == "eia" and not path.lower().endswith("/detail.php")
                            or source == "fed" and not path.startswith("/newsevents/speech/")):
                        continue
                    if source == "eia":
                        url = EIA + "/todayinenergy/detail.php?" + urlsplit(url).query
                    matches.append((bool(re.search(pattern, title, re.I)), Candidate(source, event, url, title, day, index)))
                # Keep a small same-window control per index, separate from topical matches.
                for matching in (True, False):
                    group = [candidate for matched, candidate in matches if matched == matching]
                    cap = 8 if matching else (1 if year == last_year else 0)
                    for candidate in group[:cap]:
                        candidates[candidate.url] = candidate
        if event in REPORTS:
            prefix, release, release_url = REPORTS[event]
            index = f"{FED}/publications/{prefix}-purpose.htm"
            try:
                page = markup(http.get(index))
            except (ValueError, requests.RequestException) as exc:
                errors.append({"url": index, "error": str(exc)})
                continue
            for suffix in REPORT_SECTIONS:
                url = f"{FED}/publications/{prefix}-{suffix}.htm"
                if any(urljoin(index, a["href"]) == url for a in page.select("a[href]")):
                    candidates[url] = Candidate("fed", event, url, None, date.fromisoformat(release), index, release_url)
    for event, article_id, day in SEEDS:
        url = f"{EIA}/todayinenergy/detail.php?id={article_id}"
        candidates.setdefault(url, Candidate("eia", event, url, None, date.fromisoformat(day), url))
    return list(candidates.values())


def document(candidate: Candidate, payload: bytes) -> dict:
    page = markup(payload)
    is_eia = candidate.source == "eia"
    body = page.select_one(".tie-article" if is_eia else "#article")
    if body is None:
        raise ValueError("missing institutional article body")
    heading = body.select_one("h1" if is_eia else "h3")
    dateline = body.select_one(".date" if is_eia else ".article__time")
    if heading is None or not clean(heading):
        raise ValueError("missing article title")
    if dateline is None and "/publications/" not in candidate.url:
        raise ValueError("missing article dateline")
    if dateline is not None and datetime.strptime(clean(dateline), "%B %d, %Y").date() != candidate.day:
        raise ValueError("index and article publication dates disagree")
    publication_evidence = clean(dateline) if dateline else candidate.release_url
    for element in body.select("figure, table, blockquote, .source, .figure, .footnote, .footnotes, .article__time, .speaker, .location, .tags"):
        element.decompose()
    paragraphs = []
    for p in body.select("p"):
        value = clean(p)
        if len(value) < 65 or re.match(r"(Source|Data source|Note|Return to text|Last update|Reposted|Principal contributor|Tags)\b", value, re.I):
            continue
        if EXTERNAL_PROSE.search(value):
            raise ValueError("possible third-party prose or copyright notice")
        paragraphs.append(value)
    if not paragraphs:
        raise ValueError("missing approved narrative")
    normalized = "\n\n".join(paragraphs)
    if "\ufffd" in normalized:
        raise ValueError("undecodable text")
    source_name, policy = SOURCES[candidate.source]
    return {
        **asdict(candidate), "title": clean(heading), "id": stable_id(candidate.url),
        "source_name": source_name, "retrieved_at": datetime.now(UTC).isoformat(),
        "type": "analysis" if is_eia else ("report_section" if dateline is None else "speech"),
        "publication_precision": "day", "publication_evidence": publication_evidence, "extractor_version": 2,
        "rights": {
            "basis": policy, "download": "institutional prose only", "storage": "institutional prose only",
            "embedding": True, "redistribution": "attribute agency; excluded assets and third-party material",
            "external_llm": "prohibited by pilot scope",
        },
        "sha256": sha256(normalized.encode()).hexdigest(), "text": normalized,
    }


def gkg_candidates(http: PublicHTTP, errors: list) -> list[dict]:
    records = {}
    with TemporaryDirectory(dir=ROOT) as work:
        for event, timestamp in SNAPSHOTS.items():
            path = Path(work) / f"{timestamp}.gkg.csv.zip"
            url = gdelt.ARCHIVE_ROOT + path.name
            try:
                path.write_bytes(http.get(url, archive=True))
                batch = gdelt.read_batch(
                    path, [], datetime.now(UTC), limit=8,
                    discover=lambda record: bool(re.search(MATCH[event], gdelt.url_terms(record), re.I)),
                )
            except (ValueError, OSError, BadZipFile, requests.RequestException) as exc:
                errors.append({"url": url, "error": str(exc)})
                continue
            for record in batch.records:
                records.setdefault(record.canonical_url, {
                    **asdict(record), "id": stable_id(record.canonical_url), "source_name": gdelt.SOURCE_NAME,
                    "type": "discovery_metadata", "title": None, "event": event, "archive_sha256": batch.archive_sha256,
                    "asset_associations": [
                        {"asset": ASSET_SOURCES[link.asset_id].symbol, "relation": link.relationship,
                         "evidence": link.evidence, "verified_in_body": False}
                        for link in associate(record)
                    ],
                    "publication_evidence": "unverified GKG tag" if record.publication_raw else "unknown",
                    "rights": {"basis": gdelt.TERMS_URL, "download": "GKG metadata only",
                               "storage": "metadata only", "embedding": False,
                               "redistribution": "attribute GDELT metadata; publisher body rights unverified",
                               "external_llm": "prohibited by pilot scope"},
                })
    return list(records.values())


def acquire() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    http = PublicHTTP()
    errors = []
    official = official_candidates(http, errors)
    metadata = gkg_candidates(http, errors)
    docs, hashes, rejected = [], set(), []
    for candidate in official:
        path = ROOT / f"{stable_id(candidate.url)}.json"
        try:
            record = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
            if record and record.get("extractor_version") == 2:
                if sha256(record["text"].encode()).hexdigest() != record["sha256"]:
                    raise ValueError("cached text hash mismatch")
                if record["rights"]["basis"] != SOURCES[candidate.source][1] or not record["rights"]["embedding"]:
                    raise ValueError("cached rights mismatch")
                if record["url"] != candidate.url or record["day"] != candidate.day.isoformat():
                    raise ValueError("cached identity or date mismatch")
            else:
                record = document(candidate, http.get(candidate.url))
                write_json(path, record)
        except (ValueError, requests.RequestException) as exc:
            errors.append({"url": candidate.url, "error": str(exc)})
            rejected.append({
                **asdict(candidate), "id": stable_id(candidate.url), "retrieved_at": None,
                "rights": {"basis": SOURCES[candidate.source][1], "storage": "metadata only", "embedding": False,
                           "external_llm": "prohibited by pilot scope"},
            })
            continue
        if record["sha256"] in hashes:
            continue
        hashes.add(record["sha256"])
        docs.append(record)
    for item in metadata + rejected:
        item["sha256"] = sha256(json.dumps(item, default=str, sort_keys=True).encode()).hexdigest()
    write_json(ROOT / "manifest.json", {
        "official_candidates": len(official), "gkg_candidates": len(metadata),
        "unique_urls_discovered": len({c.url for c in official} | {m["canonical_url"] for m in metadata}),
        "documents": [{key: value for key, value in doc.items() if key != "text"} for doc in docs],
        "metadata_only": metadata + rejected, "errors": errors,
    })
    print(f"Official candidates: {len(official)}. GKG links: {len(metadata)}. Eligible texts: {len(docs)}. Errors: {len(errors)}.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    acquire()
