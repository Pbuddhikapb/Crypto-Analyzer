import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { computeVersionInfo, formatCacheAgeSeconds } from './compiled/version.js';

const require = createRequire(import.meta.url);
const { getTTL, corsHeaders, CACHE_RULES } = require('../../scripts/local-cache-proxy.js');

describe('Local Cache Proxy Rules', () => {
  test('never caches /api/health and forwards live', () => {
    assert.equal(getTTL('/api/health'), 0);
    assert.equal(getTTL('/api/health?detailed=true'), 0);
  });

  test('preserves intended cache TTLs for heavy endpoints', () => {
    assert.equal(getTTL('/api/scanner/opportunities'), 60_000);
    assert.equal(getTTL('/api/market/candles?symbol=BTCUSDT'), 30_000);
    assert.equal(getTTL('/api/market/symbols'), 300_000);
    assert.equal(getTTL('/api/microstructure/summary?symbol=BTCUSDT'), 10_000);
    assert.equal(getTTL('/api/portfolio/summary'), 5_000);
    assert.equal(getTTL('/api/unknown/route'), 0);
  });

  test('exposes X-Cache-Age-Ms in CORS headers for browser access', () => {
    const headers = corsHeaders();
    assert.ok(headers['Access-Control-Expose-Headers'], 'Missing Access-Control-Expose-Headers');
    assert.ok(
      headers['Access-Control-Expose-Headers'].includes('X-Cache-Age-Ms'),
      'X-Cache-Age-Ms must be exposed'
    );
    assert.ok(
      headers['Access-Control-Expose-Headers'].includes('X-Cache'),
      'X-Cache must be exposed'
    );
  });
});

describe('Version Mismatch and Cache Age', () => {
  test('formats matching versions without mismatch warning', () => {
    const res = computeVersionInfo('9edaaba', '9edaaba');
    assert.equal(res.isMismatch, false);
    assert.equal(res.displayText, 'UI 9edaaba / API 9edaaba');
  });

  test('flags version mismatch when UI and API commits differ', () => {
    const res = computeVersionInfo('abc1234', 'def5678');
    assert.equal(res.isMismatch, true);
    assert.equal(res.displayText, 'UI abc1234 / API def5678 (version mismatch)');
  });

  test('handles unknown commits gracefully without false mismatch', () => {
    const res1 = computeVersionInfo('abc1234', 'unknown');
    assert.equal(res1.isMismatch, false);
    assert.equal(res1.displayText, 'UI abc1234 / API unknown');

    const res2 = computeVersionInfo(undefined, undefined);
    assert.equal(res2.isMismatch, false);
    assert.equal(res2.displayText, 'UI unknown / API unknown');
  });

  test('calculates total cache age in seconds correctly', () => {
    // 15s proxy cache age + 5s client elapsed time = 20s
    const ageSec = formatCacheAgeSeconds(15000, 100000, 105000);
    assert.equal(ageSec, 20);

    // 0s cache miss (fresh) + 4s client elapsed time = 4s
    const freshAge = formatCacheAgeSeconds(0, 100000, 104000);
    assert.equal(freshAge, 4);

    // Initial fetch (0s elapsed)
    assert.equal(formatCacheAgeSeconds(12500, 100000, 100000), 12);
  });
});
