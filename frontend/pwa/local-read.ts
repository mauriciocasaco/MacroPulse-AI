const macroRead = /^\/api\/macro\/indicators(?:\/(?:eur-usd|m3-stock|deposit-rate|real-gdp-qoq|hicp-yoy)\/history)?(?:\?|$)/;

export function allowsLocalRead(url: string, lanEnabled: boolean): boolean {
  return !lanEnabled && macroRead.test(url);
}
