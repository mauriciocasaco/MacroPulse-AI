/* oxlint-disable jsx-a11y/prefer-tag-over-role -- A keyboard-operable SVG data plot requires SVG slider semantics; an HTML input cannot contain the plotted marks. */
import { useEffect, useId, useRef, useState, type CSSProperties } from 'react';
import { indicatorPresentation, numeric, pointDay, rateSegments, type ChartHistory } from './macro-chart';
export function MacroPlot({ history }: { history: ChartHistory }) {
  const [activeIndex, setActiveIndex] = useState<number | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const [size, setSize] = useState({ width: 720, height: 225 });
  const tooltipId = useId();
  const helpId = useId();
  const points = history.observations.filter(numeric);
  const effective = history.indicator.frequency === 'effective_date';
  const segments = effective ? rateSegments(history) : [];
  const values = [...points.map((row) => Number(row.value)), ...segments.map((segment) => segment.value)];
  useEffect(() => {
    const svg = svgRef.current;
    if (!svg || typeof ResizeObserver === 'undefined') return;
    const update = () => {
      const { width, height } = svg.getBoundingClientRect();
      if (width && height) setSize({ width, height });
    };
    const observer = new ResizeObserver(update);
    observer.observe(svg);
    update();
    return () => observer.disconnect();
  }, [values.length]);
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
  const selectedIndex = activeIndex === null ? null : Math.min(activeIndex, Math.max(0, points.length - 1));
  const active = selectedIndex === null ? undefined : points[selectedIndex];
  // SVG's default aspect-ratio preservation may letterbox narrow containers.
  const scale = Math.min(size.width / 720, size.height / 225);
  const offsetX = (size.width - 720 * scale) / 2;
  const offsetY = (size.height - 225 * scale) / 2;
  const description = (row: typeof points[number]) => `${presentation.label} · ${row.period_raw} · ${row.value} ${history.indicator.unit} · ${row.status_kind}`;
  return <div className="macro-plot-container">
    <p id={helpId} className="sr-only">Hover or touch the chart to inspect an observation. Focus the chart and use Left and Right Arrow keys to move between observations, Home and End to jump, and Escape to dismiss the value.</p>
    <svg ref={svgRef} className="macro-plot" viewBox="0 0 720 225"
    role="slider" tabIndex={points.length ? 0 : undefined}
    aria-valuemin={0} aria-valuemax={Math.max(0, points.length - 1)}
    aria-valuenow={selectedIndex ?? Math.max(0, points.length - 1)}
    aria-valuetext={points.length ? description(active ?? points[points.length - 1]) : 'No observed events in this period'}
    aria-orientation="horizontal"
    aria-describedby={`${helpId}${active ? ` ${tooltipId}` : ''}`}
    onFocus={() => setActiveIndex(points.length ? points.length - 1 : null)}
    onBlur={() => setActiveIndex(null)}
    onKeyDown={(event) => {
      if (!points.length) return;
      const current = selectedIndex ?? points.length - 1;
      const next = event.key === 'ArrowLeft' ? Math.max(0, current - 1)
        : event.key === 'ArrowRight' ? Math.min(points.length - 1, current + 1)
        : event.key === 'Home' ? 0 : event.key === 'End' ? points.length - 1 : null;
      if (next !== null || event.key === 'Escape') {
        event.preventDefault();
        setActiveIndex(next);
      }
    }}
    onPointerMove={(event) => {
      const bounds = event.currentTarget.getBoundingClientRect();
      if (!bounds.width || !points.length) return;
      const pointerScale = Math.min(bounds.width / 720, bounds.height / 225);
      const cursor = (event.clientX - bounds.left - (bounds.width - 720 * pointerScale) / 2) / pointerScale;
      let nearest = 0;
      points.forEach((row, index) => {
        if (Math.abs(x(pointDay(row)) - cursor) < Math.abs(x(pointDay(points[nearest])) - cursor)) nearest = index;
      });
      setActiveIndex(nearest);
    }}
    onPointerLeave={() => setActiveIndex(null)}
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
    {points.map((row, index) => <circle key={row.period_raw} className="macro-dot" cx={x(pointDay(row))} cy={y(Number(row.value))}
      r={selectedIndex === index ? 5 : points.length > 150 ? 1.6 : 3.5}
      onPointerEnter={() => setActiveIndex(index)}>
      <title>{description(row)}</title>
    </circle>)}
    <text x={first === last ? '377' : '76'} y="211" textAnchor={first === last ? 'middle' : 'start'}>{label(first)}</text>
    {first !== last && <text x="678" y="211" textAnchor="end">{label(last)}</text>}
    </svg>
    {active && <div id={tooltipId} role="tooltip" className="macro-tooltip"
      aria-live="polite"
      style={{ '--indicator-color': presentation.color,
        '--tooltip-left': `${(offsetX + x(pointDay(active)) * scale) / size.width * 100}%`,
        top: `${(offsetY + y(Number(active.value)) * scale) / size.height * 100}%`,
        transform: y(Number(active.value)) < 85 ? 'translate(-50%, 12px)' : 'translate(-50%, calc(-100% - 12px))',
      } as CSSProperties}>
      <strong>{presentation.label}</strong>
      <span>{active.period_raw} · {active.value} {history.indicator.unit}</span>
      <span className="sr-only">{active.status_kind}</span>
    </div>}
  </div>;
}
