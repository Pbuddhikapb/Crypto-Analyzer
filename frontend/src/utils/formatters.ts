/**
 * Single source of truth for price precision and formatting.
 *
 * Rules:
 * - Reference price >= 1000 : 1 decimal  (e.g., BTC at 81671.7)
 * - Reference price >= 1    : 2 decimals (e.g., SOL at 184.25, XRP at 1.32)
 * - Reference price >= 0.1  : 4 decimals (e.g., ADA at 0.2161)
 * - Else (< 0.1)            : 6 decimals (e.g., DOGE at 0.081234)
 */

export function getPricePrecision(price: number): number {
  const abs = Math.abs(price);
  if (abs >= 1000) return 1;
  if (abs >= 1) return 2;
  if (abs >= 0.1) return 4;
  return 6;
}

export function formatPrice(price: number | null | undefined, referencePrice?: number | null): string {
  if (price === null || price === undefined || isNaN(price)) return '--';
  const ref = (referencePrice !== undefined && referencePrice !== null && !isNaN(referencePrice) && Math.abs(referencePrice) > 0)
    ? referencePrice
    : price;
  const precision = getPricePrecision(ref);
  return price.toFixed(precision);
}

export function getMinMove(referencePrice: number): number {
  const precision = getPricePrecision(referencePrice);
  if (precision === 1) return 0.1;
  if (precision === 2) return 0.01;
  if (precision === 4) return 0.0001;
  return 0.000001;
}

/**
 * Format real UTC clock: HH:mm:ss UTC.
 * Uses Date.getUTCHours(), getUTCMinutes(), getUTCSeconds() or toISOString.
 */
export function formatUTCClock(date: Date | number = new Date()): string {
  const d = typeof date === 'number' ? new Date(date) : date;
  if (isNaN(d.getTime())) return '--:--:-- UTC';
  const hours = String(d.getUTCHours()).padStart(2, '0');
  const minutes = String(d.getUTCMinutes()).padStart(2, '0');
  const seconds = String(d.getUTCSeconds()).padStart(2, '0');
  return `${hours}:${minutes}:${seconds} UTC`;
}

/**
 * Format UTC hours and minutes: HH:MM UTC.
 */
export function formatUTCHM(date: Date | number = new Date()): string {
  const d = typeof date === 'number' ? new Date(date) : date;
  if (isNaN(d.getTime())) return '--:-- UTC';
  const hours = String(d.getUTCHours()).padStart(2, '0');
  const minutes = String(d.getUTCMinutes()).padStart(2, '0');
  return `${hours}:${minutes} UTC`;
}
