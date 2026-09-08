// Shared helpers for the query endpoints. Runs on Cloudflare Pages Functions.
// The party index is read from the static files the nightly build wrote (via env.ASSETS)
// and cached in the isolate between requests.

let cache = { built: null, index: null, tokens: null, at: 0 };

export function norm(s) {
  return (s || "").normalize("NFKD").replace(/[\u0300-\u036f]/g, "").toUpperCase().replace(/[^A-Z0-9 ]+/g, " ").replace(/\s+/g, " ").trim();
}
const LEGAL = new Set("LLC LTD LIMITED INC CORP CORPORATION CO COMPANY GMBH AG SA SAS SARL BV NV PLC PJSC JSC OJSC CJSC OAO ZAO OOO AO PAO LLP LP SRL SPA PTE PTY PVT FZE FZCO THE OF AND PUBLIC JOINT STOCK OPEN CLOSED".split(" "));
export function tokens(s) { return norm(s).split(" ").filter(t => t && !LEGAL.has(t)); }

export async function loadIndex(env, request) {
  if (cache.index && Date.now() - cache.at < 6 * 3600 * 1000) return cache;
  const base = new URL(request.url).origin;
  const r = await env.ASSETS.fetch(new Request(base + "/api/v1/index.json"));
  if (!r.ok) throw new Error("index unavailable");
  const index = await r.json();
  const tok = new Map();
  index.forEach((p, i) => {
    const seen = new Set();
    for (const name of [p.n, ...(p.alt || [])]) for (const t of tokens(name)) if (t.length > 1 && !seen.has(t)) { seen.add(t); if (!tok.has(t)) tok.set(t, []); tok.get(t).push(i); }
  });
  cache = { index, tokens: tok, at: Date.now() };
  return cache;
}

export async function loadParty(env, request, id) {
  const base = new URL(request.url).origin;
  const sh = await sha1hex(id);
  const r = await env.ASSETS.fetch(new Request(base + "/api/v1/shards/" + sh.slice(0, 2) + ".json"));
  if (!r.ok) return null;
  const recs = await r.json();
  return recs[id] || null;
}
async function sha1hex(s) {
  const buf = await crypto.subtle.digest("SHA-1", new TextEncoder().encode(s));
  return [...new Uint8Array(buf)].map(b => b.toString(16).padStart(2, "0")).join("");
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

// Score a query against a party: best of name and aliases, comparing sorted token strings
// so "IVANOV IVAN" matches "IVAN IVANOV", plus token-overlap so long names with one extra word still score.
// Token-level match: each query token takes its best Jaro-Winkler match in the name; score is the
// average coverage of the query, lightly penalised when the listed name has many extra words.
export function scoreParty(q,p){const qt=tokens(q);if(!qt.length)return 0;let best=0;
  for(const name of [p.n,...(p.alt||[])]){const nt=tokens(name);if(!nt.length)continue;
    let sum=0;for(const a of qt){let m=0;for(const b of nt){const s=a===b?1:jw(a,b);if(s>m)m=s;}sum+=m>=0.8?m:m*0.5;}
    const cov=sum/qt.length, extra=Math.max(0,nt.length-qt.length);
    const s=cov*Math.max(0.8,1-0.04*extra);
    if(s>best)best=s;if(best>=0.999)break;}
  return best;}

export function candidates(cache, q, cap = 400) {
  const qt = tokens(q);
  if (!qt.length) return [];
  // rare tokens first, so "IVANOV" contributes before "IVAN"
  const lists = qt.map(t => cache.tokens.get(t) || []).filter(l => l.length).sort((a, b) => a.length - b.length);
  const seen = new Map();
  for (const l of lists) for (const i of l) { seen.set(i, (seen.get(i) || 0) + 1); }
  return [...seen.entries()].sort((a, b) => b[1] - a[1]).slice(0, cap).map(([i]) => cache.index[i]);
}

export function json(obj, status = 200, extra = {}) {
  return new Response(JSON.stringify(obj), { status, headers: { "content-type": "application/json; charset=utf-8", "access-control-allow-origin": "*", "cache-control": "public, max-age=3600", ...extra } });
}
