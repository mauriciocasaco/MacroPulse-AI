"""Synthetic fixtures only: no network, corpus runtime or database."""
import json

import pytest

from scripts import m7a_discovery as m7a


URL = m7a.ECB + "/press/pr/date/2020/html/ecb.synthetic.en.html"
DATE = "2020-06-01"


class Response:
    def __init__(self, code=200, headers=None, data=b""):
        self.status_code = code
        self.headers = headers or {}
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def iter_content(self, _):
        yield self.data


class Session:
    def __init__(self, *responses):
        self.headers = {}
        self.responses = iter(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return next(self.responses)


@pytest.mark.parametrize("url", [
    "http://www.ecb.europa.eu/press/a",
    "https://www.ecb.europa.eu.evil.test/press/a",
    "https://test@www.ecb.europa.eu/press/a",
    "https://www.ecb.europa.eu:8443/press/a",
    "https://ec.europa.eu/another-service/a",
    "https://ec.europa.eu/eurostat/%2e%2e/private",
    "https://ec.europa.eu/eurostat/web/a?token=x",
    "https://data.gdeltproject.org/gdeltv2/20200420210000.gkg.csv.zip",
])
def test_reject_unsafe_origins_paths_or_credentials(url):
    with pytest.raises(ValueError):
        m7a.checked_url(url)


def test_redirect_checked_before_any_second_request():
    session = Session(Response(302, {"Location": "https://127.0.0.1/private"}))
    with pytest.raises(ValueError):
        m7a.HTTP(session).get(URL)
    assert [x[0] for x in session.calls] == [URL]
    assert session.calls[0][1]["allow_redirects"] is False
    assert session.trust_env is False


def test_allowed_redirect_and_rate_limit(monkeypatch):
    monkeypatch.setattr(m7a, "sleep", lambda _: None)
    session = Session(Response(301, {"Location": "/press/replaced.en.html"}),
                      Response(200, {"Content-Type": "text/html; charset=UTF-8"}, b"<main/>"),
                      Response(429))
    http = m7a.HTTP(session)
    assert http.get(URL)[1:] == (m7a.ECB + "/press/replaced.en.html", "text/html",
                                [m7a.ECB + "/press/replaced.en.html"])
    with pytest.raises(ValueError, match="HTTP 429"):
        http.get(URL)
    with pytest.raises(ValueError, match="pausa"):
        http.get(URL)
    assert len(session.calls) == 3


@pytest.mark.parametrize(("mime", "payload"), [
    ("text/html", b"not a PDF"),
    ("application/pdf", b"<html>error page</html>"),
])
def test_pdf_requires_media_type_and_signature(mime, payload):
    session = Session(Response(200, {"Content-Type": mime}, payload))
    with pytest.raises(ValueError):
        m7a.HTTP(session).get(URL, "pdf")


def page(text="Pandemic precautionary deposits lifted broad money M3.", date="1 June 2020"):
    return (f'<html lang="en"><head><meta name="citation_online_date" content="{date}">'
            f'</head><main><h1>Synthetic release</h1><p>{text}</p></main></html>').encode()


def add(discovery, url=URL, payload=None, **kwargs):
    discovery.add("covid", url, "Synthetic release", DATE, "BCE", "m3",
                  m7a.MONEY, payload=payload or (page(), url, "text/html", []), **kwargs)


def test_discovery_deduplicates_payload_and_keeps_metadata_only(tmp_path):
    discovery = m7a.Discovery(None)
    discovery.policies["BCE"] = True
    add(discovery)
    add(discovery, URL.replace("synthetic", "copy"))
    assert len(discovery.rows) == 1
    row = discovery.rows[URL]
    assert row["status"] == "accessible" and row["approval"] == "pending"
    assert row["indicators"] == {"M3": "mismo_pasaje"}
    assert row["publication_evidence"] == {"index_date": DATE, "page_date": DATE}
    assert discovery.rejected[0]["duplicate_of"] == URL
    output = tmp_path / "test.jsonl"
    m7a.write_lines(output, [row])
    assert json.loads(output.read_text(encoding="utf-8")) == row
    assert "Pandemic precautionary deposits" not in output.read_text(encoding="utf-8")


def test_different_archive_and_article_dates_are_not_silently_accepted():
    discovery = m7a.Discovery(None)
    add(discovery, payload=(page(date="2 June 2020"), URL, "text/html", []))
    assert not discovery.rows
    assert "no coinciden" in discovery.rejected[0]["reason"]


def test_archive_only_and_policy_evidence_are_independent():
    discovery = m7a.Discovery(None)
    discovery.policies["BCE"] = True
    discovery.add("covid", URL, "Known in archive", DATE, "BCE", "m3", m7a.MONEY, fetch=False)
    row = discovery.rows[URL]
    assert row["status"] == "discovered"
    assert row["content_sha256"] is None
    assert row["rights"]["status"] == "pendiente"
    assert not m7a.select_proposal([row])
    authored = m7a.markup(page("Prepared by Author. Pandemic precautionary deposits lifted M3."))
    assert m7a.terms_for("BCE", "m3", authored, True)[2] == "pendiente"


def test_windows_and_no_spurious_genai_m3_link():
    assert not m7a.in_window("ukraine", "2022-02-23")
    assert m7a.in_window("ukraine", "2022-02-24")
    assert m7a.in_window("genai", "2026-09-30")
    assert not m7a.in_window("genai", "2026-10-01")
    assert not m7a.evidence(["Generative AI was discussed. M3 and US dollar quotes appeared."], "genai", "focus")


def test_eurostat_pdf_identity_ignores_cache_version():
    url = m7a.EU + "/documents/1234/release.pdf/a-valid-id"
    assert m7a.identity(m7a.checked_url(url + "?t=123&version=2.0")) == url


def test_publisher_date_formats_and_invalid_calendar_date():
    for value in ("22 September 2023", "September 22, 2023", "22‑09‑2023", "2023-09-22"):
        assert m7a.dateline(value) == "2023-09-22"
    with pytest.raises(ValueError):
        m7a.dateline("29 February 2023")


def test_proposal_avoids_parent_issue_and_its_child():
    def row(url, kind, parent, rank, indicator):
        return {"id": url, "canonical_url": url, "parent_url": parent, "kind": kind,
                "event": "covid", "status": "accessible", "publication_date": DATE,
                "indicators": {indicator: "contexto"}, "rights": {"status": "pendiente"},
                "relevance": rank}
    box = row("box", "focus", "issue", 4, "EUR/USD")
    issue = row("issue", "bulletin", None, 3, "M3")
    money = row("money", "m3", None, 2, "M3")
    assert m7a.select_proposal([issue, money, box]) == [box, money]
