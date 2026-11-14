"""The verified English display preserves the original editorial claim and citation."""

from types import SimpleNamespace

import pytest

from backend.macro_ai import v2_service
from backend.macro_ai.models import Claim
from backend.macro_ai.v2_models import Intent


@pytest.mark.parametrize("dynamic", [True, False])
def test_display_distinguishes_gdp_vintages_without_replacing_evidence(monkeypatch, dynamic):
    monkeypatch.setattr(v2_service, "ENGLISH_SUMMARIES", {
        "fixture-version": "The 2021 bulletin reported 3.6% quarter-on-quarter growth.",
    })
    source = SimpleNamespace(
        id="claim:fixture", claim_version="fixture-version", relation="direct",
        measure="real_gdp_qoq", claim_type="observed", attribution="Test bulletin",
    )
    claim = Claim(text="El boletín de 2021 indicó un crecimiento intertrimestral de 3,6%.",
                  citations=[source.id])
    series = SimpleNamespace(id="series:real-gdp-qoq", updated_at=None)
    result = SimpleNamespace(
        fact=Claim(text="The official series records 3.2% growth.", citations=[series.id]),
        evidence=series, observation_prose=("The official series records 3.2% growth.",) * 2,
        calculation=None, provisional=False,
    )
    intent = Intent.model_validate({
        "purpose": "descriptive", "requirements": [{"excerpt": "GDP growth?", "operation": "trend"}],
    })
    output = v2_service.evaluate_support({
        "official": [result], "curated": [(source, claim)], "limitations": [],
        "plans": [], "intent": intent,
        "runtime": SimpleNamespace(dynamic_editorial=dynamic, capture_vintage="fixture", knowledge_cutoff=None),
    })
    official, editorial = output["statements"]
    assert editorial.claim is claim
    paragraph = editorial.render("direct")
    assert paragraph.citations == [source.id]
    if dynamic:
        assert official.render("direct").text.startswith("Revised Eurostat EA21 series.")
        assert paragraph.text == "Historical release estimate. The 2021 bulletin reported 3.6% quarter-on-quarter growth."
    else:
        assert official.render("direct").text == result.fact.text
        assert paragraph.text == claim.text
