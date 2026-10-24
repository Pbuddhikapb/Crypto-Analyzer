// scripts/local-cache-proxy.js
// Lightweight in-memory cache proxy for Crypto-Analyzer hybrid (local+VM) mode.
// Listens on port 8009 locally; forwards cache-miss requests to localhost:8008 (SSH tunnel → VM).
// No npm dependencies — uses only Node.js built-in modules.
//
// Usage:
//   node scripts/local-cache-proxy.js
//
// Cache TTLs:
//   /api/market/candles         → 30s   (updates via WS candle events)
//   /api/scanner/opportunities  → 60s   (heavy compute, changes slowly)
//   /api/market/symbols         → 5min  (rarely changes)
//   /api/microstructure/summary → 10s   (whale trades come via WS)
//   /api/portfolio/summary      → 5s    (updates via WS trade events)
//   /api/trade/bracket          → 5s    (price updates via WS)
//   /api/health                 → NO CACHE (always forwarded to VM; never faked)

'use strict';

const http = require('http');

const PORT = 8009;
const VM_HOST = '127.0.0.1';
const VM_PORT = 8008;

// ─── Cache rules ──────────────────────────────────────────────────────────────
const CACHE_RULES = [
  { pattern: /^\/api\/market\/candles/,        ttl: 30_000  }, // 30s
  { pattern: /^\/api\/scanner\/opportunities/, ttl: 60_000  }, // 60s
  { pattern: /^\/api\/market\/symbols/,        ttl: 300_000 }, // 5min
  { pattern: /^\/api\/microstructure\/summary/,ttl: 10_000  }, // 10s
  { pattern: /^\/api\/portfolio\/summary/,     ttl: 5_000   }, // 5s
  { pattern: /^\/api\/trade\/bracket/,         ttl: 5_000   }, // 5s
  { pattern: /^\/api\/market\/depth/,          ttl: 2_000   }, // 2s (order book)
];

/** @type {Map<string, {body: string, ct: string, expires: number, hits: number, status: number}>} */
const cache = new Map();

let totalMisses = 0;
let totalHits   = 0;

// ─── Helpers ──────────────────────────────────────────────────────────────────
function getTTL(url) {
  // /api/health is NEVER cached; always live from upstream VM
  if (/^\/api\/health/.test(url)) return 0;
  for (const rule of CACHE_RULES) {
    if (rule.pattern.test(url)) return rule.ttl;
  }
  return 0; // no caching for unmatched routes
}

function corsHeaders(extra = {}) {
  return {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'GET, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
    'Access-Control-Expose-Headers': 'X-Cache, X-Cache-Age-Ms',
    ...extra,
  };
}

// ─── Forward to VM (cache miss) ───────────────────────────────────────────────
function forwardToVM(path, clientRes) {
  totalMisses++;

  const proxyReq = http.request(
    {
      hostname: VM_HOST,
      port: VM_PORT,
      path,
      method: 'GET',
      headers: { Accept: 'application/json' },
      timeout: 10_000,
    },
    (vmRes) => {
      let body = '';
      vmRes.on('data', (chunk) => (body += chunk));
      vmRes.on('end', () => {
        const ttl = getTTL(path);
        const ct  = vmRes.headers['content-type'] || 'application/json';

        if (ttl > 0 && vmRes.statusCode === 200) {
          cache.set(path, {
            body,
            ct,
            status:   vmRes.statusCode,
            cachedAt: Date.now(),
            expires:  Date.now() + ttl,
            hits:     0,
          });
        }

        clientRes.writeHead(
          vmRes.statusCode,
          corsHeaders({
            'Content-Type': ct,
            'X-Cache': 'MISS',
            'X-Cache-Age-Ms': '0',
          })
        );
        clientRes.end(body);
      });
    }
  );

  proxyReq.on('timeout', () => {
    proxyReq.destroy();
    if (!clientRes.headersSent) {
      clientRes.writeHead(504, corsHeaders({ 'Content-Type': 'application/json' }));
      clientRes.end(JSON.stringify({ error: 'VM request timed out (SSH tunnel may be down)' }));
    }
  });

  proxyReq.on('error', (err) => {
    if (!clientRes.headersSent) {
      clientRes.writeHead(502, corsHeaders({ 'Content-Type': 'application/json' }));
      clientRes.end(
        JSON.stringify({ error: 'VM unreachable via SSH tunnel', detail: err.message,
          hint: 'Run: ssh -L 8008:127.0.0.1:8008 ubuntu@<VM_IP> -N' })
      );
    }
  });

  proxyReq.end();
}

