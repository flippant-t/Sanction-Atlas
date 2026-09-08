export async function onRequest(context) {
  if (context.request.method === "OPTIONS") {
    return new Response(null, { status: 204, headers: { "access-control-allow-origin": "*", "access-control-allow-methods": "GET, POST, OPTIONS", "access-control-allow-headers": "content-type", "access-control-max-age": "86400" } });
  }
  const res = await context.next();
  const h = new Headers(res.headers); h.set("access-control-allow-origin", "*");
  return new Response(res.body, { status: res.status, headers: h });
}
