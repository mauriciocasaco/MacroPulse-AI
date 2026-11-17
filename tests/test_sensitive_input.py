import json
from pathlib import Path

import pytest

from backend.macro_ai.sensitive_input import contains_sensitive_input


QUESTIONS = json.loads(
    (Path(__file__).parent / "fixtures/security/fase-12-s3.json").read_text(encoding="utf-8")
)["questions"]


@pytest.mark.parametrize("case", QUESTIONS["sensitive"], ids=lambda case: case["id"])
def test_sensitive_formats(case):
    assert contains_sensitive_input("".join(case["parts"]), secrets=(case.get("configured_secret", ""),))


@pytest.mark.parametrize("case", QUESTIONS["admitted"], ids=lambda case: case["id"])
def test_economic_questions_are_admitted(case):
    assert not contains_sensitive_input("".join(case["parts"]))


@pytest.mark.parametrize("question", [
    "Compare 2020-02-01 and 2020-12-31, including the base and target observations.",
    "How did banks change interest rates on €1.234.567,00 from +10000000%?",
    "Does a 400 500 6000 euro lending scenario illustrate pass-through?",
    "How would a +123456789 euro capital inflow affect the exchange rate?",
])
def test_economic_dates_and_amounts_are_not_identifiers(question):
    assert not contains_sensitive_input(question)


@pytest.mark.parametrize("question", [
    "Please call +1 (202) 555-0142.",
    "Phone: +12025550142.",
    "My password is fictional-value.",
])
def test_sensitive_format_before_sentence_punctuation(question):
    assert contains_sensitive_input(question)
