import { RippleButton } from './RippleButton';
import { Fragment, useEffect, useRef, useState, type ReactNode } from 'react';
import {
  AdministratorCredentialError, askMacroAnalysis, checkAdministrator,
  type MacroAnalysis, type MacroIndicator, type ObservedChange, type ResearchDiagnostic, type ResearchEvidence,
} from './macro-api';
import { QuestionComposer, type QuestionSubmission } from './QuestionComposer';
import { TurnstileCheck } from './TurnstileCheck';

const displayDate = new Intl.DateTimeFormat('en-GB', {
  day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC',
});
const measureLabels: Record<string, string> = {
  'm3-stock': 'Broad money (M3)', 'deposit-rate': 'ECB deposit rate',
  'real-gdp-qoq': 'Quarterly real GDP growth', 'hicp-yoy': 'Annual euro area inflation',
};
const categoryLabels = {
  'official observation': 'Official data',
  'calculated from official observations': 'Calculated change',
  'documentary context': 'Documentary context',
  'institutional interpretation': 'Institutional interpretation',
  'proxy context': 'Proxy context',
};
const ragDiagnosis: Record<ResearchDiagnostic, string> = {
  ambiguous_interpretation: 'Research v2 could not confidently interpret the question within the selected scope.',
  unsupported_operation_or_series: 'Research v2 does not support the requested operation, series or period definition.',
  missing_official_observations: 'Research v2 could not find comparable official observations for the requested period.',
  insufficient_documentary_evidence: 'There is insufficient evidence in our institutional corpus to answer this question confidently.',
  unsupported_causal_inference: 'Research v2 could not estimate the requested causal effect.',
  interpretation_unavailable: 'The question interpreter was unavailable.',
  evidence_reader_unavailable: 'An evidence reader was unavailable.',
  citation_validation_failed: 'Research v2 could not validate the citations.',
  no_verified_statements: 'Research v2 could not produce a verified statement for this question.',
};
const fallbackErrors = {
  timeout: 'Selection of a conceptual explanation timed out.',
  provider_unavailable: 'Gemini is currently unavailable to select a conceptual explanation.',
  invalid_response: 'Gemini did not return a valid conceptual selection.',
};

function ChangeHeadline({ change, citation }: { change: ObservedChange; citation: ReactNode }) {
  const { direction, value, unit, indicator_id: id } = change;
  const sign = direction === 'up' ? '+' : direction === 'down' ? '−' : '';
  const arrow = direction === 'up' ? '↑' : direction === 'down' ? '↓' : '→';
  const unitLabel = unit === 'percent' ? '%' : ' pp';
  const tiny = value === '0.00' && direction !== 'flat';
  const label = id === 'eur-usd'
    ? direction === 'flat' ? 'The euro was unchanged against the US dollar'
      : direction === 'up' ? 'The euro gained value against the US dollar' : 'The euro lost value against the US dollar'
    : `${measureLabels[id]} ${direction === 'up' ? 'rose' : direction === 'down' ? 'fell' : 'was unchanged'}`;
  return <div className="research-change">
    <div className={`research-change-value ${direction}`}>
      {tiny ? `<0.01${unitLabel}` : `${sign}${value.replace(/^-/, '')}${unitLabel}`} <span aria-hidden="true">{arrow}</span>
    </div>
    <p>{label}{citation}</p>
    <p className="muted">
      <time dateTime={change.first_observed_on}>{displayDate.format(new Date(change.first_observed_on))}</time>
      {' – '}
      <time dateTime={change.last_observed_on}>{displayDate.format(new Date(change.last_observed_on))}</time>
      {unit === 'percentage_points' && ' · percentage points'}
    </p>
  </div>;
}

function uniqueDocuments(items: ResearchEvidence[]) {
  const seen = new Set<string>();
  return items.filter((item) => {
    if (item.kind !== 'document' || seen.has(item.url)) return false;
    seen.add(item.url);
    return true;
  });
}

