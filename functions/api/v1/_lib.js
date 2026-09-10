// Shared helpers for the query endpoints (Cloudflare Pages Functions).
//
// Search used to load a 7.8 MB index and build a token index on every cold start: about 420 ms of CPU,
// against a 10 ms budget on the Workers free plan. It now reads a prefix-sharded inverted index that the
// nightly build writes to /api/v1/search/, so a query parses a few KB and does almost no work.

const isolate = { meta: null, shards: new Map(), at: 0 };
const SHARD_BUDGET = 40;          // the free plan allows 50 subrequests per request; leave headroom
const TTL = 6 * 3600 * 1000;

export function norm(s) {
  return (s || "").normalize("NFKD").replace(/[\u0300-\u036f]/g, "").toUpperCase().replace(/[^A-Z0-9 ]+/g, " ").replace(/\s+/g, " ").trim();
}
let LEGAL = new Set("LLC LTD LIMITED INC CORP CORPORATION CO COMPANY GMBH AG SA SAS SARL BV NV PLC PJSC JSC OJSC CJSC OAO ZAO OOO AO PAO LLP LP SRL SPA PTE PTY PVT FZE FZCO THE OF AND PUBLIC JOINT STOCK OPEN CLOSED".split(" "));
export function tokens(s) { return norm(s).split(" ").filter(t => t.length > 1 && !LEGAL.has(t)); }

async function meta(env, request) {
  if (isolate.meta && Date.now() - isolate.at < TTL) return isolate.meta;
  const base = new URL(request.url).origin;
  const r = await env.ASSETS.fetch(new Request(base + "/api/v1/search/_meta.json"));
  if (!r.ok) throw new Error("search index unavailable");
  const m = await r.json();
  m.set = new Set(m.shards);
  if (m.legal) LEGAL = new Set(m.legal);
  isolate.meta = m; isolate.at = Date.now(); isolate.shards.clear();
  return m;
}

function shardFor(m, token) {
  for (const n of m.prefix_lens) {              // longest prefix first
    const k = token.length >= n ? token.slice(0, n) : token;
    if (m.set.has(k)) return k;
  }
  return null;
}

async function loadShard(env, request, name) {
  if (isolate.shards.has(name)) return isolate.shards.get(name);
  const base = new URL(request.url).origin;
  const r = await env.ASSETS.fetch(new Request(base + "/api/v1/search/" + encodeURIComponent(name.toLowerCase()) + ".json"));
  const data = r.ok ? await r.json() : { r: {}, t: {} };
  isolate.shards.set(name, data);
  return data;
}

// Jaro-Winkler similarity, 0..1
export function jw(a, b) {
  if (a === b) return 1;
  const la = a.length, lb = b.length; if (!la || !lb) return 0;
  const range = Math.max(0, Math.floor(Math.max(la, lb) / 2) - 1);
  const ma = new Array(la).fill(false), mb = new Array(lb).fill(false);
  let m = 0;
  for (let i = 0; i < la; i++) { const lo = Math.max(0, i - range), hi = Math.min(lb - 1, i + range); for (let j = lo; j <= hi; j++) if (!mb[j] && a[i] === b[j]) { ma[i] = mb[j] = true; m++; break; } }
  if (!m) return 0;
  let t = 0, k = 0;
  for (let i = 0; i < la; i++) if (ma[i]) { while (!mb[k]) k++; if (a[i] !== b[k]) t++; k++; }
  const j = (m / la + m / lb + (m - t / 2) / m) / 3;
  let l = 0; while (l < 4 && a[l] === b[l]) l++;
  return j + l * 0.1 * (1 - j);
}

// Each query token takes its best match in the candidate's name; the score is average coverage
// of the query, lightly penalised when the listed name carries many extra words.
export function scoreRec(q, rec) {
  const qt = tokens(q); if (!qt.length) return 0;
  let best = 0;
  for (const name of [rec[1], ...(rec[6] || []).slice(0, 3)]) {
    const nt = tokens(name); if (!nt.length) continue;
    let sum = 0;
    for (const a of qt) { let m = 0; for (const b of nt) { const s = a === b ? 1 : jw(a, b); if (s > m) m = s; } sum += m >= 0.8 ? m : m * 0.5; }
    const cov = sum / qt.length, extra = Math.max(0, nt.length - qt.length);
    const s = cov * Math.max(0.8, 1 - 0.04 * extra);
    if (s > best) best = s;
    if (best >= 0.999) break;
  }
  return best;
}

