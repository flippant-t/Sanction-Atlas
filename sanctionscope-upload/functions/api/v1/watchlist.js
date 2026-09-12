// Watchlists for the Pro tier. Create a list of counterparty names, get told when any of
// them is listed, delisted, linked to a listed party or picked up by another authority.
//
// This endpoint only stores the list. The matching runs in the nightly build
// (pipeline/monitor.py), because 10 ms of CPU and 50 subrequests cannot match thousands of
// names against 37,000 parties. So a create returns "queued", not results; /api/v1/screen
// is the endpoint for an answer right now.
import { json, tierOf, guard, preflight } from "./_lib.js";

const MAX_LISTS = 10, MAX_NAMES = 5000, MAX_NAME_LEN = 200, MAX_LABEL = 80;

const rand = (n, p) => { const b = new Uint8Array(n); crypto.getRandomValues(b); return p + [...b].map(x => x.toString(16).padStart(2, "0")).join(""); };

async function pro(request, env) {
  if (!env.KEYS) return { err: json({ error: "watchlists are not configured on this deployment" }, 503) };
  const t = await tierOf(request, env);
  if (t.invalid) return { err: json({ error: "unknown or inactive API key" }, 401) };
  if (t.tier !== "pro") return { err: json({
    error: "watchlist monitoring is a Pro feature",
    hint: "Screening stays free and unlimited at /api/v1/screen and /screen.html.",
    subscribe: new URL(request.url).origin + "/api/subscribe",
  }, 403) };
  return { key: t.key };
}

// A webhook must be a public https URL. Without this check the endpoint is an open port
// scanner: anyone could point it at 169.254.169.254 or a 10.x host and read the response.
function badWebhook(u) {
  if (!u) return null;
  let url; try { url = new URL(u); } catch { return "webhook must be a valid URL"; }
  if (url.protocol !== "https:") return "webhook must be https";
  const h = url.hostname.toLowerCase();
  if (h === "localhost" || h.endsWith(".localhost") || h.endsWith(".internal") ||
      /^(\[|::1|0\.|127\.|10\.|192\.168\.|169\.254\.|172\.(1[6-9]|2\d|3[01])\.)/.test(h))
    return "webhook must be a public address";
  return null;
}

export async function onRequestPost({ request, env }) {
  const g = guard(request); if (g) return g;
  const { key, err } = await pro(request, env); if (err) return err;

  let body; try { body = await request.json(); } catch { return json({ error: 'send JSON: {"label":"...","names":["..."]}' }, 400); }
  const names = [...new Set((Array.isArray(body.names) ? body.names : [])
    .map(s => String(s).trim().slice(0, MAX_NAME_LEN)).filter(s => s.length > 1))];
  if (!names.length) return json({ error: "names[] is required" }, 400);
  if (names.length > MAX_NAMES) return json({ error: `a list holds at most ${MAX_NAMES} names`, max: MAX_NAMES }, 413);

  const wh = badWebhook(body.webhook);
  if (wh) return json({ error: wh }, 400);
  const email = body.email ? String(body.email).trim().slice(0, 200) : "";
  if (email && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) return json({ error: "email is not a valid address" }, 400);

  const existing = await env.KEYS.list({ prefix: `wl:${key}:` });
  const id = body.id && /^wl_[0-9a-f]{6,12}$/.test(body.id) ? body.id : rand(3, "wl_");
  const replacing = existing.keys.some(k => k.name === `wl:${key}:${id}`);
  if (!replacing && existing.keys.length >= MAX_LISTS)
    return json({ error: `at most ${MAX_LISTS} lists per key; delete one first`, max: MAX_LISTS }, 409);

  const prev = replacing ? await env.KEYS.get(`wl:${key}:${id}`, { type: "json" }) : null;
  const rec = {
    id, label: String(body.label || "Watchlist").slice(0, MAX_LABEL), names,
    threshold: Math.min(1, Math.max(0.5, +body.threshold || 0.85)),
    near: !!body.near, email, webhook: body.webhook || "",
    webhook_secret: prev?.webhook_secret || (body.webhook ? rand(16, "whsec_") : ""),
    created: prev?.created || new Date().toISOString(), updated: new Date().toISOString(),
    baseline_done: false, active: true,
  };
  // Replacing the names resets the baseline, so the customer gets a fresh summary instead of
  // an alert for every name they just added.
  await env.KEYS.put(`wl:${key}:${id}`, JSON.stringify(rec));
  if (replacing) await env.KEYS.delete(`wlstate:${key}:${id}`);

  return json({
    id, label: rec.label, names: names.length, threshold: rec.threshold, near: rec.near,
    ...(rec.webhook_secret ? { webhook_secret: rec.webhook_secret } : {}),
    baseline: "queued",
    note: "Current matches arrive in the first report, within 24 hours. For an answer now, POST the same names to /api/v1/screen.",
  }, replacing ? 200 : 201, { "cache-control": "no-store" });
}

export async function onRequestGet({ request, env }) {
  const g = guard(request); if (g) return g;
  const { key, err } = await pro(request, env); if (err) return err;
  const ls = await env.KEYS.list({ prefix: `wl:${key}:` });
  const out = [];
  for (const k of ls.keys) {
    const w = await env.KEYS.get(k.name, { type: "json" });
    if (!w) continue;
    const st = await env.KEYS.get(`wlstate:${key}:${w.id}`, { type: "json" });
    const matching = st ? Object.values(st.matches || {}).filter(v => v.length).length : null;
    out.push({
      id: w.id, label: w.label, names: (w.names || []).length, threshold: w.threshold, near: !!w.near,
      email: w.email || null, webhook: w.webhook || null, webhook_failing: !!w.webhook_dead,
      created: w.created, updated: w.updated,
      monitored_since: st?.last_run || null, currently_matching: matching,
    });
  }
  return json({ count: out.length, lists: out }, 200, { "cache-control": "no-store" });
}

export const onRequestOptions = () => preflight();
