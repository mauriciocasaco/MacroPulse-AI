"""M7.A offline inventory generator: public institutional discovery, no ingestion."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import UTC, date, datetime
from hashlib import sha256
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from time import monotonic, sleep
from urllib.parse import parse_qs, urljoin, urlsplit, urlunsplit
from zipfile import BadZipFile

import requests

from backend.news import gdelt
from backend.news.historical_corpus import SOURCES, clean, markup, stable_id

ROOT = Path(__file__).resolve().parents[1] / "docs" / "m7a"
ECB = "https://www.ecb.europa.eu"
EU = "https://ec.europa.eu/eurostat"
BIS = "https://www.bis.org"
ARCHIVE = EU + "/web/products-euro-indicators/"
BULLETINS = ECB + "/press/economic-bulletin/html/all_releases.en.html"
MONEY = ECB + "/press/stats/md/html/index.en.html"
POLICY = {
    "BCE": ECB + "/services/using-our-site/disclaimer/html/index.en.html",
    "Eurostat": EU + "/help/copyright-notice",
    "BIS": BIS + "/about/terms-conditions",
    "Reserva Federal (Junta)": SOURCES["fed"][1],
}
WINDOWS = {
    "covid": ("2020-02-01", "2020-12-31"),
    "ukraine": ("2022-02-24", "2024-02-29"),
    "genai": ("2023-09-01", "2026-09-30"),
}
TOPIC = {
    "covid": r"pandemic|covid|coronavirus|lockdown|containment measures",
    "ukraine": r"ukrain|russia.{0,15}(?:invasi|aggress|war)|war in europe",
    "genai": r"generative (?:ai|artificial intelligence)|gen[ -]?ai\b|chatgpt|large language model|AI boom",
}
FOCUS = {
    "covid": r"pandemic|covid|coronavirus|lockdown|containment|precautionary|pepp|pandemic emergency",
    "ukraine": r"ukrain|russia|energy prices|natural gas|energy shock|food inflation|terms.of.trade|exchange rate",
    "genai": r"artificial intelligence|generative|\bAI\b",
}
SIGNALS = {
    "EUR/USD": r"(?:against|vis.à.vis|versus) the US dollar|USD per euro|euro.dollar|EUR/USD",
    "M3": r"\bM3\b|broad money",
    "HICP": r"\bHICP\b|harmonised index of consumer prices",
    "PIB real trimestral": r"real GDP|quarter.on.quarter|quarterly GDP|GDP growth|seasonally adjusted GDP",
    "Tasa de depósito BCE": r"deposit facility(?: interest)? rate|interest rate on the deposit facility|deposit facility will (?:remain|be|increase|decrease)",
}
MECHANISMS = {
    "EUR/USD": "Canal externo: tipo de cambio frente al dólar, condiciones financieras o precios de importación.",
    "M3": "Canal monetario: contribuciones de depósitos, crédito y otras contrapartidas al dinero amplio.",
    "HICP": "Canal de precios: contribuciones de energía, alimentos, demanda u otros componentes al HICP.",
    "PIB real trimestral": "Canal de actividad: evolución de la producción o el gasto real y sus componentes.",
    "Tasa de depósito BCE": "Canal de política monetaria: nivel o ajuste de la facilidad de depósito y orientación de la política.",
}
SNAPSHOTS = {"covid": "20200420210000", "ukraine": "20220224180000", "genai": "20240110230000"}
# Fixed research scope. Per-source page, request, byte and time limits apply.
MAX_REQUESTS = 310
MAX_BYTES = {"text/html": 2**21, "application/pdf": 2**24, "application/zip": 2**24,
             "application/octet-stream": 2**24}
GKG_HOST = "data.gdeltproject.org"
EU_PAGING = "com_liferay_asset_publisher_web_portlet_AssetPublisherPortlet_INSTANCE_NBJ1YXaAuDiT"
EU_KEY = "_" + EU_PAGING


def checked_url(raw, *, archive=False):
    url = gdelt.canonicalize_url(raw)
    p = urlsplit(url)
    if p.scheme != "https" or p.netloc not in {
        "www.ecb.europa.eu", "ec.europa.eu", "www.bis.org", "www.federalreserve.gov", GKG_HOST,
    }:
        raise ValueError("URL fuera de los orígenes institucionales autorizados")
    if re.search(r"%(?:2f|5c|2e|25|00)", p.path, re.I) or set(p.path.split("/")) & {".", ".."}:
        raise ValueError("ruta codificada o relativa no permitida")
    if archive:
        if p.netloc != GKG_HOST or not re.fullmatch(r"/gdeltv2/\d{14}\.gkg\.csv\.zip", p.path) or p.query:
            raise ValueError("ruta GKG no permitida")
    elif p.netloc == GKG_HOST:
        raise ValueError("descarga GKG no habilitada")
    elif p.netloc == "ec.europa.eu":
        if not p.path.startswith(("/eurostat/",)):
            raise ValueError("ruta fuera de Eurostat")
        keys = set(parse_qs(p.query, keep_blank_values=True))
        allowed = {"t", "version"} if p.path.startswith("/eurostat/documents/") else {
            "p_p_id", "p_p_lifecycle", "p_p_state", "p_p_mode", "p_r_p_resetCur",
            EU_KEY + "_delta", EU_KEY + "_cur", EU_KEY + "_redirect",
        } if p.path.endswith("/products-euro-indicators/product") else set()
        if keys - allowed:
            raise ValueError("parámetro Eurostat fuera del alcance")
    elif p.netloc == "www.federalreserve.gov":
        if p.query or not re.fullmatch(r"/(?:disclaimer|newsevents/speech/[a-z0-9]+)\.htm", p.path):
            raise ValueError("ruta de la Reserva Federal fuera del alcance")
    elif p.query or not p.path.startswith(("/press/", "/services/using-our-site/", "/publications/", "/about/", "/speeches/")):
        raise ValueError("ruta institucional fuera del alcance")
    return url


def identity(url):
    p = urlsplit(url)
    # Eurostat document URLs include a Liferay cache buster, not a publication identifier.
    if p.netloc == "ec.europa.eu" and p.path.startswith("/eurostat/documents/"):
        return urlunsplit(p._replace(query=""))
    return url


class HTTP:
    def __init__(self, session=None):
        self.session = session or requests.Session()
        self.session.trust_env = False
        self.session.headers["User-Agent"] = "MarketPulse-AI/1.0 (M7.A historical research)"
        self.last = {}
        self.deferred = set()
        self.requests = 0

    def get(self, raw, kind="html"):
        archive = kind == "zip"
        url = checked_url(raw, archive=archive)
        origin = urlsplit(url).netloc
        redirects = []
        for _ in range(3):
            for attempt in range(2):
                if self.requests >= MAX_REQUESTS or origin in self.deferred:
                    raise ValueError("presupuesto agotado o servidor en pausa")
                sleep(max(0, 0.4 - (monotonic() - self.last.get(origin, 0))))
                start = self.last[origin] = monotonic()
                self.requests += 1
                try:
                    with self.session.get(url, stream=True, timeout=(5, 9), allow_redirects=False) as response:
                        code = response.status_code
                        if code in (301, 302, 303, 307, 308):
                            location = response.headers.get("Location")
                            if not location:
                                raise ValueError("redirección sin destino")
                            destination = checked_url(urljoin(url, location), archive=archive)
                            if urlsplit(destination).netloc != origin:
                                raise ValueError("redirección fuera del origen")
                            redirects.append(destination)
                            url = destination
                            break
                        if code in (429, 503):
                            self.deferred.add(origin)
                        if code != 200:
                            raise ValueError(f"HTTP {code}")
                        mime = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
                        expected = {
                            "html": {"text/html"}, "pdf": {"application/pdf"},
                            "zip": {"application/zip", "application/octet-stream"},
                        }[kind]
                        if mime not in expected:
                            raise ValueError("tipo de contenido inesperado")
                        buf = bytearray()
                        for chunk in response.iter_content(65536):
                            buf.extend(chunk)
                            if monotonic() - start > 30 or len(buf) > MAX_BYTES[mime]:
                                raise ValueError("límite de descarga excedido")
                        if kind == "pdf" and not buf.startswith(b"%PDF-") or kind == "zip" and not buf.startswith(b"PK\x03\x04"):
                            raise ValueError("firma de archivo incorrecta")
                        return bytes(buf), url, mime, redirects
                except (requests.Timeout, requests.ConnectionError):
                    if attempt:
                        raise ValueError("conexión o timeout tras dos intentos") from None
                    sleep(1)
        raise ValueError("límite de redirecciones excedido")


def dateline(value):
    # Avoid platform locale when parsing publisher dates in English.
    value = value.strip().replace("‑", "-")
    if re.fullmatch(r"\d{2}-\d{2}-\d{4}", value):
        d, m, y = map(int, value.split("-"))
        return date(y, m, d).isoformat()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return date.fromisoformat(value).isoformat()
    if match := re.fullmatch(r"([A-Za-z]+) (\d{1,2}), (\d{4})", value):
        m, d, y = match.groups()
    else:
        d, m, y = value.split()
    months = "January February March April May June July August September October November December".split()
    return date(int(y), months.index(m) + 1, int(d)).isoformat()


def in_window(event, day):
    first, last = WINDOWS[event]
    return bool(day and first <= day <= last)


def ecb_entries(page, index, rejected, events, title_pattern):
    for a in page.select("dd .title > a[href]"):
        dt = a.find_parent("dd").find_previous_sibling("dt")
        title = clean(a)
        if dt and dt.get("isodate") and re.search(title_pattern, title, re.I):
            try:
                day = dateline(dt["isodate"])
                if not any(in_window(event, day) for event in events):
                    continue
                url = checked_url(urljoin(index, a["href"]))
                if urlsplit(url).netloc != urlsplit(index).netloc:
                    raise ValueError("enlace fuera del origen del índice")
            except ValueError as exc:
                rejected.append({"url": index, "reason": f"entrada de índice descartada: {exc}"})
                continue
            yield url, title, day


def evidence(paragraphs, event, kind):
    """Indicator associations are conservative rules, not causal assertions."""
    matches = {}
    for key, regex in SIGNALS.items():
        hits = [p for p in paragraphs if re.search(regex, p, re.I)]
        if not hits:
            continue
        topical = any(re.search(TOPIC[event], p, re.I) for p in hits)
        if event == "genai":
            # Generic inflation, interest rates and FX do not establish a link to official series.
            if key not in {"PIB real trimestral", "HICP"} or not topical:
                continue
            relation = "proxy_o_escenario"
        else:
            relation = "mismo_pasaje" if topical else "contexto"
        if kind == "m3" and key != "M3":
            continue
        matches[key] = relation
    return matches


def terms_for(institution, kind, page, verified):
    source = POLICY[institution]
    text = clean(page) if page else ""
    if institution == "BCE":
        if kind in {"bulletin", "focus", "analysis"} or re.search(
            r"\b(?:prepared by|byline|all rights reserved)\b", text, re.I
        ) or (page and page.select_one('meta[name="citation_author"], .ecb-authors')):
            return "revisión de autoría o condiciones de la publicación", source, "pendiente"
        if verified:
            return "posible uso de información institucional con atribución y exactitud", source, "política de sitio comprobada; alcance RAG pendiente"
    if institution == "Eurostat" and verified:
        return "CC BY 4.0 para contenido editorial de la UE; excluir terceros y avisos específicos", source, "política de sitio comprobada; alcance por documento pendiente"
    if institution == "Reserva Federal (Junta)" and verified:
        return "posible dominio público para prosa de la Junta; excluir materiales de terceros", source, "política de sitio comprobada; alcance por documento pendiente"
    return "derechos del documento pendientes; conservar solo metadatos", source, "pendiente"


def select_proposal(rows):
    """Stable balanced shortlist; all selections remain conditional."""
    chosen = []
    for event, limit in (("covid", 21), ("ukraine", 23), ("genai", 16)):
        pool = [r for r in rows if r["event"] == event and r["status"] == "accessible"]
        remaining = sorted(pool, key=lambda r: (-r["relevance"], not potentially_reusable(r),
                                                r["publication_date"], r["id"]))
        selected = []
        def overlaps(row):
            return any(r["parent_url"] == row["canonical_url"]
                       or row["parent_url"] == r["canonical_url"] for r in selected)

        # Cover available indicators first, preferring materially relevant and reusable sources.
        for key in SIGNALS:
            if any(key in row["indicators"] for row in selected):
                continue
            match = next((r for r in remaining if key in r["indicators"] and not overlaps(r)), None)
            if match:
                selected.append(match)
                remaining.remove(match)
        # Prefer independent pieces to whole issues that contain the same material.
        counts = Counter(r["kind"] for r in selected)
        for row in remaining:
            kind = row["kind"]
            if len(selected) == limit:
                break
            if overlaps(row):
                continue
            if counts[kind] >= {"m3": 5, "decision": 6, "bulletin": 5}.get(kind, limit):
                continue
            selected.append(row)
            counts[kind] += 1
        chosen.extend(selected)
    return chosen


def potentially_reusable(row):
    return row["rights"]["status"].startswith("política de sitio comprobada")


class Discovery:
    def __init__(self, http):
        self.http = http
        self.checked_at = datetime.now(UTC).isoformat(timespec="seconds")
        self.rows = {}
        self.by_hash = {}
        self.rejected = []
        self.policies = {}
        self.indexes = {}

    def fetch(self, url, kind="html"):
        try:
            return self.http.get(url, kind)
        except (ValueError, requests.RequestException) as exc:
            # Do not persist exception strings containing URL or request headers from requests.
            self.rejected.append({"url": url, "reason": str(exc) if isinstance(exc, ValueError) else type(exc).__name__})
            return None

    def page(self, url):
        result = self.fetch(url)
        if result:
            self.indexes[url] = sha256(result[0]).hexdigest()
            return markup(result[0])
        return None

    def add(self, event, url, title, day, source, kind, index, *, parent=None, editorial=None, payload=None,
            fetch=True):
        try:
            canonical = identity(checked_url(url))
        except ValueError as exc:
            self.rejected.append({"url": index, "reason": f"enlace descartado: {exc}"})
            return
        if canonical in self.rows:
            return
        institution = source
        result = (payload or self.fetch(url, "pdf" if kind == "stat_pdf" else "html")) if fetch else None
        if not result and (not title or not in_window(event, day)):
            return
        row = {
            "id": stable_id(canonical), "title": title, "institution": institution,
            "original_url": url, "canonical_url": canonical, "publication_date": day, "event": event,
            "indicators": {}, "mechanism": None, "language": "en",
            "rights": {"status": "pendiente", "reuse": "solo metadatos", "license_url": POLICY[source]},
            "status": "discovered", "relevance": 0, "content_sha256": None, "kind": kind,
            "discovered_from": index, "parent_url": parent, "verified_at": None,
            "publication_evidence": {"index_date": day}, "access": None, "approval": "pending",
        }
        if not result:
            self.rows[canonical] = row
            return
        body, final, mime, redirects = result
        canonical = identity(final)
        if canonical in self.rows:
            self.rejected.append({"url": url, "reason": "URL final duplicada", "duplicate_of": canonical})
            return
        row.update(id=stable_id(canonical), canonical_url=canonical)
        try:
            page = markup(body) if mime == "text/html" else None
            heading = None
            paragraphs = []
            if page:
                is_fed = source == "Reserva Federal (Junta)"
                main = page.select_one("#article" if is_fed else "main") or (page if source not in {"BCE", "Reserva Federal (Junta)"} else None)
                heading = main.select_one("h3" if is_fed else "h1") if main else None
                if not heading or not clean(heading) or not str(page.html.get("lang", "") if page.html else "").lower().startswith("en"):
                    raise ValueError("documento en inglés sin título o contenido esperado")
                row["title"] = clean(heading)
                meta = page.select_one('meta[name="citation_online_date"], meta[name="citation_publication_date"]')
                raw_day = meta.get("content") if meta else None
                if is_fed and (date_node := main.select_one(".article__time")):
                    raw_day = clean(date_node)
                if not raw_day and source in {"Eurostat", "BIS"}:
                    context = clean(heading.parent.parent) if source == "Eurostat" else clean(main)
                    match = re.search(r"\b\d{1,2} (?:January|February|March|April|May|June|July|August|September|October|November|December) \d{4}\b", context)
                    raw_day = match[0] if match else None
                if raw_day:
                    page_day = dateline(raw_day)
                    row["publication_evidence"]["page_date"] = page_day
                    if day and page_day != day and not parent:
                        raise ValueError("fechas del índice y del documento no coinciden")
                    # Box metadata may predate publication of the containing issue.
                    row["publication_date"] = max(page_day, day or page_day)
                elif not day:
                    raise ValueError("fecha editorial no localizada")
                paragraphs = [clean(p) for p in main.select("p") if len(clean(p)) >= 35]
                if not paragraphs:
                    raise ValueError("prosa institucional no localizada")
                digest = sha256("\n".join(paragraphs).encode()).hexdigest()
                hash_type = "normalized_paragraphs_utf8"
            else:
                digest = sha256(body).hexdigest()
                hash_type = "pdf_bytes"
            if not in_window(event, row["publication_date"]):
                raise ValueError("publicación fuera de ventana")
            if digest in self.by_hash:
                self.rejected.append({"url": url, "reason": "contenido duplicado", "duplicate_of": self.by_hash[digest]})
                return
            indicators = evidence(paragraphs, event, kind)
            topical = any(re.search(TOPIC[event], p, re.I) for p in paragraphs)
            if editorial:
                indicators = editorial["indicators"]
                mechanism = editorial["mechanism"]
                relevance = editorial["relevance"]
                row["evidence_locator"] = editorial["evidence_locator"]
                if mime == "application/pdf":
                    row["publication_evidence"] = {"reviewed_pdf_dateline": day}
            elif kind == "stat_pdf":
                key = "HICP" if "inflation" in title.lower() else "PIB real trimestral"
                indicators = {key: "contexto"}
                mechanism = "Publicación estadística contemporánea: mide precios o producción; no identifica el efecto del evento."
                relevance = 2
            else:
                if not indicators:
                    raise ValueError("sin vínculo comprobable con los indicadores")
                if kind in {"focus", "bulletin"} and not topical:
                    raise ValueError("sin evidencia temática en la prosa")
                mechanism = " ".join(MECHANISMS[key] for key in indicators)
                if kind in {"m3", "decision"} and not topical:
                    mechanism += " Registro contemporáneo; no atribuye el cambio al evento."
                if event == "genai":
                    mechanism += " La evidencia sobre productividad o inflación es un escenario, no una medición del efecto trimestral."
                relevance = 3 if topical and kind in {"focus", "bulletin", "decision"} else 2
                row["evidence_locator"] = {
                    key: [i for i, paragraph in enumerate(paragraphs, 1)
                          if re.search(SIGNALS[key], paragraph, re.I)
                          and (relation != "mismo_pasaje" or re.search(TOPIC[event], paragraph, re.I))][:4]
                    for key, relation in indicators.items()
                }
            verified = self.policies.get(source, False)
            reuse, license_url, rights_status = terms_for(source, kind, page, verified)
            row.update(
                status="accessible", verified_at=self.checked_at, indicators=indicators, mechanism=mechanism,
                relevance=relevance, content_sha256=digest, content_hash_scope=hash_type,
                rights={"status": rights_status, "reuse": reuse, "license_url": license_url},
                access={"final_url": final, "media_type": mime, "redirects": redirects},
            )
        except (ValueError, AttributeError) as exc:
            self.rejected.append({"url": url, "reason": str(exc) if isinstance(exc, ValueError) else "estructura editorial inesperada"})
            return
        self.by_hash[digest] = canonical
        self.rows[canonical] = row

    def ecb(self):
        bulletin = self.page(BULLETINS)
        for url, title, day in ecb_entries(bulletin, BULLETINS, self.rejected, WINDOWS,
                                           r"^Economic Bulletin Issue") if bulletin else ():
            events = [e for e in WINDOWS if in_window(e, day)]
            if not events:
                continue
            result = self.fetch(url)
            if not result:
                for event in events:
                    if event != "genai":
                        self.add(event, url, title, day, "BCE", "bulletin", BULLETINS, fetch=False)
                continue
            page = markup(result[0])
            issue = re.search(r"eb(\d{6})\.en", url)
            if not issue:
                continue
            for event in events:
                if event != "genai":
                    self.add(event, url, title, day, "BCE", "bulletin", BULLETINS, payload=result)
                for link in page.select("main a[href]"):
                    ref = link["href"]
                    if not re.search(rf"ecb\.eb(?:box|art){issue[1]}_\d+~[a-f0-9]+\.en\.html$", ref):
                        continue
                    if clean(link) != "More":
                        continue
                    heading = link.find_previous(["h2", "h3"])
                    if heading and re.search(FOCUS[event], clean(heading), re.I):
                        self.add(event, urljoin(url, ref), clean(heading), day, "BCE", "focus", url, parent=url)
        for year in (2020, 2022, 2023, 2024):
            event = "covid" if year == 2020 else "ukraine"
            index = f"{ECB}/press/pr/date/{year}/html/index_include.en.html"
            page = self.page(index)
            pattern = r"monetary policy decisions|pandemic emergency purchase programme|global US dollar liquidity"
            for url, title, day in ecb_entries(page, index, self.rejected, (event,), pattern) if page else ():
                self.add(event, url, title, day, "BCE", "decision", index)
        page = self.page(MONEY)
        events = ("covid", "ukraine")
        for url, title, day in ecb_entries(page, MONEY, self.rejected, events,
                                           r"^Monetary developments in the euro area:") if page else ():
            event = next(event for event in events if in_window(event, day))
            self.add(event, url, title, day, "BCE", "m3", MONEY)

    def eurostat(self):
        # Follow only linked pages in the legacy collection. Curated releases
        # remain independent of pagination availability.
        index = ARCHIVE
        seen = set()
        for number in range(1, 14):
            if index in seen:
                break
            seen.add(index)
            page = self.page(index)
            if not page:
                break
            dates = []
            for item in page.select(".product-item"):
                link = item.select_one(".product-title a[href]")
                dat = item.select_one(".product-date")
                if not link or not dat:
                    continue
                try:
                    day = dateline(clean(dat))
                except ValueError:
                    self.rejected.append({"url": index, "reason": "fecha de archivo Eurostat no reconocida"})
                    continue
                dates.append(day)
                title = clean(link)
                if not re.search(r"(?:^Euro area annual inflation|^GDP (?:up|down|stable|unchanged))", title, re.I):
                    continue
                # One deterministic sample per release month/type, to avoid redundant estimates.
                key = "inflation" if "inflation" in title.lower() else "GDP"
                for event in ("covid", "ukraine"):
                    if not in_window(event, day):
                        continue
                    if any(r["institution"] == "Eurostat" and r["publication_date"][:7] == day[:7]
                           and (("inflation" in r["title"].lower()) == (key == "inflation"))
                           for r in self.rows.values()):
                        continue
                    if day[5:7] not in {"03", "05", "06", "08", "09", "11", "12"}:
                        continue
                    self.add(event, link["href"], title, day, "Eurostat", "stat_pdf", index)
            if not dates or min(dates) < WINDOWS["covid"][0]:
                break
            next_page = page.select_one(f'a[href*="{EU_KEY}_cur={number + 1}"][aria-label="Page {number + 1}"]')
            if not next_page:
                # Older archive templates provide a title rather than aria-label.
                next_page = next((a for a in page.select("a[href]")
                                  if clean(a) == f"Page {number + 1}"), None)
            if not next_page:
                self.rejected.append({"url": index, "reason": "página siguiente de Eurostat no localizada"})
                break
            index = urljoin(index, next_page["href"])

    def gkg(self):
        rows = {}
        with TemporaryDirectory() as temp:
            for event, stamp in SNAPSHOTS.items():
                path = Path(temp) / f"{stamp}.gkg.csv.zip"
                result = self.fetch(gdelt.ARCHIVE_ROOT + path.name, "zip")
                if not result:
                    continue
                path.write_bytes(result[0])
                try:
                    batch = gdelt.read_batch(
                        path, [], datetime.fromisoformat(self.checked_at), limit=8,
                        discover=lambda r: bool(re.search(TOPIC[event], gdelt.url_terms(r), re.I)),
                    )
                except (ValueError, OSError, BadZipFile) as exc:
                    self.rejected.append({"url": result[1], "reason": type(exc).__name__})
                    continue
                for r in batch.records:
                    rows.setdefault(r.canonical_url, {
                        "id": stable_id(r.canonical_url), "title": None, "institution": r.publisher,
                        "original_url": r.source_url, "canonical_url": r.canonical_url,
                        "publication_date": (r.published_at or r.published_on).isoformat()
                        if r.published_at or r.published_on else None,
                        "event": event, "indicators": {}, "mechanism": None, "language": None,
                        "rights": {"status": "publisher_unverified", "reuse": "GKG metadata only", "license_url": gdelt.TERMS_URL},
                        "status": "discovered", "approval": "pending", "relevance": 0, "content_sha256": None,
                        "discovered_from": r.provenance_ref, "archive_sha256": batch.archive_sha256,
                        "observed_at": r.observed_at.isoformat(), "retrieved_at": r.retrieved_at.isoformat(),
                        "publication_evidence": "GKG no verificado",
                    })
        return rows.values()


def write_lines(path, rows):
    with path.open("w", encoding="utf-8", newline="\n") as fp:
        for row in rows:
            fp.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def run(out, include_gkg):
    discovery = Discovery(HTTP())
    for key, pattern in {
        "BCE": r"may make free use.{0,1500}explicit prior written authorisation",
        "Eurostat": r"Creative Commons Attribution 4\.0",
        "BIS": r"copyright",
        "Reserva Federal (Junta)": r"public domain",
    }.items():
        page = discovery.page(POLICY[key])
        discovery.policies[key] = bool(page and re.search(pattern, clean(page), re.I))
    seeds = json.loads((ROOT / "research-seeds.json").read_text(encoding="utf-8"))
    for seed in seeds:
        discovery.add(
            seed["event"], seed["url"], seed.get("title"), seed.get("publication_date"),
            seed["institution"], seed.get("kind", "analysis"), seed.get("discovered_from", seed["url"]), editorial=seed,
            parent=seed.get("parent_url"),
        )
    discovery.ecb()
    discovery.eurostat()
    gkg = list(discovery.gkg()) if include_gkg else []
    rows = sorted(discovery.rows.values(), key=lambda r: (r["event"], r["publication_date"], r["id"]))
    accessible = [r for r in rows if r["status"] == "accessible"]
    proposal = select_proposal(rows)
    out.mkdir(parents=True, exist_ok=True)
    write_lines(out / "candidates.jsonl", rows)
    write_lines(out / "gdelt-metadata.jsonl", sorted(gkg, key=lambda r: r["id"]))
    write_lines(out / "rejected.jsonl", discovery.rejected)
    summary = {
        "checked_at": discovery.checked_at, "requests": discovery.http.requests, "windows": WINDOWS,
        "policies_checked": discovery.policies, "index_sha256": discovery.indexes,
        "candidates": len(rows), "accessible": len(accessible), "approved": [],
        "accessible_ids": [r["id"] for r in accessible],
        "proposal_ids": [r["id"] for r in proposal],
        "discovered_only_ids": [r["id"] for r in rows if r["status"] == "discovered"],
        "gkg_metadata_only": len(gkg), "rejected": len(discovery.rejected),
    }
    (out / "manifest.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(rows)} candidates; {len(accessible)} accessible; {len(proposal)} proposed; {len(gkg)} GKG metadata.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--with-gkg", action="store_true")
    args = parser.parse_args()
    run(args.out, args.with_gkg)
