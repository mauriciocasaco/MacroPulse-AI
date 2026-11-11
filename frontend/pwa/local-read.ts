const macroRead = /^\/api\/macro\/indicators(?:\/(?:eur-usd|m3-stock|deposit-rate|real-gdp-qoq|hicp-yoy)\/history)?(?:\?|$)/;
export const launcherReadHeader = 'x-marketpulse-local-request';

export function allowsLocalRead(url: string): boolean {
  return macroRead.test(url);
}
