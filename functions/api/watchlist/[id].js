// One watchlist: read it back, or delete it and everything derived from it.
import { json, tierOf, guard, preflight } from "../_lib.js";

async function pro(request, env) {
  if (!env.KEYS) return { err: json({ error: "watchlists are not configured on this deployment" }, 503) };
  const t = await tierOf(request, env);
  if (t.invalid) return { err: json({ error: "unknown or inactive API key" }, 401) };
  if (t.tier !== "pro") return { err: json({ error: "watchlist monitoring is a Pro feature", subscribe: new URL(request.url).origin + "/api/subscribe" }, 403) };
  return { key: t.key };
}
const clean = id => (decodeURIComponent(id || "").replace(/\.json$/, "").match(/^wl_[0-9a-f]{6,12}$/) || [""])[0];

export async function onRequestGet({ request, env, params }) {
  const g = guard(request); if (g) return g;
  const { key, err } = await pro(request, env); if (err) return err;
  const id = clean(params.id); if (!id) return json({ error: "not found" }, 404);
  const w = await env.KEYS.get(`wl:${key}:${id}`, { type: "json" });
  if (!w) return json({ error: "not found", id }, 404);
  const st = await env.KEYS.get(`wlstate:${key}:${id}`, { type: "json" });
  const matches = st?.matches || {};
  return json({
    id: w.id, label: w.label, threshold: w.threshold, near: !!w.near, names: w.names,
    email: w.email || null, webhook: w.webhook || null, webhook_failing: !!w.webhook_dead,
    created: w.created, updated: w.updated,
    monitored_since: st?.last_run || null,
    baseline_done: !!st?.baseline_done,
    // which of their names currently match something, so the list is useful before any alert fires
    matching: Object.entries(matches).filter(([, v]) => v.length).map(([name, ids]) => ({ name, parties: ids })),
    note: "Matches are by name. Confirm every hit against the official record before acting.",
  }, 200, { "cache-control": "no-store" });
}

export async function onRequestDelete({ request, env, params }) {
  const g = guard(request); if (g) return g;
  const { key, err } = await pro(request, env); if (err) return err;
  const id = clean(params.id); if (!id) return json({ error: "not found" }, 404);
  const w = await env.KEYS.get(`wl:${key}:${id}`, { type: "json" });
  if (!w) return json({ error: "not found", id }, 404);
  // Delete the uploaded names and the match state together. The customer's own retention
  // answer depends on this actually removing the data, so it must not leave state behind.
  await env.KEYS.delete(`wl:${key}:${id}`);
  await env.KEYS.delete(`wlstate:${key}:${id}`);
  return json({ deleted: id, names_removed: (w.names || []).length,
                note: "The uploaded names and their match history are gone. Past alerts stay in /api/v1/alerts until they age out." },
              200, { "cache-control": "no-store" });
}

export const onRequestOptions = () => preflight();
