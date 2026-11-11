"""Deterministic Phase C fixtures. No live database or Gemini verification."""
from datetime import date
from decimal import Decimal
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.macro_ai.models import ResearchRequest, EVENT_WINDOWS, ObservedChange
from backend.macro_ai.statistics import summarize
from backend.macro_ai.retrieval import CorpusIndex, CorpusDocument
from backend.macro_ai.grounding import Proposition, movement, permitted_caveats, validate_narrative
from backend.macro_ai.service import Workflow
from backend.macro_data.api import MacroHistory, MacroObservation, indicator_metadata
from backend.macro_data.contracts import CATALOG, parse_period


def row(key, period, value, status="normal"):
    parsed = parse_period(CATALOG[key].frequency, period)
    return MacroObservation(period_raw=period, period_start=parsed.start, period_end=parsed.end,
                            effective_date=parsed.effective_date, value=Decimal(value), status_kind=status, status_raw=None)


def history(key, request, rows):
    return MacroHistory(indicator=indicator_metadata(CATALOG[key]), start=request.start, end=request.end,
                        updated_at=None, observations=rows)


def doc(id="fixture", text="The institutional context describes observed data without establishing causality.",
        topics=None, published=date(2020, 3, 1)):
    return CorpusDocument(id=id, title="Synthetic institutional context", url="https://www.ecb.europa.eu/fixture",
                          rights_url="https://www.ecb.europa.eu/rights", attribution="Synthetic test fixture",
                          published_on=published, topics=topics or ["fx", "covid"], text=text)


@pytest.mark.parametrize('event', list(EVENT_WINDOWS))
def test_exact_selected_event_windows(event):
    start, end = EVENT_WINDOWS[event]
    request = ResearchRequest(question='How did EUR/USD change?', start=start, end=end, indicator_id='eur-usd', event_id=event)
    assert (request.start, request.end) == (start, end)
    with pytest.raises(ValidationError):
        ResearchRequest(question=request.question, start=start, end=start, indicator_id='eur-usd', event_id=event)


@pytest.mark.parametrize('question,indicator,event', [
    ('How did Eurozone Inflation change?', 'eur-usd', 'covid'),
    ('How did the Federal Reserve change interest rates?', 'deposit-rate', 'covid'),
    ('How did the war in Ukraine affect EUR/USD rate?', 'eur-usd', 'covid'),
])
def test_explicit_context_conflicts_abstain_before_io(question, indicator, event):
    def forbidden(*_):
        pytest.fail('conflicting selected context reached provider')
    start, end = EVENT_WINDOWS[event]
    response = Workflow(CorpusIndex([doc()]), read=forbidden, generate=forbidden).run(
        ResearchRequest(question=question, indicator_id=indicator, event_id=event, start=start, end=end))
    assert response.status == 'insufficient_evidence' and response.evidence == []
    assert response.indicator_id == indicator and response.event_id == event


@pytest.mark.parametrize('values,reported', [
    (('100', '90', '110'), True), (('0', '1', '2'), False), (('-1', '1', '2'), False),
    (('0.0000000000001', '1', '2'), False), (('0.000001', '100', '200'), False),
])
def test_percent_safety_and_largest_swing_chronology(values, reported):
    request = ResearchRequest(question='How did EUR/USD change?', start='2020-02-01', end='2020-12-31', event_id='covid')
    rows = [row('eur-usd', period, value) for period, value in zip(('2020-02-01', '2020-06-01', '2020-12-01'), values)]
    summary = summarize({'eur-usd': history('eur-usd', request, list(reversed(rows)))}, ('eur-usd',), request)
    percents = [item.text for item in summary.calculations if 'percentage change' in item.text]
    assert bool(percents) == reported
    assert bool(summary.observed_changes) == reported
    if reported:
        assert '10.000000%' in percents[0]
        assert '22.222222%' in percents[1]
        assert '2020-06-01' in summary.calculations[1].text and '2020-12-01' in summary.calculations[1].text
    else:
        assert any('nonpositive or near zero' in note for note in summary.limitations)


@pytest.mark.parametrize('key,unit', [('deposit-rate', 'basis points'), ('hicp-yoy', 'percentage points'), ('real-gdp-qoq', 'percentage points')])
def test_rate_and_growth_changes_keep_meaningful_units(key, unit):
    request = ResearchRequest(question='How did rates change?', start='2020-01-01', end='2020-12-31')
    periods = ('2020-Q1', '2020-Q3') if key == 'real-gdp-qoq' else ('2020-01', '2020-12') if key == 'hicp-yoy' else ('2020-01-01', '2020-12-01')
    summary = summarize({key: history(key, request, [row(key, periods[0], '-1'), row(key, periods[1], '2')])}, (key,), request)
    assert unit in summary.calculations[0].text and unit in summary.calculations[1].text
    assert not any('percentage change' in claim.text for claim in summary.calculations)
    assert summary.observed_changes[0].unit == 'percentage_points'
    assert summary.observed_changes[0].value == '3.00'


def test_selected_event_excludes_prior_anchor_and_outside_rows_and_preserves_dates():
    request = ResearchRequest(question='How did the ECB deposit rate change?', start='2020-02-01', end='2020-12-31', event_id='covid')
    h = history('deposit-rate', request, [row('deposit-rate', '2020-01-01', '-1'), row('deposit-rate', '2020-03-01', '1'), row('deposit-rate', '2020-12-01', '2'), row('deposit-rate', '2021-01-01', '10')])
    h.anchor = row('deposit-rate', '2019-01-01', '-2')
    h.coverage_start = request.start
    summary = summarize({'deposit-rate': h}, ('deposit-rate',), request)
    assert '2019-01-01' not in summary.facts[0].text and '2021-01-01' not in summary.facts[0].text
    assert summary.evidence[0].period_start == date(2020, 3, 1)
    assert '100 basis points' in summary.calculations[0].text


