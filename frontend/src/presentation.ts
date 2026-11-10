import type { Asset, Decimal } from './api';

export const ASSET_PRESENTATION = {
  'BTC-USD': { name: 'Bitcoin', color: '#F7931A' },
  VWO: { name: 'Emerging ETF', color: '#A78BFA' },
  'GC=F': { name: 'Gold', color: '#D4AF37' },
  '^GSPC': { name: 'S&P 500', color: '#7DD3FC' },
  VT: { name: 'Global ETF', color: '#34D399' },
  'CL=F': { name: 'Crude Oil', color: '#F5F5F5' },
} as const;

type Symbol = keyof typeof ASSET_PRESENTATION;

function isKnownSymbol(value: string): value is Symbol {
  return Object.hasOwn(ASSET_PRESENTATION, value);
}

const symbols = Object.keys(ASSET_PRESENTATION).map((value) => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
const symbolInText = new RegExp(`(^|[^\\p{L}\\p{N}_])(${symbols.join('|')})(?![\\p{L}\\p{N}_])`, 'gu');
const dollarPrice = /\$-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?!\d|[.,]\d)/g;

export function assetPresentation({ name, symbol }: Asset): { name: string; color: string } {
  return isKnownSymbol(symbol)
    ? ASSET_PRESENTATION[symbol]
    : { name: name.trim() && name !== symbol ? name : 'Asset', color: '#D4D4D8' };
}

const oneDecimal = { minimumFractionDigits: 1, maximumFractionDigits: 1 } as const;
const price = new Intl.NumberFormat('en-US', oneDecimal);
const percent = new Intl.NumberFormat('en-US', { ...oneDecimal, signDisplay: 'exceptZero' });

export function formatPrice(value: Decimal): string {
  // Modern Intl accepts decimal strings directly, preserving precision through rounding.
  return (price.format as (decimal: Decimal) => string)(value);
}

export function formatAnalysisText(text: string): string {
  return text
    .replace(symbolInText, (_match, prefix: string, symbol: Symbol) => prefix + ASSET_PRESENTATION[symbol].name)
    .replace(dollarPrice, (match) => {
      const value = match.slice(1).replaceAll(',', '');
      return Number.isFinite(Number(value)) ? `$${formatPrice(value)}` : match;
    });
}

// The API supplies percentage points, e.g. 14.2 for +14.2%.
export function formatPercentage(value: number): string {
  return `${percent.format(value)}%`;
}
