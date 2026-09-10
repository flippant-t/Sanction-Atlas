import { candidates, scoreRec, recOut, json, tierOf, LIMITS, guard, preflight } from "./_lib.js";
export async function onRequestPost({ request, env }) {
  const g = guard(request); if (g) return g;
  let body; try { body = await request.json(); } catch { return json({ error: 'send JSON: {"names": [...], "threshold": 0.85}' }, 400); }
  const t = await tierOf(request, env);
  if (t.invalid) return json({ error: "unknown or inactive API key" }, 401);
  const lim = LIMITS[t.tier];
  const all = Array.isArray(body.names) ? body.names.map(s => String(s).trim().slice(0, 200)).filter(Boolean) : [];
  if (!all.length) return json({ error: "names[] is required" }, 400);
  if (all.length > lim.screen) return json({
    error: `this ${t.tier} tier allows ${lim.screen} names per request`,
    max: lim.screen,
    hint: "Split the batch, or use the in-browser screener at /screen.html, which runs on your own machine and has no limit.",
  }, 413);
  const threshold = Math.min(1, Math.max(0.5, +body.threshold || 0.85));
  const budget = { left: lim.shards };
  const u = new URL(request.url);
  const out = [];
  for (const name of all) {
    const recs = await candidates(env, request, name, lim.candidates, budget);
    const hits = recs.map(r => ({ r, s: scoreRec(name, r) })).filter(x => x.s >= threshold).sort((a, b) => b.s - a.s).slice(0, 5);
    out.push({ name, matches: hits.map(({ r, s }) => recOut(r, u.origin, s)) });
  }
  const partial = budget.left <= 0;
  return json({
    tier: t.tier, threshold, screened: all.length, flagged: out.filter(r => r.matches.length).length, results: out,
    ...(partial ? { partial: true, warning: "This batch needed more index shards than one request may load. Some names were matched against a reduced candidate set; split the batch or use the in-browser screener for a complete result." } : {}),
    note: "Name matching only. Confirm every hit against the official record before acting.",
  }, 200, { "cache-control": "no-store" });
}
export async function onRequestGet() { return json({ error: 'POST a JSON body: {"names": ["..."], "threshold": 0.85}' }, 405); }

export const onRequestOptions = () => preflight();
