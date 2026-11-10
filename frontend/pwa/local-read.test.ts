import { expect, it } from 'vitest';
import { allowsLocalRead } from './local-read';

it('permits only macro reads on loopback and keeps them off the LAN proxy', () => {
  for (const path of ['/api/macro/indicators', '/api/macro/indicators/eur-usd/history?start=2026-01-01&end=2026-01-31']) {
    expect(allowsLocalRead(path, false)).toBe(true);
    expect(allowsLocalRead(path, true)).toBe(false);
  }
  for (const path of ['/api/assets', '/api/assets/1/history?start=2026-01-01', '/api/analysis']) {
    expect(allowsLocalRead(path, true)).toBe(false);
    expect(allowsLocalRead(path, false)).toBe(false);
  }
  expect(allowsLocalRead('/api/macro/indicators/unknown/history', false)).toBe(false);
});
