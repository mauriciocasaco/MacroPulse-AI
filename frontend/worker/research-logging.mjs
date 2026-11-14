export const researchIdHeader = 'X-MacroPulse-Research-Id';

const knownErrorTypes = new Set(['Error', 'TypeError', 'SyntaxError', 'AbortError', 'TimeoutError',
  'NetworkError', 'InvalidStateError', 'OperationError']);

export function researchErrorType(cause) {
  const name = cause instanceof Error || cause instanceof DOMException ? cause.name : null;
  return knownErrorTypes.has(name) ? name : 'UnknownError';
}

export function researchEvent(id, stage, started, errorType, httpStatus) {
  const record = {
    event: 'public_research_stage',
    component: 'worker',
    research_id: id,
    stage,
    outcome: errorType ? 'error' : 'ok',
    duration_ms: Math.round((performance.now() - started) * 100) / 100,
  };
  if (errorType) record.error_type = errorType;
  if (httpStatus !== undefined) record.http_status = httpStatus;
  // Emit only fixed fields, never the thrown value or any request data.
  if (errorType) console.warn(record);
  else console.info(record);
}

export async function researchStage(id, stage, run) {
  const started = performance.now();
  try {
    const result = await run();
    researchEvent(id, stage, started);
    return result;
  } catch (cause) {
    researchEvent(id, stage, started, researchErrorType(cause));
    throw cause;
  }
}
