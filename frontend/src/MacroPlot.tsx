import type { CSSProperties } from 'react';
import { indicatorPresentation, numeric, pointDay, rateSegments, type ChartHistory } from './macro-chart';

export function MacroPlot({ history }: { history: ChartHistory }) {
  const points = history.observations.filter(numeric);
  const effective = history.indicator.frequency === 'effective_date';
  const segments = effective ? rateSegments(history) : [];
  const values = [...points.map((row) => Number(row.value)), ...segments.map((segment) => segment.value)];
  if (!values.length) return <output className="chart-empty">No numeric observations in this period.</output>;
  const dates = [...points.map(pointDay), ...segments.flatMap(({ first, last }) => [first, last])].sort();
  const first = dates[0];
  const last = dates[dates.length - 1];
  const minimumDate = Date.parse(first);
  const maximumDate = Date.parse(last);
  const x = (day: string) => maximumDate === minimumDate ? 377
    : 76 + ((Date.parse(day) - minimumDate) / (maximumDate - minimumDate)) * 602;
  const lower = Math.min(...values);
  const upper = Math.max(...values);
  const y = (value: number) => upper === lower ? 103 : 178 - ((value - lower) / (upper - lower)) * 150;
  const format = new Intl.NumberFormat('en', { maximumSignificantDigits: 6 });
  const presentation = indicatorPresentation(history.indicator);
  const linePoints = points.map((row) => `${x(pointDay(row))},${y(Number(row.value))}`).join(' ');
  const label = (day: string) => {
    const date = new Date(`${day}T00:00:00Z`);
    if (history.indicator.frequency === 'quarterly') return `${date.getUTCFullYear()} Q${Math.floor(date.getUTCMonth() / 3) + 1}`;
    return new Intl.DateTimeFormat('en', { timeZone: 'UTC', year: 'numeric', month: 'short',
      ...(history.indicator.frequency === 'monthly' ? {} : { day: 'numeric' as const }) }).format(date);
  };
  return <svg className="macro-plot" viewBox="0 0 720 225"
    style={{ '--indicator-color': presentation.color } as CSSProperties}
    data-domain-start={first} data-domain-end={last}
    aria-label={`${presentation.label}: ${effective ? 'effective events and verified periods of applicability' : 'official observations by period'}`}>
    <title>{presentation.label} · {history.indicator.unit}</title>
    {[upper, lower].filter((value, i, all) => all.indexOf(value) === i).map((value) =>
      <g key={value}>
        <line className="macro-gridline" x1="76" x2="678" y1={y(value)} y2={y(value)} />
        <text x="69" y={y(value) + 4} textAnchor="end">{format.format(value)}</text>
      </g>)}
    <line className="macro-gridline" x1="76" x2="678" y1="188" y2="188" />
    {!effective && points.length > 1 && <g>
      <polyline className="macro-outline" points={linePoints} aria-hidden="true" />
      <polyline className="macro-line" fill="none" points={linePoints} />
    </g>}
    {segments.map(({ first: start, last: end, value, nextValue }, index) => <g key={index}>
      <line className="macro-outline" aria-hidden="true" x1={x(start)} x2={x(end)} y1={y(value)} y2={y(value)} />
      <line className="macro-segment" x1={x(start)} x2={x(end)} y1={y(value)} y2={y(value)} />
      {nextValue !== null && <>
        <line className="macro-outline" aria-hidden="true" x1={x(end)} x2={x(end)} y1={y(value)} y2={y(nextValue)} />
        <line className="macro-step" x1={x(end)} x2={x(end)} y1={y(value)} y2={y(nextValue)} />
      </>}
    </g>)}
    {points.map((row) => <circle key={row.period_raw} className="macro-dot" cx={x(pointDay(row))} cy={y(Number(row.value))}
      r={points.length > 150 ? 1.6 : 3.5} tabIndex={0}>
      <title>{presentation.label} · {row.period_raw} · {row.value} {history.indicator.unit} · {row.status_kind}</title>
    </circle>)}
    <text x={first === last ? '377' : '76'} y="211" textAnchor={first === last ? 'middle' : 'start'}>{label(first)}</text>
    {first !== last && <text x="678" y="211" textAnchor="end">{label(last)}</text>}
  </svg>;
}
