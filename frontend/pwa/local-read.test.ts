import { expect, it } from 'vitest';
import { allowsLocalRead } from './local-read';

it('keeps macro reads off the LAN proxy while preserving legacy LAN reads', () => {
  for (const path of ['/api/macro/indicators', '/api/macro/indicators/eur-usd/history?start=2026-01-01&end=2026-01-31']) {
    expect(allowsLocalRead(path, false)).toBe(true);
    expect(allowsLocalRead(path, true)).toBe(false);
  }
  expect(allowsLocalRead('/api/assets/1/history?start=2026-01-01', true)).toBe(true);
  expect(allowsLocalRead('/api/macro/indicators/unknown/history', false)).toBe(false);
});
