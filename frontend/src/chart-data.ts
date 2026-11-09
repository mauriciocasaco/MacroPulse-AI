import type { DailyBar } from './api';
import { DAY_SECONDS, dateToSeconds } from './dates';

export type PriceField = 'close' | 'adjusted_close';

export function chartData(bars: DailyBar[], field: PriceField) {
  const dates: number[] = [];
  const prices: (number | null)[] = [];
  const byDate = new Map(bars.map((bar) => [dateToSeconds(bar.bar_date), bar]));
  const datesAvailable = [...byDate.keys()].sort((a, b) => a - b);
  const first = datesAvailable.at(0);
  const last = datesAvailable.at(-1);
  let lastObserved: number | undefined;

  if (first !== undefined && last !== undefined) {
    for (let date = first; date <= last; date += DAY_SECONDS) {
      const price = byDate.get(date)?.[field];
      const value = price == null ? NaN : Number(price);
      dates.push(date);
      if (Number.isFinite(value)) {
        prices.push(value);
        lastObserved = date;
      } else {
        prices.push(null);
      }
    }
  }

  const isolated = prices.flatMap((price, index) =>
    price !== null && prices[index - 1] == null && prices[index + 1] == null ? [index] : []);
  return { dates, prices, byDate, lastObserved, isolated };
}
