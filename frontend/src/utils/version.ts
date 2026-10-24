export interface VersionInfo {
  uiCommit: string;
  apiCommit: string;
  isMismatch: boolean;
  displayText: string;
}

export function computeVersionInfo(uiCommit?: string, apiCommit?: string): VersionInfo {
  const ui = uiCommit && uiCommit.trim() ? uiCommit.trim() : 'unknown';
  const api = apiCommit && apiCommit.trim() ? apiCommit.trim() : 'unknown';
  const isMismatch = ui !== 'unknown' && api !== 'unknown' && ui !== api;
  const displayText = isMismatch
    ? `UI ${ui} / API ${api} (version mismatch)`
    : `UI ${ui} / API ${api}`;
  return {
    uiCommit: ui,
    apiCommit: api,
    isMismatch,
    displayText,
  };
}

export function formatCacheAgeSeconds(cacheAgeMs: number, fetchedAt: number, now: number = Date.now()): number {
  const totalAgeMs = (now - fetchedAt) + Math.max(0, cacheAgeMs);
  return Math.max(0, Math.floor(totalAgeMs / 1000));
}
