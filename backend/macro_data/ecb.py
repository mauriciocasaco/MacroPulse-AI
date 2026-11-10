"""ECB SDMX CSV adapter for the three approved ECB series."""

from __future__ import annotations

import csv
from datetime import date, datetime
from io import StringIO
from urllib.parse import urlencode
from xml.etree import ElementTree

from .contracts import Indicator, Observation, format_period, parse_period, parse_value
from .transport import Response


ECB_API = "https://data-api.ecb.europa.eu/service/data"
# The format parameter selects the representation. An explicit text/csv
# Accept produced upstream timeouts for some otherwise valid ECB requests.
ACCEPT = "*/*"
HISTORY_ACCEPT = ACCEPT
_HISTORY_CONTENT_TYPE = "application/vnd.sdmx.genericdata+xml"
_MESSAGE = "{http://www.sdmx.org/resources/sdmxml/schemas/v2_1/message}"
_GENERIC = "{http://www.sdmx.org/resources/sdmxml/schemas/v2_1/data/generic}"
UNITS = {"EXR": "USD", "BSI": "EUR", "FM": "PCPA"}
STATUSES = {"A": "normal", "P": "provisional", "E": "estimated", "F": "forecast"}


def request_url(indicator: Indicator, start: date, end: date, *, history: bool = False) -> str:
    params = {
        "startPeriod": format_period(indicator.frequency, start),
        "endPeriod": format_period(indicator.frequency, end),
    }
    if history:
        params["includeHistory"] = "true"
    params["format"] = "genericdata" if history else "csvdata"
    key = ".".join(value for _, value in indicator.dimensions)
    return f"{ECB_API}/{indicator.dataset}/{key}?{urlencode(params)}"


def parse(indicator: Indicator, response: Response) -> tuple[tuple[Observation, ...], dict]:
    if response.content_type not in {"text/csv", "application/vnd.sdmx.data+csv"}:
        raise ValueError("unexpected ECB content type")
    if not response.body.strip() and indicator.frequency == "effective_date":
        # The exact ECB DFR route returns an empty CSV body for no-event windows.
        return (), {}
    reader = csv.DictReader(StringIO(response.body.decode("utf-8-sig"), newline=""), strict=True)
    headers = reader.fieldnames or []
    expected = dict(indicator.dimensions)
    required = {"KEY", "TIME_PERIOD", "OBS_VALUE", "OBS_STATUS", "OBS_CONF", "UNIT", "UNIT_MULT"}
    if not required.union(expected).issubset(headers) or len(set(headers)) != len(headers):
        raise ValueError("unexpected ECB CSV header")
    observations = []
    for row in reader:
        if (None in row or None in row.values() or row["KEY"] != indicator.series_key
                or any(row[name] != value for name, value in expected.items())
                or row["UNIT"] != UNITS[indicator.dataset] or row["UNIT_MULT"] != str(indicator.multiplier)):
            raise ValueError("unexpected ECB series or units")
        raw = row["OBS_VALUE"] or None
        status = row["OBS_STATUS"] or None
        # Keep all the returned source attributes, including FX's inconsistent
        # index label, without applying those metadata as a transformation.
        attributes = {name: value for name, value in row.items() if value and name not in {
            "KEY", "TIME_PERIOD", "OBS_VALUE", "OBS_STATUS", "OBS_CONF",
        }}
        observations.append(Observation(
            parse_period(indicator.frequency, row["TIME_PERIOD"]),
            parse_value(raw) if raw is not None else None, raw, status,
            STATUSES.get(status, "unknown") if raw is not None else "missing",
            row["OBS_CONF"] or None, attributes,
        ))
    if not observations and indicator.frequency != "effective_date":
        raise ValueError("ECB response has no series observations")
    return tuple(observations), {}


def _attributes(node, name: str) -> dict[str, str]:
    values = node.findall(f"{_GENERIC}{name}/{_GENERIC}Value")
    result = {item.attrib["id"]: item.attrib["value"] for item in values}
    if len(result) != len(values):
        raise ValueError("duplicate ECB version attribute")
    return result


def _version_time(raw: str) -> datetime:
    stamp = datetime.fromisoformat(raw)
    if stamp.utcoffset() is None:
        raise ValueError("ECB validity timestamp lacks a timezone")
    return stamp


def parse_history(indicator: Indicator, response: Response) -> tuple[tuple[Observation, ...], dict]:
    if response.content_type not in {_HISTORY_CONTENT_TYPE, "application/xml", "text/xml"}:
        raise ValueError("unexpected ECB historical content type")
    xml = response.body.decode("utf-8-sig")
    if "<!DOCTYPE" in xml.upper() or "<!ENTITY" in xml.upper():
        raise ValueError("unexpected ECB document declaration")
    root = ElementTree.fromstring(xml)
    sender = root.find(f"{_MESSAGE}Header/{_MESSAGE}Sender")
    if root.tag != f"{_MESSAGE}GenericData" or sender is None or sender.get("id") != "ECB":
        raise ValueError("unexpected ECB historical data")
    observations = []
    for dataset in root.findall(f"{_MESSAGE}DataSet"):
        action = dataset.attrib["action"]
        valid_from = _version_time(dataset.attrib["validFromDate"])
        raw_valid_to = dataset.get("validToDate")
        valid_to = _version_time(raw_valid_to) if raw_valid_to else None
        if action not in {"Replace", "Append", "Delete"} or (valid_to is not None and valid_to <= valid_from):
            raise ValueError("unexpected ECB historical validity")
        series_rows = dataset.findall(f"{_GENERIC}Series")
        if not series_rows:
            raise ValueError("ECB history requires explicit series and periods")
        for series in series_rows:
            dimensions = _attributes(series, "SeriesKey")
            attributes = _attributes(series, "Attributes")
            if dimensions != dict(indicator.dimensions):
                raise ValueError("unexpected ECB historical series")
            if action != "Delete" and (attributes.get("UNIT") != UNITS[indicator.dataset]
                                      or attributes.get("UNIT_MULT") != str(indicator.multiplier)):
                raise ValueError("unexpected ECB historical units")
            versions = series.findall(f"{_GENERIC}Obs")
            if not versions:
                raise ValueError("ECB history requires explicit observation periods")
            for version in versions:
                period = version.find(f"{_GENERIC}ObsDimension").attrib["value"]
                value = version.find(f"{_GENERIC}ObsValue")
                raw = value.attrib["value"] if value is not None else None
                details = {**dimensions, **attributes, **_attributes(version, "Attributes")}
                status = details.pop("OBS_STATUS", None)
                confidentiality = details.pop("OBS_CONF", None)
                if action == "Delete":
                    if raw is not None:
                        raise ValueError("ECB withdrawal unexpectedly has a value")
                    kind = "withdrawn"
                else:
                    kind = STATUSES.get(status, "unknown") if raw is not None else "missing"
                observations.append(Observation(
                    parse_period(indicator.frequency, period), parse_value(raw) if raw is not None else None,
                    raw, status, kind, confidentiality, details, valid_from, valid_to, action,
                ))
    if not observations and indicator.frequency != "effective_date":
        raise ValueError("ECB response has no historical observations")
    return tuple(observations), {"version_scope": "provider_repository"}
