/* oxlint-disable jsx-a11y/prefer-tag-over-role -- A keyboard-operable SVG data plot requires SVG slider semantics; an HTML input cannot contain the plotted marks. */
import { useEffect, useId, useRef, useState, type PointerEvent, type CSSProperties } from 'react';
import { indicatorPresentation, unitLabel, numeric, pointDay, rateSegments, type ChartHistory } from './macro-chart';
export function MacroPlot({ history }: { history: ChartHistory }) {
  const [activeIndex, setActiveIndex] = useState<number | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const [size, setSize] = useState({ width: 720, height: 225 });
  const tooltipId = useId();
  const helpId = useId();
  const clipId = useId();
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
  const lower = Math.min(...values);
  const upper = Math.max(...values);
  const format = new Intl.NumberFormat('en', { maximumSignificantDigits: 6 });
  const labelWidth = Math.max(format.format(lower).length, format.format(upper).length);
  const compact = size.width < 480;
  const left = Math.max(compact ? 58 : 76, labelWidth * (compact ? 6.1 : 7.25) + 16);
  const right = size.width - (compact ? 18 : 42);
  const top = 28;
  const bottom = size.height - 47;
  const center = (left + right) / 2;
  const x = (day: string) => maximumDate === minimumDate ? center
    : left + ((Date.parse(day) - minimumDate) / (maximumDate - minimumDate)) * (right - left);
  const y = (value: number) => upper === lower ? (top + bottom) / 2 : bottom - ((value - lower) / (upper - lower)) * (bottom - top);
  const presentation = indicatorPresentation(history.indicator);
  const unit = unitLabel(history.indicator.unit, history.indicator.multiplier);
  const linePoints = points.map((row) => `${x(pointDay(row))},${y(Number(row.value))}`).join(' ');
  const label = (day: string) => {
    const date = new Date(`${day}T00:00:00Z`);
    if (history.indicator.frequency === 'quarterly') return `${date.getUTCFullYear()} Q${Math.floor(date.getUTCMonth() / 3) + 1}`;
    return new Intl.DateTimeFormat('en', { timeZone: 'UTC', year: 'numeric', month: 'short',
      ...(history.indicator.frequency === 'monthly' ? {} : { day: 'numeric' as const }) }).format(date);
  };
  const selectedIndex = activeIndex === null ? null : Math.min(activeIndex, Math.max(0, points.length - 1));
  const active = selectedIndex === null ? undefined : points[selectedIndex];
  function inspectPointer(event: PointerEvent<SVGSVGElement>) {
    const bounds = event.currentTarget.getBoundingClientRect();
    if (!bounds.width || !bounds.height || !points.length) return;
    const scale = Math.min(bounds.width / size.width, bounds.height / size.height);
    const cursor = (event.clientX - bounds.left - (bounds.width - size.width * scale) / 2) / scale;
    let nearest = 0;
    points.forEach((row, index) => {
      if (Math.abs(x(pointDay(row)) - cursor) < Math.abs(x(pointDay(points[nearest])) - cursor)) nearest = index;
    });
    setActiveIndex(nearest);
  }
  const description = (row: typeof points[number]) => `${presentation.label} · ${row.period_raw} · ${row.value} ${unit} · ${row.status_kind}`;
  return <div className="macro-plot-container">
    <p id={helpId} className="sr-only">Hover or touch the chart to inspect an observation. Focus the chart and use Left and Right Arrow keys to move between observations, Home and End to jump, and Escape to dismiss the value.</p>
    <svg ref={svgRef} className="macro-plot" viewBox={`0 0 ${size.width} ${size.height}`}
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
    onPointerMove={inspectPointer}
    onPointerDown={inspectPointer}
    onPointerLeave={(event) => { if (event.pointerType !== 'touch') setActiveIndex(null); }}
    style={{ '--indicator-color': presentation.color } as CSSProperties}
    data-compact={compact} data-domain-start={first} data-domain-end={last}
    aria-label={`${presentation.label}: ${effective ? 'effective events and verified periods of applicability' : 'official observations by period'}`}>
    <title>{presentation.label} · {unit}</title>
    {[upper, lower].filter((value, i, all) => all.indexOf(value) === i).map((value) =>
      <g key={value}>
        <line className="macro-gridline" x1={left} x2={right} y1={y(value)} y2={y(value)} />
        <text x={left - 7} y={y(value) + 4} textAnchor="end">{format.format(value)}</text>
      </g>)}
    <line className="macro-gridline" x1={left} x2={right} y1={bottom + 10} y2={bottom + 10} />
    {/* Reveal the marks via clipping while the date and value coordinates stay fixed. */}
    <defs><clipPath id={clipId}>
      <rect key={`${history.indicator.id}/${history.start}/${history.end}`} className="macro-reveal"
        width={size.width} height={size.height} />
    </clipPath></defs>
    {active && <line className="macro-guide" x1={x(pointDay(active))} x2={x(pointDay(active))} y1={top} y2={bottom + 10} />}
    <g clipPath={`url(#${clipId})`}>
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
      {points.map((row, index) => <circle key={row.period_raw} className={selectedIndex === index ? 'macro-dot is-active' : 'macro-dot'} cx={x(pointDay(row))} cy={y(Number(row.value))}
        r={selectedIndex === index ? 5 : points.length > 150 ? 1.6 : 3.5}
        onPointerEnter={() => setActiveIndex(index)}>
        <title>{description(row)}</title>
      </circle>)}
    </g>
    <text x={first === last ? center : left} y={size.height - 14} textAnchor={first === last ? 'middle' : 'start'}>{label(first)}</text>
    {first !== last && <text x={right} y={size.height - 14} textAnchor="end">{label(last)}</text>}
    </svg>
    {active && <div id={tooltipId} role="tooltip" className="macro-tooltip"
      aria-live="polite"
      style={{ '--tooltip-left': `${x(pointDay(active)) / size.width * 100}%`,
        top: `${y(Number(active.value)) / size.height * 100}%`,
        transform: y(Number(active.value)) < 100 ? 'translate(-50%, 12px)' : 'translate(-50%, calc(-100% - 12px))',
      } as CSSProperties}>
      <strong>{presentation.label}</strong>
      <span>{active.period_raw} · {active.value} {unit}</span>
      <span className="sr-only">{active.status_kind}</span>
    </div>}
  </div>;
}
