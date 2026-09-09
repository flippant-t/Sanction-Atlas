// Stripe webhook: deactivates keys when a subscription ends. Env: STRIPE_WEBHOOK_SECRET, KV binding KEYS.
async function verify(raw, sigHeader, secret) {
  const parts = Object.fromEntries(sigHeader.split(",").map(kv => kv.split("=")));
  const payload = `${parts.t}.${raw}`;
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const mac = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(payload));
  const hex = [...new Uint8Array(mac)].map(b => b.toString(16).padStart(2, "0")).join("");
  return hex === parts.v1 && Math.abs(Date.now() / 1000 - +parts.t) < 600;
}
export async function onRequestPost({ request, env }) {
  const raw = await request.text();
  const sig = request.headers.get("stripe-signature") || "";
  if (!env.STRIPE_WEBHOOK_SECRET || !(await verify(raw, sig, env.STRIPE_WEBHOOK_SECRET))) return new Response("bad signature", { status: 400 });
  const ev = JSON.parse(raw);
  const obj = ev.data?.object || {};
  if (ev.type === "customer.subscription.deleted" || (ev.type === "customer.subscription.updated" && ["canceled", "unpaid", "past_due"].includes(obj.status))) {
    const key = await env.KEYS.get("cust:" + obj.customer);
    if (key) { const rec = await env.KEYS.get("key:" + key, { type: "json" }); if (rec) { rec.active = false; rec.deactivated = new Date().toISOString(); await env.KEYS.put("key:" + key, JSON.stringify(rec)); } }
  }
  if (ev.type === "customer.subscription.updated" && obj.status === "active") {
    const key = await env.KEYS.get("cust:" + obj.customer);
    if (key) { const rec = await env.KEYS.get("key:" + key, { type: "json" }); if (rec) { rec.active = true; await env.KEYS.put("key:" + key, JSON.stringify(rec)); } }
  }
  return new Response("ok");
}
