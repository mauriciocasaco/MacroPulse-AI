const marketRead = /^\/api\/assets(?:\/[1-9]\d{0,18}\/history)?(?:\?|$)/;
const macroRead = /^\/api\/macro\/indicators(?:\/(?:eur-usd|m3-stock|deposit-rate|real-gdp-qoq|hicp-yoy)\/history)?(?:\?|$)/;

export function allowsLocalRead(url: string, lanEnabled: boolean): boolean {
  return marketRead.test(url) || (!lanEnabled && macroRead.test(url));
}
