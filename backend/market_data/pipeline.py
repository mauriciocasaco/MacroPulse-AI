"""Minimal coordination for one daily provider ingestion run."""

from __future__ import annotations

from .models import Asset, AssetSource, DateRange, PriceAdapter, RunSummary
from .normalizer import normalize
from .repository import PriceRepository


def ingest_daily(
    adapter: PriceAdapter,
    repository: PriceRepository,
    asset: Asset,
    mapping: AssetSource,
    window: DateRange,
) -> RunSummary:
    """Acquire, normalize, and save one bounded daily batch.

    Persistence failures are recorded by the repository transaction path.
    This function records only failures that occur before save_batch starts.
    """
    mapping_id = repository.resolve_mapping_id(mapping)
    run_id = repository.start_run(mapping_id=mapping_id, window=window)
    try:
        raw_batch = adapter.fetch_daily(mapping, window)
        normalized_batch = normalize(raw_batch, asset, mapping, window)
    except Exception as error:
        try:
            repository.fail_run(run_id, _failure_code(error))
        except Exception:
            error.add_note("could not record the failed acquisition run")
        raise
    return repository.save_batch(run_id, normalized_batch)


def _failure_code(error: Exception) -> str:
    code = getattr(error, "code", None)
    if isinstance(code, str) and code.replace("_", "").isalnum() and code[:1].islower() and len(code) <= 64:
        return code
    if error.__class__.__name__ == "NormalizationStructureError":
        return "incompatible_batch"
    return "acquisition_failed"