/** Candidate records for one query string. `budget` is shared across a batch to respect the subrequest cap. */
export async function candidates(env, request, q, cap, budget) {
  const m = await meta(env, request);
  const qt = tokens(q);
  if (!qt.length) return [];
  // Use the most selective tokens for candidate generation. Without this a query like
  // "Central Bank of the Russian Federation" pulls four of the largest shards for nothing.
  const df = m.df || {};
  const ranked = [...new Set(qt)].sort((a, b) => (df[a] || 1) - (df[b] || 1));
  const rare = ranked.filter(t => !df[t]);
  const pick = (rare.length ? rare : ranked).slice(0, 3);
  const wanted = new Map();                      // shard name -> tokens needed from it
  for (const t of pick) {
    const sh = shardFor(m, t);
    if (!sh) continue;
    if (!isolate.shards.has(sh)) {
      if (budget.left <= 0) continue;            // out of subrequests: score against what is already cached
      budget.left--;
    }
    if (!wanted.has(sh)) wanted.set(sh, []);
    wanted.get(sh).push(t);
  }
  if (!wanted.size) return [];
  const names = [...wanted.keys()];
  const loaded = await Promise.all(names.map(n => loadShard(env, request, n)));
  const postings = [];
  names.forEach((n, i) => {
    const data = loaded[i];
    for (const t of wanted.get(n)) { const ids = data.t[t]; if (ids && ids.length) postings.push({ ids, data }); }
  });
  postings.sort((a, b) => a.ids.length - b.ids.length);   // rarest tokens first
  const hits = new Map();
  for (const { ids, data } of postings) for (const id of ids) {
    const cur = hits.get(id);
    if (cur) cur.n++; else hits.set(id, { rec: data.r[id], n: 1 });
  }
  return [...hits.values()].filter(h => h.rec).sort((a, b) => b.n - a.n).slice(0, cap).map(h => h.rec);
}

export const recOut = (rec, origin, score) => ({
  id: rec[0], name: rec[1], type: rec[2], country: rec[3], authorities: rec[4], programs: rec[5],
  score: +score.toFixed(3), url: origin + "/api/v1/party/" + encodeURIComponent(rec[0]), map: origin + "/#p=" + encodeURIComponent(rec[0]),
});

// ---- cheap in-isolate flood guard.
// This deliberately lives in the route handlers, not in a _middleware.js: middleware under /api/v1/
// would intercept the static JSON files there too, turning every index shard fetch into a billed
// Function invocation. Isolates are per-colo and short-lived, so this only stops a naive loop from one
// client; the real protection is the Cloudflare rate-limiting rule on /api/* described in the README.
const hits = new Map();
const WINDOW = 60_000, MAX = 120;
export function guard(request) {
  const ip = request.headers.get("cf-connecting-ip");
  if (!ip) return null;
  const now = Date.now();
  if (hits.size > 5000) hits.clear();
  let s = hits.get(ip);
  if (!s || now > s.until) { s = { n: 0, until: now + WINDOW }; hits.set(ip, s); }
  if (++s.n > MAX) {
    const retry = Math.ceil((s.until - now) / 1000);
    return json({ error: "too many requests", retry_after_seconds: retry }, 429, { "retry-after": String(retry), "cache-control": "no-store" });
  }
  return null;
}
export const preflight = () => new Response(null, { status: 204, headers: {
  "access-control-allow-origin": "*", "access-control-allow-methods": "GET, POST, OPTIONS",
  "access-control-allow-headers": "content-type, x-api-key, authorization", "access-control-max-age": "86400" } });

export function json(obj, status = 200, extra = {}) {
  return new Response(JSON.stringify(obj), { status, headers: { "content-type": "application/json; charset=utf-8", "access-control-allow-origin": "*", "cache-control": "public, max-age=3600", ...extra } });
}

// ---- API keys (Pro tier). Keys live in the KEYS KV namespace: key:<key> -> {tier, email, customer, active}
const KEY_SHAPE = /^ss_[0-9a-f]{48}$/;
export function keyFrom(request) {
  const h = request.headers.get("x-api-key") || request.headers.get("authorization")?.replace(/^Bearer\s+/i, "");
  if (h) return h.trim();
  return new URL(request.url).searchParams.get("key") || "";
}
export async function tierOf(request, env) {
  const key = keyFrom(request);
  if (!key) return { tier: "free", key: null };
  // Reject malformed keys before touching KV, so junk traffic cannot burn the KV read quota.
  if (!KEY_SHAPE.test(key)) return { tier: "free", key: null, invalid: true };
  if (!env.KEYS) return { tier: "free", key: null };
  const rec = await env.KEYS.get("key:" + key, { type: "json" });
  if (!rec || rec.active === false) return { tier: "free", key: null, invalid: true };
  return { tier: rec.tier || "pro", key, email: rec.email };
}
export const LIMITS = {
  free: { screen: 50, candidates: 60, search: 20, shards: SHARD_BUDGET },
  pro:  { screen: 200, candidates: 200, search: 100, shards: SHARD_BUDGET },
};

// ---- single party lookup, from the id-hash shards the build writes
async function sha1hex(s) {
  const buf = await crypto.subtle.digest("SHA-1", new TextEncoder().encode(s));
  return [...new Uint8Array(buf)].map(b => b.toString(16).padStart(2, "0")).join("");
}
const partyShards = new Map();
export async function loadParty(env, request, id) {
  const base = new URL(request.url).origin;
  const sh = (await sha1hex(id)).slice(0, 2);
  if (!partyShards.has(sh)) {
    const r = await env.ASSETS.fetch(new Request(base + "/api/v1/shards/" + sh + ".json"));
    partyShards.set(sh, r.ok ? await r.json() : {});
  }
  return partyShards.get(sh)[id] || null;
}
