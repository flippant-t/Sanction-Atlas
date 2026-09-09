import { json, tierOf, LIMITS } from "./_lib.js";
export async function onRequestGet({ request, env }) {
  const t = await tierOf(request, env);
  if (t.invalid) return json({ error: "unknown or inactive API key" }, 401);
  return json({ tier: t.tier, limits: LIMITS[t.tier], keyed: !!t.key }, 200, { "cache-control": "no-store" });
}
