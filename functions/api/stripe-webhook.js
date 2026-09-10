// Stripe webhook: deactivates and reactivates API keys as subscriptions change.
// Env: STRIPE_WEBHOOK_SECRET, KV binding KEYS.

function timingSafeEqual(a, b) {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

async function verify(raw, sigHeader, secret) {
  // Stripe sends "t=<ts>,v1=<sig>" and may send several v1 values while a signing secret is being rotated,
  // so every v1 has to be checked, not just the last one.
  let ts = null; const sigs = [];
  for (const part of sigHeader.split(",")) {
    const i = part.indexOf("=");
    if (i < 0) continue;
    const k = part.slice(0, i).trim(), v = part.slice(i + 1).trim();
    if (k === "t") ts = v; else if (k === "v1") sigs.push(v);
  }
  if (!ts || !sigs.length) return false;
  if (Math.abs(Date.now() / 1000 - +ts) > 300) return false;      // replay window, as Stripe recommends
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const mac = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(`${ts}.${raw}`));
  const hex = [...new Uint8Array(mac)].map(b => b.toString(16).padStart(2, "0")).join("");
  return sigs.some(s => timingSafeEqual(hex, s.toLowerCase()));
}

async function setActive(env, customer, active) {
  const key = await env.KEYS.get("cust:" + customer);
  if (!key) return;
  const rec = await env.KEYS.get("key:" + key, { type: "json" });
  if (!rec) return;
  rec.active = active;
  if (active) delete rec.deactivated; else rec.deactivated = new Date().toISOString();
  await env.KEYS.put("key:" + key, JSON.stringify(rec));
}

export async function onRequestPost({ request, env }) {
  const raw = await request.text();
  const sig = request.headers.get("stripe-signature") || "";
  if (!env.STRIPE_WEBHOOK_SECRET || !env.KEYS) return new Response("not configured", { status: 503 });
  if (!(await verify(raw, sig, env.STRIPE_WEBHOOK_SECRET))) return new Response("bad signature", { status: 400 });
  let ev; try { ev = JSON.parse(raw); } catch { return new Response("bad body", { status: 400 }); }
  const obj = ev.data?.object || {};
  if (!obj.customer) return new Response("ok");
  if (ev.type === "customer.subscription.deleted" || (ev.type === "customer.subscription.updated" && ["canceled", "unpaid", "past_due", "incomplete_expired"].includes(obj.status))) {
    await setActive(env, obj.customer, false);
  } else if (ev.type === "customer.subscription.updated" && ["active", "trialing"].includes(obj.status)) {
    await setActive(env, obj.customer, true);
  }
  return new Response("ok");
}