def test_statistical_break_abstains_from_comparisons():
    request = ResearchRequest(question='How did EUR/USD change?', start='2020-01-01', end='2020-12-31')
    summary = summarize({'eur-usd': history('eur-usd', request, [row('eur-usd', '2020-02-01', '1'), row('eur-usd', '2020-12-01', '2', 'break')])}, ('eur-usd',), request)
    assert not summary.complete and not summary.calculations
    assert 'statistical break' in summary.limitations[-1]


def test_retrieval_filters_event_before_top_k_and_exposes_real_cosine():
    docs = [doc(f'other-{i}', topics=['fx'], text='EUR USD EUR USD FX foreign exchange market observations are described.') for i in range(4)]
    docs.append(doc('eligible', topics=['fx', 'covid']))
    docs.append(doc('future', topics=['fx', 'covid'], published=date(2021, 1, 1)))
    index = CorpusIndex(docs)
    matches = index.retrieve('EUR USD', ('fx', 'covid'), date(2020, 12, 31), event='covid', onset=date(2020, 2, 1))
    assert [item.id for item in matches] == ['doc:eligible:1']
    query = index.embeddings.embed_query('EUR USD fx covid')
    vector = index.vectors[4]
    expected = sum(a*b for a,b in zip(query, vector))
    assert matches[0].metadata['similarity_score'] == pytest.approx(expected)
    assert matches[0].metadata['similarity_metric'] == 'cosine'
    ranked = index.retrieve('EUR USD', ('fx',), date(2020, 12, 31))
    scores = [item.metadata['similarity_score'] for item in ranked]
    assert scores == sorted(scores, reverse=True)


@pytest.mark.parametrize('sentence', [
    'The euro rose by 999% over the selected period.',
    'The euro rose by 10.89 percentage points over the selected period.',
    'The euro rose by 10.89% in 2022.',
    'The euro fell by 10.89% over the selected period.',
    'COVID-19 caused the euro to rise by 10.89% over the selected period.',
    'The euro rose by 10.89% over the selected period. [1]',
])
def test_grounding_rejects_unsupported_factual_claim_even_with_valid_id(sentence):
    change = ObservedChange(indicator_id='eur-usd', value='10.89', unit='percent', direction='up',
                            first_observed_on='2020-02-03', last_observed_on='2020-12-31', citation='series:eur-usd')
    context = Proposition('doc:fixture:1', ('the ECB',), ('announced a joint effort',), ('in March 2020',))
    caveats = permitted_caveats(event=True, multiple=False)
    with pytest.raises(ValueError):
        validate_narrative({
            'sentences': [{'evidence_id': 1, 'text': sentence},
                          {'evidence_id': 2, 'text': 'In March 2020, the ECB announced a joint effort.'}],
            'caveat': caveats[0],
        }, [movement(change), context], caveats)


def test_covid_eur_usd_uses_actual_fixture_endpoints_and_keeps_authored_prose():
    request = ResearchRequest(question='How did the COVID-19 pandemic affect EUR/USD rate?',
                              indicator_id='eur-usd', event_id='covid', start='2020-02-01', end='2020-12-31')
    def read(keys, req):
        assert keys == ('eur-usd',) and (req.start, req.end) == EVENT_WINDOWS['covid']
        return {'eur-usd': history('eur-usd', req, [
            row('eur-usd', '2020-12-31', '1.2271'), row('eur-usd', '2020-02-01', '2', status='missing'),
            row('eur-usd', '2020-02-03', '1.1066'), row('eur-usd', '2021-01-01', '3'),
        ])}
    authored = [
        'Between the first and last observations, the value of the euro in US dollars rose.',
        'For context, central banks including the ECB announced a joint effort to make US dollars easier to obtain in March 2020.',
        'The timing provides useful context, but it does not show that the event caused the observed change.',
    ]

    def model(intent, raw):
        assert 'historical context: COVID-19' in intent
        context = json.loads(raw)
        assert 'rose by 10.89%' in context['propositions'][0]['predicates']
        return {'sentences': [{'evidence_id': i, 'text': text} for i, text in enumerate(authored[:2], 1)],
                'caveat': authored[2]}

    corpus = CorpusIndex.load(Path('backend/macro_ai/corpus.json'))
    result = Workflow(corpus, read=read, generate=model).run(request)
    assert result.status == 'answered' and result.narrative_kind == 'gemini' and result.model_id is None
    change, = result.observed_changes
    assert (change.value, change.unit, change.direction) == ('10.89', 'percent', 'up')
    assert (change.first_observed_on, change.last_observed_on) == (date(2020, 2, 3), date(2020, 12, 31))
    assert [claim.text for claim in result.narrative] == authored
    assert result.narrative[1].citations == ['doc:ecb-usd-liquidity-march-2020:1']
    assert result.evidence[-1].published_on <= request.end


def test_positive_level_to_zero_reports_minus_one_hundred_percent():
    request = ResearchRequest(question='How did M3 change?', start='2020-01-01', end='2020-12-31')
    summary = summarize({'m3-stock': history('m3-stock', request, [row('m3-stock', '2020-01', '100'), row('m3-stock', '2020-12', '0')])}, ('m3-stock',), request)
    assert any('-100.000000%' in claim.text for claim in summary.calculations)
    assert summary.observed_changes[0].value == '-100.00'
    assert summary.observed_changes[0].direction == 'down'
