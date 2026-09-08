import { loadIndex, candidates, scoreParty, json } from "./_lib.js";
export async function onRequestPost({ request, env }) {
  let body; try { body = await request.json(); } catch { return json({ error: "send JSON: {\"names\": [...], \"threshold\": 0.85}" }, 400); }
  const names = Array.isArray(body.names) ? body.names.map(s => String(s).trim()).filter(Boolean).slice(0, 100) : [];
  if (!names.length) return json({ error: "names[] is required (max 100 per request; use the in-browser screener at /screen.html for larger lists)" }, 400);
  const threshold = Math.min(1, Math.max(0.5, +body.threshold || 0.85));
  const cache = await loadIndex(env, request);
  const u = new URL(request.url);
  const out = names.map(name => {
    const hits = candidates(cache, name, 60).map(p => ({ p, score: scoreParty(name, p) })).filter(x => x.score >= threshold).sort((a, b) => b.score - a.score).slice(0, 5);
    return { name, matches: hits.map(({ p, score }) => ({ id: p.id, name: p.n, type: p.t, country: p.cc, authorities: p.au, programs: p.p, score: +score.toFixed(3), url: u.origin + "/api/v1/party/" + encodeURIComponent(p.id) })) };
  });
  return json({ threshold, screened: names.length, flagged: out.filter(r => r.matches.length).length, results: out, note: "Name matching only. Confirm every hit against the official record before acting." }, 200, { "cache-control": "no-store" });
}
export async function onRequestGet() { return json({ error: "POST a JSON body: {\"names\": [\"...\"], \"threshold\": 0.85}" }, 405); }
