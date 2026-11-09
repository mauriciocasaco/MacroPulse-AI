import { useEffect, useMemo, useRef, useState } from 'react';
import uPlot from 'uplot';
import 'uplot/dist/uPlot.min.css';
import type { Asset, DailyBar } from './api';
import { chartData, type PriceField } from './chart-data';
import { DAY_SECONDS, dateToSeconds, formatDate, secondsToDate } from './dates';

const priceAxis = new Intl.NumberFormat('en-US', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const priceReading = new Intl.NumberFormat('en-US', { maximumFractionDigits: 20 });
const axisDate = new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' });
const axisMonth = new Intl.DateTimeFormat('en-US', { month: 'short', year: '2-digit', timeZone: 'UTC' });
const plotHeight = (width: number) => width < 600 ? 264 : 342;

interface Props {
  asset: Asset;
  bars: DailyBar[];
  field: PriceField;
}

export function PriceChart({ asset, bars, field }: Props) {
  const container = useRef<HTMLDivElement>(null);
  const chart = useRef<uPlot | null>(null);
  const [hover, setHover] = useState<number>();
  const [pinned, setPinned] = useState<number>();
  const { dates, prices, byDate, lastObserved, isolated } = useMemo(() => chartData(bars, field), [bars, field]);
  const selected = hover ?? pinned ?? lastObserved;
  const reading = selected === undefined ? undefined : byDate.get(selected);
  const price = reading?.[field];
  const label = field === 'close' ? 'Close' : 'Adj Close';

  useEffect(() => {
    const element = container.current;
    if (!element || lastObserved === undefined) return;

    const width = Math.max(1, element.clientWidth);
    const plot = new uPlot({
      width,
      height: plotHeight(width),
      legend: { show: false },
      tzDate: (timestamp) => uPlot.tzDate(new Date(timestamp * 1000), 'UTC'),
      cursor: { drag: { x: true, y: false } },
      scales: {
        x: { range: (_self, min, max) => min != null && min === max
          ? [min - DAY_SECONDS / 2, min + DAY_SECONDS / 2]
          : [min, max] },
      },
      series: [{}, {
        label,
        stroke: '#65e2bd',
        width: 1.75,
        spanGaps: false,
        points: { filter: (self, _index, show) => {
          if (show) return null;
          const [first, last] = self.series[0].idxs!;
          return isolated.filter((index) => index >= first && index <= last);
        } },
      }],
      axes: [
        {
          stroke: '#9eafbc',
          grid: { stroke: '#202d39' },
          ticks: { stroke: '#354553' },
          values: (self, splits) => {
            const { min, max } = self.scales.x;
            const formatter = (max ?? 0) - (min ?? 0) > 150 * DAY_SECONDS ? axisMonth : axisDate;
            return splits.map((date) => formatter.format(date * 1000));
          },
        },
        {
          side: 1,
          size: 68,
          stroke: '#9eafbc',
          grid: { stroke: '#202d39' },
          ticks: { stroke: '#354553' },
          values: (_self, values) => values.map((value) => priceAxis.format(value)),
        },
      ],
      hooks: { setCursor: [(self) => {
        const index = self.cursor.idx;
        setHover(index == null || (self.cursor.left ?? -1) < 0 ? undefined : dates[index]);
      }] },
    }, [dates, prices], element);
    chart.current = plot;

    function inspectTouch(event: PointerEvent) {
      if (event.pointerType !== 'touch' || !event.isPrimary) return;
      if (event.type === 'pointerdown') event.preventDefault();
      const bounds = plot.over.getBoundingClientRect();
      const left = Math.max(0, Math.min(bounds.width, event.clientX - bounds.left));
      const top = Math.max(0, Math.min(bounds.height, event.clientY - bounds.top));
      plot.setCursor({ left, top });
      if (event.type === 'pointerup' && plot.cursor.idx != null) setPinned(dates[plot.cursor.idx]);
    }

    function cancelTouch(event: PointerEvent) {
      if (event.pointerType === 'touch' && event.isPrimary) plot.setCursor({ left: -10, top: -10 });
    }

    plot.over.addEventListener('pointerdown', inspectTouch);
    plot.over.addEventListener('pointermove', inspectTouch);
    plot.over.addEventListener('pointerup', inspectTouch);
    plot.over.addEventListener('pointercancel', cancelTouch);

    let renderedWidth = width;
    let resizeFrame: number | undefined;
    const observer = new ResizeObserver(([entry]) => {
      const resizedWidth = Math.max(1, Math.floor(entry.contentRect.width));
      if (resizeFrame !== undefined) cancelAnimationFrame(resizeFrame);
      resizeFrame = undefined;
      if (resizedWidth === renderedWidth) return;

      resizeFrame = requestAnimationFrame(() => {
        resizeFrame = undefined;
        renderedWidth = resizedWidth;
        plot.setSize({ width: renderedWidth, height: plotHeight(renderedWidth) });
      });
    });
    observer.observe(element);

    return () => {
      plot.over.removeEventListener('pointerdown', inspectTouch);
      plot.over.removeEventListener('pointermove', inspectTouch);
      plot.over.removeEventListener('pointerup', inspectTouch);
      plot.over.removeEventListener('pointercancel', cancelTouch);
      observer.disconnect();
      if (resizeFrame !== undefined) cancelAnimationFrame(resizeFrame);
      chart.current = null;
      plot.destroy();
    };
  }, [dates, prices, label, lastObserved, isolated]);

  if (lastObserved === undefined) {
    return <output className="chart-empty">No {label} prices are available for this period.</output>;
  }

  const firstDate = dates[0];
  const lastDate = dates[dates.length - 1];

  function inspect(date: string) {
    const seconds = dateToSeconds(date);
    if (!Number.isFinite(seconds) || seconds < firstDate || seconds > lastDate) return;
    setHover(undefined);
    setPinned(seconds);
    if (firstDate < lastDate) {
      chart.current?.setScale('x', {
        min: Math.max(firstDate, seconds - 15 * DAY_SECONDS),
        max: Math.min(lastDate, seconds + 15 * DAY_SECONDS),
      });
    }
  }

  function reset() {
    setHover(undefined);
    setPinned(undefined);
    chart.current?.setCursor({ left: -10, top: -10 });
    chart.current?.setData([dates, prices]);
  }

  return <>
    <div className="chart-reading" aria-live="off">
      <div className="reading-value">
        <span className="reading-price">{price == null ? '—' : typeof price === 'string' ? price : priceReading.format(price)}</span>
        <span className="muted">{asset.economic_unit}</span>
      </div>
      <div className="reading-meta">
        {selected !== undefined && <time dateTime={secondsToDate(selected)}>{formatDate(secondsToDate(selected))}</time>}
        {price == null && <span>No {label} price</span>}
        {reading?.session_status === 'provisional' && <span className="amber">Provisional session</span>}
        {reading?.quality_status === 'partial' && <span className="amber">Partial data</span>}
      </div>
    </div>
    {/* oxlint-disable-next-line jsx-a11y/prefer-tag-over-role -- uPlot needs a container for its canvas. */}
    <div className="plot" ref={container} role="img" aria-label={`Daily ${label} for ${asset.symbol}`} />
    <div className="chart-tools">
      <p className="muted">Gaps indicate missing prices. <span className="mouse-chart-help">Drag to zoom.</span><span className="touch-chart-help">Tap or slide to inspect prices.</span></p>
      <div className="chart-actions">
        <label htmlFor="inspect-date">Inspect date</label>
        <input id="inspect-date" type="date" min={secondsToDate(firstDate)} max={secondsToDate(lastDate)}
          value={pinned === undefined ? '' : secondsToDate(pinned)}
          onChange={(event) => event.target.value ? inspect(event.target.value) : reset()} />
        <button type="button" className="secondary" onClick={reset}>Reset view</button>
      </div>
    </div>
  </>;
}
