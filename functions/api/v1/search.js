import { candidates, scoreRec, recOut, json, tierOf, LIMITS, guard, preflight } from "./_lib.js";
export async function onRequestGet({ request, env }) {
  const g = guard(request); if (g) return g;
  const u = new URL(request.url);
  const q = (u.searchParams.get("q") || "").trim().slice(0, 200);
  if (q.length < 2) return json({ error: "q must be at least 2 characters" }, 400);
  const t = await tierOf(request, env);
  if (t.invalid) return json({ error: "unknown or inactive API key" }, 401);
  const lim = LIMITS[t.tier];
  const limit = Math.min(lim.search, Math.max(1, +u.searchParams.get("limit") || 20));
  const budget = { left: lim.shards };
  const recs = await candidates(env, request, q, 150, budget);
  const hits = recs.map(r => ({ r, s: scoreRec(q, r) })).filter(x => x.s >= 0.5).sort((a, b) => b.s - a.s).slice(0, limit);
  return json({ query: q, count: hits.length, results: hits.map(({ r, s }) => recOut(r, u.origin, s)) });
}

export const onRequestOptions = () => preflight();