export function MacroResearch({ enabled, indicatorId, indicators, onIndicatorChange, researchV2Available = false, publicDemo = false, sitekey = '' }: {
  enabled: boolean; indicatorId: string; indicators: MacroIndicator[]; onIndicatorChange: (id: string) => void;
  researchV2Available?: boolean; publicDemo?: boolean; sitekey?: string;
}) {
  const [experimental, setExperimental] = useState(false);
  const researchV2 = publicDemo || (experimental && researchV2Available);
  const [consent, setConsent] = useState(false);
  const [token, setToken] = useState('');
  const [challengeAttempt, setChallengeAttempt] = useState(0);
  const [adminSecret, setAdminSecret] = useState('');
  const [adminStatus, setAdminStatus] = useState<'unchecked' | 'checking' | 'authenticated' | 'invalid'>('unchecked');
  const [adminError, setAdminError] = useState('');
  const [showSecret, setShowSecret] = useState(false);
  const adminCheck = useRef<AbortController>(null);
  useEffect(() => () => adminCheck.current?.abort(), []);
  const isAdmin = publicDemo && adminStatus === 'authenticated';
  const researchAvailable = enabled || isAdmin;
  const [request, setRequest] = useState<{
    indicatorId: string; controller: AbortController; pending: boolean; error?: string; result?: MacroAnalysis;
    view?: 'rag' | 'general';
  }>();
  const active = useRef<AbortController>(null);
  useEffect(() => () => active.current?.abort(), [indicatorId, enabled, researchV2]);
  const current = researchAvailable && request?.indicatorId === indicatorId && !request.controller.signal.aborted ? request : undefined;
  const pending = current?.pending ?? false;
  const error = current?.error;
  const result = current?.result;
  const fallback = result?.general_fallback;
  const showingGeneral = current?.view === 'general' && fallback?.status === 'answered';

  function selectResultView(view: 'rag' | 'general') {
    setRequest((previous) => previous && previous.controller === current?.controller ? { ...previous, view } : previous);
  }

  async function validateAdministrator() {
    if (!adminSecret || pending || isAdmin || adminStatus === 'checking') return;
    adminCheck.current?.abort();
    const controller = new AbortController();
    adminCheck.current = controller;
    setAdminStatus('checking');
    setAdminError('');
    setShowSecret(false);
    try {
      const valid = await checkAdministrator(adminSecret, controller.signal);
      if (controller.signal.aborted) return;
      setAdminStatus(valid ? 'authenticated' : 'invalid');
      if (valid) setToken('');
    } catch (cause) {
      if (controller.signal.aborted) return;
      setAdminStatus('unchecked');
      setAdminError(cause instanceof Error ? cause.message : 'Administrator verification is currently unavailable.');
    }
  }

  function leaveAdministrator() {
    adminCheck.current?.abort();
    if (isAdmin && pending) {
      active.current?.abort();
      setRequest(undefined);
    }
    setAdminSecret('');
    setAdminStatus('unchecked');
    setAdminError('');
    setShowSecret(false);
  }

  const canSubmit = !publicDemo || (consent && (isAdmin || Boolean(token)));

  function submit(submission: QuestionSubmission) {
    if (!researchAvailable || pending || submission.indicatorId !== indicatorId || !canSubmit) return;
    active.current?.abort();
    const controller = new AbortController();
    active.current = controller;
    setRequest({ indicatorId, controller, pending: true });
    if (publicDemo) {
      setToken('');
      setChallengeAttempt((value) => value + 1);
    }
    askMacroAnalysis(submission.question, submission.start, submission.end, controller.signal,
      { indicator_id: submission.indicatorId, event_id: submission.eventId,
        ...(researchV2 ? { research_version: 'v2' as const } : {}) },
      publicDemo && !isAdmin ? token : undefined, isAdmin ? adminSecret : undefined).then((data) => {
      if (!controller.signal.aborted) setRequest((current) => current?.controller === controller ? {
        ...current, result: data, view: data.general_fallback?.status === 'answered' ? 'general' : 'rag',
      } : current);
    }, (cause: unknown) => {
      const error = publicDemo && cause instanceof Error ? cause.message : 'Local macro research is currently unavailable.';
      if (controller.signal.aborted) return;
      if (cause instanceof AdministratorCredentialError) {
        setAdminStatus('invalid');
        setAdminSecret('');
        setShowSecret(false);
      }
      setRequest((current) => current?.controller === controller ? { ...current, error } : current);
    }).finally(() => {
      if (!controller.signal.aborted) setRequest((current) => current?.controller === controller ? { ...current, pending: false } : current);
    });
  }

  const documents = result?.research_v2 ? result.evidence.filter(({ kind }) => kind === 'document')
    : uniqueDocuments(result?.evidence ?? []);
  const series = result?.evidence.filter(({ kind }) => kind === 'series') ?? [];
  const narrative = result?.narrative ?? [];
  const changes = result?.observed_changes ?? [];
  const hasObservations = changes.length > 0 || narrative.some(({ category }) =>
    category === 'official observation' || category === 'calculated from official observations');
  const citations = new Map(result?.evidence.map((source, index) => [source.id, { source, number: index + 1 }]));

  function citationLink(id: string) {
    const citation = citations.get(id);
    if (!citation) return null;
    const { source, number } = citation;
    return <Fragment key={id}>{' '}<a href={source.url} aria-label={`Source ${number}, ${source.title}`}>[{number}]</a></Fragment>;
  }

  return <section className="panel research-panel" aria-labelledby="macro-research-title">
    <h3 id="macro-research-title">Macro research</h3>
    {enabled && !publicDemo && researchV2Available && <div>
      <label>
        <input type="checkbox" checked={researchV2} disabled={pending}
          onChange={(event) => { setExperimental(event.target.checked); setRequest(undefined); }} />
        {' '}Use experimental Research v2
      </label>
      <p className="muted">
        Research v2 sends your original question to Google Gemini. Keep secrets and credentials out of your question.
      </p>
    </div>}
    {publicDemo && <details className="research-details">
      <summary>Administrator access</summary>
      <form className="research-admin" onSubmit={(event) => { event.preventDefault(); void validateAdministrator(); }} autoComplete="off">
        <label>Administrator secret
          <input type={showSecret ? 'text' : 'password'} value={adminSecret}
            autoComplete="off" spellCheck={false} autoCapitalize="none" maxLength={64}
            disabled={pending || isAdmin || adminStatus === 'checking'}
            onChange={(event) => {
              setAdminSecret(event.target.value);
              setAdminStatus('unchecked');
              setAdminError('');
            }} />
        </label>
        <button type="button" disabled={!adminSecret}
          onClick={() => setShowSecret(!showSecret)}>{showSecret ? 'Hide secret' : 'Show secret'}</button>
        {!isAdmin && <button type="submit" disabled={!adminSecret || pending || adminStatus === 'checking'}>
          Verify credential
        </button>}
        {(adminSecret || isAdmin) && <button type="button" onClick={leaveAdministrator}>Exit administrator mode</button>}
        <output className="muted">{adminError || ({
          unchecked: 'Credential not checked', checking: 'Checking credential…',
          authenticated: 'Authenticated', invalid: 'Invalid credential',
        })[adminStatus]}</output>
      </form>
    </details>}
    {researchAvailable ? <QuestionComposer enabled={researchAvailable} pending={pending} indicators={indicators}
      indicatorId={indicatorId} onIndicatorChange={onIndicatorChange} onSubmit={submit}
      allowCustomWindow={researchV2}
      canSubmit={canSubmit}
      verification={publicDemo && <>
        <label className="muted">
          <input type="checkbox" checked={consent} disabled={pending}
            onChange={(event) => setConsent(event.target.checked)} />
          {' '}I agree to send my question to Google Gemini. I will keep secrets and credentials out of it.
        </label>
        {!pending && !isAdmin && <TurnstileCheck key={challengeAttempt} sitekey={sitekey} onToken={setToken} />}
      </>} />
      : <p className="muted">{publicDemo ? 'Public AI research is currently unavailable.'
        : 'AI research is available only in an enabled local workspace.'}</p>}
    {pending && <output className="notice">Checking official evidence…</output>}
    {error && <p role="alert" className="notice error">{error}</p>}
    {result && <div className="analysis-result" aria-live="polite">
      {result.research_v2 && <fieldset className="macro-presets" aria-label="Research result view">
        <RippleButton aria-pressed={!showingGeneral} onClick={() => selectResultView('rag')}>
          RAG-Based LLM Answer
        </RippleButton>
        <RippleButton aria-pressed={showingGeneral} disabled={fallback?.status !== 'answered'}
          onClick={() => selectResultView('general')}>Conceptual Explanation</RippleButton>
      </fieldset>}
      {showingGeneral ? <section className="result-section research-analysis" aria-labelledby="macro-analysis-title">
        <h3 id="macro-analysis-title">Conceptual explanation</h3>
        <p className="notice">Gemini selected passages from MacroPulse&apos;s reviewed concept catalogue.
          This explanation is unverified against the evidence for this research question.</p>
        <p className="research-narrative research-general-answer">{fallback.answer}</p>
        <p className="muted">Selection model {fallback.model_id}</p>
      </section> : <>
      {changes.map((change) => <ChangeHeadline key={change.citation} change={change} citation={citationLink(change.citation)} />)}
      <section className="result-section research-analysis" aria-labelledby="macro-analysis-title">
        <h3 id="macro-analysis-title">{result.research_v2 ? 'Evidence-based analysis'
          : result.narrative_kind === 'deterministic' ? 'Data summary' : 'Economic analysis'}</h3>
        {result.status === 'insufficient_evidence'
          ? <p>{result.insufficiency_reason ? ragDiagnosis[result.insufficiency_reason]
            : 'There is insufficient evidence to answer this question.'}</p>
          : <>
            {result.support === 'partial' && <p className="evidence-status">
              {hasObservations ? 'Partial descriptive answer' : 'Partial answer'}
            </p>}
            {narrative.map((claim, index) => <p className="research-narrative" key={index}>
              {claim.text}{claim.citations.map(citationLink)}
            </p>)}
            {narrative.length === 0 && <p>No concise analysis is available for this result.</p>}
          </>}
        {result.limitations.length > 0 && <ul className="research-limitations muted">
          {result.limitations.map((note) => <li key={note}>{note}</li>)}
        </ul>}
        {fallback?.status === 'failed' && <p role="alert" className="notice error">{fallbackErrors[fallback.error]}</p>}
      </section>
      <section className="result-section">
        <h3>Institutional sources</h3>
        {documents.length === 0 ? <p className="muted">No institutional documents were retrieved for this answer.</p>
          : <ul className="result-list research-sources">{documents.map((item) => <li key={item.id}>
            <a href={item.url}>{item.title}</a>
            <div className="muted">{item.attribution}
              {item.published_on && <> · <time dateTime={item.published_on}>{displayDate.format(new Date(item.published_on))}</time></>}
            </div>
            {item.claim_id && <div className="muted">
              {item.claim_type === 'institutional_interpretation' ? 'Institutional interpretation'
                : item.relation === 'proxy' ? 'Proxy context' : 'Documentary context'}
              {item.qualifiers && ` · ${item.qualifiers}`}
            </div>}
          </li>)}</ul>}
        {series.length > 0 && <>
          <h4>Statistical series</h4>
          <ul className="result-list research-sources">{series.map((item) => <li key={item.id}>
            <a href={item.url}>{item.title}</a><div className="muted">{item.attribution}</div>
          </li>)}</ul>
        </>}
        <details className="research-details">
          <summary>Technical details</summary>
          <p className="muted">{result.start} to {result.end}{result.model_id && ` · ${result.model_id}`}
            {result.narrative_kind === 'deterministic' && ' · Summary generated from verified evidence without Gemini'}
          </p>
          {result.publication_cutoff && <p className="muted">Document publication cutoff {result.publication_cutoff}</p>}
          {[...documents, ...series].map((item) => <p className="muted" key={item.id}>
            {item.title}
            {item.period_start && item.period_end && <> · {item.period_start} to {item.period_end}</>}
            {item.updated_at && <> · Data updated <time dateTime={item.updated_at}>{item.updated_at}</time></>}
            {item.similarity_metric === 'cosine' && item.similarity_score != null
              && <> · Cosine similarity {item.similarity_score.toFixed(3)}</>}
            {item.rights_url && <> · <a href={item.rights_url}>Reuse terms</a></>}
            {item.available_at && <> · Editorially available <time dateTime={item.available_at}>{item.available_at}</time></>}
            {item.measure && <> · {item.measure} · {item.unit}</>}
            {item.claim_version && <> · Claim version {item.claim_version}</>}
            {item.series_key && <> · {item.series_key} · {item.unit} · {item.adjustment}</>}
            {item.claim_id && <> · Claim {item.claim_id} · Document {item.document_id} · Relation {item.relation}
              {' · '}{item.geography}{' · '}{item.locator}
            </>}
          </p>)}
          {result.research_v2 && narrative.length > 0 && <ul className="muted">
            {narrative.map(({ category, citations }, index) => category && <li key={index}>
              Paragraph {index + 1} · {categoryLabels[category]}{citations.map(citationLink)}
            </li>)}
          </ul>}
        </details>
      </section>
      </>}
    </div>}
  </section>;
}
