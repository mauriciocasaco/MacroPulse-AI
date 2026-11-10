import { useLayoutEffect, useMemo, useRef, useState, type CSSProperties } from 'react';
import uPlot from 'uplot';
import 'uplot/dist/uPlot.min.css';
import type { Asset, DailyBar } from './api';
import { chartData, priceDomain, sessionDomain, sessionLabels, sessionTicks, type PriceField } from './chart-data';
import { formatDate } from './dates';
import { assetPresentation, formatPrice } from './presentation';

const axisFont = '11px system-ui, sans-serif';
const axisStyle = {
  font: axisFont, stroke: '#B1B4BB', gap: 5,
  grid: { stroke: '#292B30' }, ticks: { stroke: '#494B51', size: 5 },
};
const plotHeight = (width: number) => width < 600 ? 264 : 342;

interface Props {
  asset: Asset;
  bars: DailyBar[];
  field: PriceField;
}

export function PriceChart({ asset, bars, field }: Props) {
  const container = useRef<HTMLDivElement>(null);
  const chart = useRef<uPlot | null>(null);
  const inspectFrame = useRef<number | undefined>(undefined);
  const [hover, setHover] = useState<number>();
  const [inspectedDate, setInspectedDate] = useState('');
  const { indices, dates, prices, observations, indexByDate, firstDate, lastDate } = useMemo(() => chartData(bars, field), [bars, field]);
  const inspectedIndex = indexByDate.get(inspectedDate);
  const activeIndex = hover ?? (inspectedDate ? inspectedIndex : observations.length - 1);
  const reading = activeIndex == null ? undefined : observations[activeIndex];
  const price = reading?.[field];
  const label = field === 'close' ? 'Close' : 'Adj Close';
  const { name, color } = assetPresentation(asset);

  useLayoutEffect(() => {
    const element = container.current;
    if (!element || prices.length === 0) return;

    let renderedWidth = 0;
    let resizeFrame: number | undefined;

    function inspectTouch(event: PointerEvent) {
      const plot = chart.current;
      if (!plot || event.pointerType !== 'touch' || !event.isPrimary) return;
      if (event.type === 'pointerdown') event.preventDefault();
      const bounds = plot.over.getBoundingClientRect();
      const left = Math.max(0, Math.min(bounds.width, event.clientX - bounds.left));
      const top = Math.max(0, Math.min(bounds.height, event.clientY - bounds.top));
      plot.setCursor({ left, top });
      if (event.type === 'pointerup' && plot.cursor.idx != null) {
        setInspectedDate(observations[plot.cursor.idx].bar_date);
      }
    }

    function cancelTouch(event: PointerEvent) {
      if (event.pointerType === 'touch' && event.isPrimary) chart.current?.setCursor({ left: -10, top: -10 });
    }

    function render(width: number) {
      if (width <= 0 || width === renderedWidth) return;
      renderedWidth = width;
      const height = plotHeight(width);
      if (chart.current) {
        chart.current.setSize({ width, height });
        return;
      }

      const plot = new uPlot({
        width, height,
        legend: { show: false },
        padding: [9, 0, 0, 38],
        cursor: {
          drag: { x: true, y: false },
          points: { size: 8, width: 1, stroke: '#111214', fill: color },
          move: (self, left, top) => {
            if (left < 0 || top < 0 || self.scales.x.min == null) return [left, top];
            const index = self.posToIdx(left);
            return [self.valToPos(indices[index], 'x'), self.valToPos(prices[index], 'y')];
          },
        },
        scales: {
          x: { time: false, range: (_self, min, max) => sessionDomain(min, max) },
          y: { range: (_self, min, max) => priceDomain(min, max) },
        },
        series: [{}, {
          label: name,
          stroke: color,
          width: 2,
          points: { show: prices.length === 1 ? true : undefined, size: 6 },
        }],
        axes: [
          {
            ...axisStyle, size: 30, space: 40,
            splits: (self, _index, min, max) => sessionTicks(min, max, self.bbox.width / uPlot.pxRatio, dates.length - 1),
            values: (_self, ticks) => sessionLabels(ticks, dates),
          },
          {
            ...axisStyle, side: 1, space: 35,
            size: (self, values) => {
              self.ctx.save();
              self.ctx.font = axisFont;
              const widest = Math.max(0, ...(values ?? []).map((value) => self.ctx.measureText(value).width));
              self.ctx.restore();
              return Math.max(44, widest + 15);
            },
            values: (_self, ticks) => {
              let previous = '';
              return ticks.map((tick) => {
                const value = formatPrice(tick);
                if (value === previous) return '';
                previous = value;
                return value;
              });
            },
          },
        ],
        hooks: { setCursor: [(self) => {
          const { idx, left } = self.cursor;
          setHover(idx == null || (left ?? -1) < 0 ? undefined : idx);
        }] },
      }, [indices, prices], element!);
      chart.current = plot;
      plot.over.addEventListener('pointerdown', inspectTouch);
      plot.over.addEventListener('pointermove', inspectTouch);
      plot.over.addEventListener('pointerup', inspectTouch);
      plot.over.addEventListener('pointercancel', cancelTouch);
    }

    // Observe the host, whose width is independent of the canvas. uPlot manages the backing DPR.
    render(Math.floor(element.getBoundingClientRect().width));
    const observer = new ResizeObserver(([entry]) => {
      const width = Math.floor(entry.contentRect.width);
      if (resizeFrame !== undefined) cancelAnimationFrame(resizeFrame);
      resizeFrame = undefined;
      if (width <= 0 || width === renderedWidth) return;
      resizeFrame = requestAnimationFrame(() => {
        resizeFrame = undefined;
        render(width);
      });
    });
    observer.observe(element);

    return () => {
      observer.disconnect();
      if (resizeFrame !== undefined) cancelAnimationFrame(resizeFrame);
      if (inspectFrame.current !== undefined) cancelAnimationFrame(inspectFrame.current);
      const plot = chart.current;
      if (!plot) return;
      plot.over.removeEventListener('pointerdown', inspectTouch);
      plot.over.removeEventListener('pointermove', inspectTouch);
      plot.over.removeEventListener('pointerup', inspectTouch);
      plot.over.removeEventListener('pointercancel', cancelTouch);
      chart.current = null;
      plot.destroy();
    };
  }, [color, dates, indices, name, observations, prices]);

  if (observations.length === 0) {
    return <output className="chart-empty">No {label} prices are available for this period.</output>;
  }

  function inspect(date: string) {
    setInspectedDate(date);
    setHover(undefined);
    if (inspectFrame.current !== undefined) cancelAnimationFrame(inspectFrame.current);
    inspectFrame.current = undefined;
    const plot = chart.current;
    if (!plot) return;
    plot.setCursor({ left: -10, top: -10 });
    const index = indexByDate.get(date);
    if (index == null) {
      if (!date) plot.setData([indices, prices]);
      return;
    }
    plot.setScale('x', { min: Math.max(0, index - 15), max: Math.min(indices.length - 1, index + 15) });
    inspectFrame.current = requestAnimationFrame(() => {
      inspectFrame.current = undefined;
      plot.setCursor({ left: plot.valToPos(index, 'x'), top: plot.valToPos(prices[index], 'y') });
    });
  }

  return <>
    <div className="chart-reading" aria-live="off">
      <div className="reading-value">
        <span className="reading-price">{price == null ? '—' : formatPrice(price)}</span>
        <span className="muted">{asset.economic_unit}</span>
      </div>
      <div className="reading-meta">
        {reading && <time dateTime={reading.bar_date}>{formatDate(reading.bar_date)}</time>}
        {reading?.session_status === 'provisional' && <span className="amber">Provisional session</span>}
        {reading?.quality_status === 'partial' && <span className="amber">Partial data</span>}
        {!reading && <output>
          {inspectedIndex === null ? `No ${label} price is available for this date.` : 'No observation is available for this date.'}
        </output>}
      </div>
    </div>
    {/* oxlint-disable-next-line jsx-a11y/prefer-tag-over-role -- uPlot needs a container for its canvas. */}
    <div className="plot" ref={container} role="img" aria-label={`Daily ${label} for ${name}`}
      style={{ '--series-color': color } as CSSProperties} />
    <div className="chart-tools">
      <p className="muted"><span className="mouse-chart-help">Drag to zoom.</span><span className="touch-chart-help">Tap or slide to inspect prices.</span></p>
      <div className="chart-actions">
        <label htmlFor="inspect-date">Inspect date</label>
        <input id="inspect-date" type="date" min={firstDate} max={lastDate} value={inspectedDate}
          onChange={(event) => inspect(event.target.value)} />
        <button type="button" className="secondary" onClick={() => inspect('')}>Reset view</button>
      </div>
    </div>
  </>;
}
