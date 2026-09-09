// Redirects to the current Stripe payment link from site/config.json at request time,
// so the Subscribe button never depends on a build or a cached page.
export async function onRequestGet({ request, env }) {
  const base = new URL(request.url).origin;
  let link = env.STRIPE_PAYMENT_LINK || "";
  try { const r = await env.ASSETS.fetch(new Request(base + "/config.json")); if (r.ok) { const c = await r.json(); if (c.payment_link) link = c.payment_link; } } catch {}
  if (!link) return new Response("Subscriptions are not open yet.", { status: 503, headers: { "cache-control": "no-store" } });
  return Response.redirect(link, 302);
}
