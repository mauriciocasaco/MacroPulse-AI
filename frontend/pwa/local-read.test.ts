import { expect, it } from 'vitest';
import { allowsLocalRead } from './local-read';

it('permits the macro catalog and the five official histories', () => {
  expect(allowsLocalRead('/api/macro/indicators')).toBe(true);
  for (const id of ['eur-usd', 'm3-stock', 'deposit-rate', 'real-gdp-qoq', 'hicp-yoy']) {
    expect(allowsLocalRead(`/api/macro/indicators/${id}/history?start=2026-01-01&end=2026-01-31`)).toBe(true);
  }
  for (const path of ['/api/assets', '/api/assets/1/history?start=2026-01-01', '/api/analysis', '/api/macro/indicators/unknown/history']) {
    expect(allowsLocalRead(path)).toBe(false);
  }
});
