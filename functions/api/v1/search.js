import { loadIndex, candidates, scoreParty, json, tierOf, LIMITS } from "./_lib.js";
export async function onRequestGet({ request, env }) {
  const u = new URL(request.url);
  const q = (u.searchParams.get("q") || "").trim();
  const t = await tierOf(request, env);
  const limit = Math.min(LIMITS[t.tier].search, Math.max(1, +u.searchParams.get("limit") || 20));
  if (q.length < 2) return json({ error: "q must be at least 2 characters" }, 400);
  const cache = await loadIndex(env, request);
  const hits = candidates(cache, q).map(p => ({ p, score: scoreParty(q, p) })).filter(x => x.score >= 0.5).sort((a, b) => b.score - a.score).slice(0, limit);
  return json({ query: q, count: hits.length, results: hits.map(({ p, score }) => ({ id: p.id, name: p.n, type: p.t, country: p.cc, city: p.city, authorities: p.au, programs: p.p, score: +score.toFixed(3), url: u.origin + "/api/v1/party/" + encodeURIComponent(p.id), map: u.origin + "/#p=" + encodeURIComponent(p.id) })) });
}
