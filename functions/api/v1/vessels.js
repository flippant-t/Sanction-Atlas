// Last known AIS positions for sanctioned vessels, from the KV the collector writes. Free tier only.
import { json, guard, preflight } from "./_lib.js";
export async function onRequestGet({ request, env }) {
  const g = guard(request); if (g) return g;
  if (!env.KEYS) return json({ error: "vessel tracking not configured" }, 503);
  const [positions, meta] = await Promise.all([env.KEYS.get("ais:positions", { type: "json" }), env.KEYS.get("ais:meta", { type: "json" })]);
  return json({ meta: meta || {}, positions: positions || {}, note: "AIS via aisstream.io, terrestrial coverage only. Sanctioned vessels often disable or spoof AIS; absence of a position means nothing on its own." }, 200, { "cache-control": "public, max-age=120" });
}

export const onRequestOptions = () => preflight();
