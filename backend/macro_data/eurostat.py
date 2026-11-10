"""Eurostat Statistics API JSON-stat 2.0 adapter for GDP and HICP."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
import json
from urllib.parse import urlencode

from .contracts import Indicator, Observation, format_period, parse_period, parse_value
from .transport import Response


EUROSTAT_API = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data"
ACCEPT = "application/json"
STATUSES = {"p": "provisional", "e": "estimated", "f": "forecast", "b": "break"}


def request_url(indicator: Indicator, start: date, end: date) -> str:
    params = urlencode({
        "lang": "en", **dict(indicator.dimensions),
        "sinceTimePeriod": format_period(indicator.frequency, start),
        "untilTimePeriod": format_period(indicator.frequency, end),
    })
    return f"{EUROSTAT_API}/{indicator.dataset}?{params}"


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate Eurostat JSON key")
        result[key] = value
    return result


def _positions(category: dict, size: int) -> tuple[str, ...]:
    index = category["index"]
    if isinstance(index, list):
        codes = tuple(index)
    elif isinstance(index, dict) and all(type(position) is int for position in index.values()):
        if set(index.values()) != set(range(size)):
            raise ValueError("invalid Eurostat dimension positions")
        codes = tuple(sorted(index, key=index.get))
    else:
        raise ValueError("invalid Eurostat dimension index")
    if len(codes) != size or not all(isinstance(code, str) for code in codes) or len(set(codes)) != size:
        raise ValueError("invalid Eurostat dimension size")
    return codes


def _cells(data, size: int) -> dict[int, object]:
    if data is None:
        return {}
    if isinstance(data, list) and len(data) == size:
        return dict(enumerate(data))
    if isinstance(data, dict):
        if any(not key.isascii() or not key.isdecimal() or len(key) > 5 for key in data):
            raise ValueError("invalid Eurostat cell position")
        result = {int(key): value for key, value in data.items()}
        if len(result) != len(data) or any(index >= size for index in result):
            raise ValueError("invalid Eurostat cell position")
        return result
    raise ValueError("invalid Eurostat cells")


def _reject_constant(_):
    raise ValueError("non-finite Eurostat value")


def parse(indicator: Indicator, response: Response) -> tuple[tuple[Observation, ...], dict]:
    if response.content_type not in {"application/json", "application/json-stat", "application/vnd.jsonstat+json"}:
        raise ValueError("unexpected Eurostat content type")
    payload = json.loads(response.body, parse_float=Decimal, parse_constant=_reject_constant,
                         object_pairs_hook=_unique_object)
    expected = dict(indicator.dimensions)
    if (payload.get("class") != "dataset" or payload.get("version") != "2.0" or payload.get("source") != "ESTAT"
            or payload["extension"].get("id", "").lower() != indicator.dataset):
        raise ValueError("unexpected Eurostat dataset")
    names, sizes, dimensions = payload["id"], payload["size"], payload["dimension"]
    if (set(names) != {*expected, "time"} or len(names) != len(expected) + 1
            or len(sizes) != len(names) or set(dimensions) != set(names)):
        raise ValueError("unexpected Eurostat dimensions")
    periods = ()
    for name, size in zip(names, sizes, strict=True):
        if type(size) is not int or not 1 <= size <= 20_000 or (name != "time" and size != 1):
            raise ValueError("unexpected Eurostat dimension size")
        codes = _positions(dimensions[name]["category"], size)
        if name == "time":
            periods = codes
        elif codes != (expected[name],):
            raise ValueError("unexpected Eurostat selection")
    values, statuses = _cells(payload["value"], len(periods)), _cells(payload.get("status"), len(periods))
    observations = []
    for position, period in enumerate(periods):
        value, status = values.get(position), statuses.get(position)
        if value is not None and type(value) not in {Decimal, int}:
            raise ValueError("invalid Eurostat value")
        if status is not None and not isinstance(status, str):
            raise ValueError("invalid Eurostat status")
        raw = str(value) if value is not None else None
        flags = status.lower().split() if status else []
        confidentiality = status if "c" in flags else None
        if value is None:
            kind = "missing"
        elif not flags:
            kind = "normal"
        else:
            kind = next((STATUSES[flag] for flag in ("p", "e", "f", "b") if flag in flags), "unknown")
        observations.append(Observation(
            parse_period(indicator.frequency, period), parse_value(raw) if raw is not None else None,
            raw, status, kind, confidentiality,
        ))
    # Dataset update and dimension labels are capture evidence only.
    updated = payload.get("updated")
    if updated is not None and not isinstance(updated, str):
        raise ValueError("invalid Eurostat dataset update metadata")
    labels = {}
    for name, code in expected.items():
        label = dimensions[name]["category"].get("label", {}).get(code)
        if label is not None and not isinstance(label, str):
            raise ValueError("invalid Eurostat dimension label")
        labels[name] = {"code": code, "label": label}
    return tuple(observations), {
        "dataset_updated": updated,
        "dimensions": labels,
    }