// ─── Server ───────────────────────────────────────────────────────────────────
const server = http.createServer((req, res) => {
  const url = req.url || '/';

  // CORS preflight
  if (req.method === 'OPTIONS') {
    res.writeHead(204, corsHeaders());
    return res.end();
  }

  // Only cache GET requests
  if (req.method !== 'GET') {
    return forwardToVM(url, res);
  }

  // ── Cache lookup ──────────────────────────────────────────────────────────
  const entry = cache.get(url);
  if (entry && Date.now() < entry.expires) {
    totalHits++;
    entry.hits++;
    const ageMs = Math.max(0, Date.now() - (entry.cachedAt || 0));
    res.writeHead(entry.status, corsHeaders({
      'Content-Type':   entry.ct,
      'X-Cache':        `HIT (${entry.hits} hits, ttl ${Math.round((entry.expires - Date.now()) / 1000)}s left)`,
      'X-Cache-Age-Ms': String(ageMs),
    }));
    return res.end(entry.body);
  }

  // ── Cache miss → forward to VM ────────────────────────────────────────────
  forwardToVM(url, res);
});

if (require.main === module) {
  server.listen(PORT, '127.0.0.1', () => {
    console.log('');
    console.log('╔══════════════════════════════════════════════════════╗');
    console.log('║     Crypto-Analyzer Local Cache Proxy  v1.0          ║');
    console.log('╚══════════════════════════════════════════════════════╝');
    console.log(`  Proxy  : http://127.0.0.1:${PORT}`);
    console.log(`  Upstream: http://${VM_HOST}:${VM_PORT}  (SSH tunnel → VM)`);
    console.log(`  Rules   : ${CACHE_RULES.length} cache patterns active`);
    console.log('');
    console.log('  Cache TTLs:');
    console.log('    /api/market/candles         → 30s');
    console.log('    /api/scanner/opportunities  → 60s');
    console.log('    /api/market/symbols         → 5min');
    console.log('    /api/microstructure/summary → 10s');
    console.log('    /api/portfolio/summary      → 5s');
    console.log('    /api/health                 → NO CACHE (always forwarded to VM)');
    console.log('');
    console.log('  Waiting for requests...');
    console.log('');
  });
}

// ── Evict expired cache entries every 60s ─────────────────────────────────────
const gcInterval = setInterval(() => {
  const now  = Date.now();
  let evicted = 0;
  for (const [k, v] of cache) {
    if (now >= v.expires) { cache.delete(k); evicted++; }
  }
  const hitRate = totalHits + totalMisses > 0
    ? ((totalHits / (totalHits + totalMisses)) * 100).toFixed(1)
    : '0.0';
  console.log(`[CacheProxy] GC: evicted=${evicted} live=${cache.size} hits=${totalHits} misses=${totalMisses} hit_rate=${hitRate}%`);
}, 60_000);
if (gcInterval.unref) gcInterval.unref();

// ── Graceful shutdown ─────────────────────────────────────────────────────────
process.on('SIGTERM', () => { server.close(() => process.exit(0)); });
process.on('SIGINT',  () => { server.close(() => process.exit(0)); });

module.exports = {
  server,
  CACHE_RULES,
  getTTL,
  corsHeaders,
  cache,
  PORT,
  VM_HOST,
  VM_PORT,
};
