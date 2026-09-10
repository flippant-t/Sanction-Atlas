import { loadParty, json, guard, preflight } from "../_lib.js";
export async function onRequestGet({ request, env, params }) {
  const g = guard(request); if (g) return g;
  const id = decodeURIComponent(params.id || "").replace(/\.json$/, "");
  if (!id) return json({ error: "id required" }, 400);
  const p = await loadParty(env, request, id);
  if (!p) return json({ error: "not found", id }, 404);
  return json(p);
}

export const onRequestOptions = () => preflight();
