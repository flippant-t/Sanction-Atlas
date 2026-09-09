// Stripe Checkout success page: verifies the session, issues (or re-shows) the API key.
// Env: STRIPE_SECRET (sk_live_...), KV binding KEYS.
function page(title, body) {
  return new Response(`<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${title} · SanctionScope</title>
<style>body{margin:0;background:#0e1726;color:#e6e1d6;font-family:"Iowan Old Style","Palatino Linotype",Palatino,Georgia,serif;font-size:15px;line-height:1.55}main{max-width:720px;margin:0 auto;padding:40px 20px}h1{font-weight:400;font-size:26px}code{display:block;background:#0b1321;border:1px solid #26344a;border-radius:4px;padding:12px;font:15px ui-monospace,Menlo,Consolas,monospace;word-break:break-all;margin:14px 0}a{color:#e9b44c}.sub{color:#a39d90}</style></head><body><main>${body}</main></body></html>`,
    { headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" } });
}
function randKey() { const b = new Uint8Array(24); crypto.getRandomValues(b); return "ss_" + [...b].map(x => x.toString(16).padStart(2, "0")).join(""); }
export async function onRequestGet({ request, env }) {
  const sid = new URL(request.url).searchParams.get("session_id");
  if (!sid) return page("Activate", "<h1>Missing session</h1><p class=sub>Open this page from the link Stripe sends you after checkout.</p>");
  if (!env.STRIPE_SECRET || !env.KEYS) return page("Activate", "<h1>Not configured</h1><p class=sub>STRIPE_SECRET and the KEYS binding are not set on this deployment.</p>");
  const r = await fetch("https://api.stripe.com/v1/checkout/sessions/" + encodeURIComponent(sid), { headers: { authorization: "Bearer " + env.STRIPE_SECRET } });
  if (!r.ok) return page("Activate", "<h1>Could not verify payment</h1><p class=sub>Stripe returned an error for this session. Contact support with your receipt.</p>");
  const s = await r.json();
  if (s.payment_status !== "paid" && s.status !== "complete") return page("Activate", "<h1>Payment not completed</h1><p class=sub>This checkout session is not paid.</p>");
  const customer = s.customer || s.customer_details?.email || sid;
  const email = s.customer_details?.email || "";
  let key = await env.KEYS.get("cust:" + customer);
  if (!key) {
    key = randKey();
    await env.KEYS.put("key:" + key, JSON.stringify({ tier: "pro", email, customer, subscription: s.subscription || null, created: new Date().toISOString(), active: true }));
    await env.KEYS.put("cust:" + customer, key);
  }
  return page("Your API key", `<h1>You're on the Pro tier</h1><p>Your API key. Keep it private; it is shown on this page only and can be re-shown by reopening the same link.</p><code>${key}</code>
<p>Use it as a header <code style="display:inline;padding:2px 6px">x-api-key: ${key}</code> or as <code style="display:inline;padding:2px 6px">?key=</code> on any query endpoint. Check it at <a href="/api/v1/me?key=${key}">/api/v1/me</a>. Docs at <a href="/api/">/api/</a>.</p>
<p class=sub>Receipts and cancellation are handled by Stripe; the key deactivates automatically when a subscription ends.</p>`);
}
