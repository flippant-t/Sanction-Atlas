export async function onRequestGet({ request, env }) {
  const base = new URL(request.url).origin;
  let link = "";
  try { const r = await env.ASSETS.fetch(new Request(base + "/config.json")); if (r.ok) link = (await r.json()).billing_portal || ""; } catch {}
  if (!link) return new Response("Billing portal is not configured.", { status: 503, headers: { "cache-control": "no-store" } });
  return Response.redirect(link, 302);
}
