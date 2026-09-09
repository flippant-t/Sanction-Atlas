import { loadIndex, candidates, scoreParty, json, tierOf, LIMITS } from "./_lib.js";
export async function onRequestPost({ request, env }) {
  let body; try { body = await request.json(); } catch { return json({ error: "send JSON: {\"names\": [...], \"threshold\": 0.85}" }, 400); }
  const t = await tierOf(request, env), lim = LIMITS[t.tier];
  if (t.invalid) return json({ error: "unknown or inactive API key" }, 401);
  const all = Array.isArray(body.names) ? body.names.map(s => String(s).trim()).filter(Boolean) : [];
  if (!all.length) return json({ error: "names[] is required" }, 400);
  if (all.length > lim.screen) return json({ error: `this ${t.tier} tier allows ${lim.screen} names per request` + (t.tier === "free" ? "; the Pro tier allows 500, and the in-browser screener at /screen.html has no limit" : ""), max: lim.screen }, 413);
  const threshold = Math.min(1, Math.max(0.5, +body.threshold || 0.85));
  const cache = await loadIndex(env, request);
  const u = new URL(request.url);
  const out = all.map(name => {
    const hits = candidates(cache, name, lim.candidates).map(p => ({ p, score: scoreParty(name, p) })).filter(x => x.score >= threshold).sort((a, b) => b.score - a.score).slice(0, 5);
    return { name, matches: hits.map(({ p, score }) => ({ id: p.id, name: p.n, type: p.t, country: p.cc, authorities: p.au, programs: p.p, score: +score.toFixed(3), url: u.origin + "/api/v1/party/" + encodeURIComponent(p.id) })) };
  });
  return json({ tier: t.tier, threshold, screened: all.length, flagged: out.filter(r => r.matches.length).length, results: out, note: "Name matching only. Confirm every hit against the official record before acting." }, 200, { "cache-control": "no-store" });
}
export async function onRequestGet() { return json({ error: "POST a JSON body: {\"names\": [\"...\"], \"threshold\": 0.85}" }, 405); }
