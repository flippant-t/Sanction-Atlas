// The alert feed. One KV read, no matching: the nightly build already did that work.
import { json, tierOf, guard, preflight } from "./_lib.js";

const TYPES = new Set(["added", "removed", "linked", "changed", "near", "baseline"]);

export async function onRequestGet({ request, env }) {
  const g = guard(request); if (g) return g;
  if (!env.KEYS) return json({ error: "alerts are not configured on this deployment" }, 503);
  const t = await tierOf(request, env);
  if (t.invalid) return json({ error: "unknown or inactive API key" }, 401);
  if (t.tier !== "pro") return json({ error: "alerts are a Pro feature", subscribe: new URL(request.url).origin + "/api/subscribe" }, 403);

  const u = new URL(request.url);
  const since = (u.searchParams.get("since") || "").slice(0, 10);
  const type = (u.searchParams.get("type") || "").toLowerCase();
  const list = (u.searchParams.get("list_id") || "").slice(0, 20);
  const limit = Math.min(200, Math.max(1, +u.searchParams.get("limit") || 50));
  if (since && !/^\d{4}-\d{2}-\d{2}$/.test(since)) return json({ error: "since must be YYYY-MM-DD" }, 400);
  if (type && !TYPES.has(type)) return json({ error: "unknown type", types: [...TYPES] }, 400);

  const feed = await env.KEYS.get(`alerts:${t.key}`, { type: "json" });
  let alerts = feed?.alerts || [];
  if (since) alerts = alerts.filter(a => a.date >= since);
  if (type) alerts = alerts.filter(a => a.type === type);
  if (list) alerts = alerts.filter(a => a.list_id === list);

  return json({
    count: alerts.length, returned: Math.min(alerts.length, limit),
    generated: feed?.generated || null, alerts: alerts.slice(0, limit),
    note: "Name matching only. Confirm every hit against the official record before acting.",
  }, 200, { "cache-control": "no-store" });
}

export const onRequestOptions = () => preflight();
