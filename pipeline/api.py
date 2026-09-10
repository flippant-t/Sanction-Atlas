"""
Static API. Written by build.py after pages.py. Everything under site/api/v1/ is a plain file
served by the host; the few dynamic endpoints live in /functions (Cloudflare Pages Functions)
and read these same files.

  /api/v1/meta.json                 build info, counts, authority status
  /api/v1/parties.json              every merged party (large)
  /api/v1/index.json                compact search index: id, name, aliases, type, country, authorities, programs
  /api/v1/changes.json              additions and removals by date
  /api/v1/programs.json             list of programs with counts
  /api/v1/programs/<slug>.json      parties in one program
  /api/v1/countries.json            list of countries with counts
  /api/v1/countries/<iso2>.json     parties located in one country
  /api/v1/shards/<xx>.json          parties sharded by id hash (used by the party lookup function)
  /api/v1/feed.xml                  RSS of changes
  /api/v1/openapi.json              machine-readable description

Dynamic (functions/):
  /api/v1/party/<id>                one party by id
  /api/v1/search?q=                 name / alias search
  /api/v1/screen  (POST)            fuzzy screening of a list of names
"""
import html, json, os, re, hashlib, datetime as dt
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = os.path.join(ROOT, "site")
DATA = os.path.join(SITE, "data")
API = os.path.join(SITE, "api", "v1")

def esc(s): return html.escape(str(s or ""))

def slug(s):
    import re
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:80] or "x"

def dump(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, separators=(",", ":"), ensure_ascii=False)

def load(name):
    with open(os.path.join(DATA, name), encoding="utf-8") as f: obj = json.load(f)
    if name == "parties.json" and "parts" in obj:
        obj["parties"] = []
        for part in obj["parts"]:
            with open(os.path.join(DATA, part), encoding="utf-8") as f: obj["parties"] += json.load(f)
    return obj

def shard_of(pid):
    return hashlib.sha1(pid.encode()).hexdigest()[:2]

def build_api(site_url):
    # payment link: site/config.json wins (easy to edit on GitHub); env var is the fallback
    pay_link = os.environ.get("STRIPE_PAYMENT_LINK", ""); portal = "https://billing.stripe.com/p/login/7sYdR8fRmdRk1q05aO8IU00"
    cfg_path = os.path.join(SITE, "config.json")
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, encoding="utf-8") as f: cfg = json.load(f)
            pay_link = cfg.get("payment_link") or pay_link; portal = cfg.get("billing_portal") or portal
        except Exception: pass
    site_url = (site_url or "").rstrip("/") + "/" if site_url else "/"
    meta = load("meta.json"); data = load("parties.json"); changes = load("changes.json")
    parties, edges = data["parties"], data["edges"]
    src_list = meta.get("src_list", [])
    by_id = {p["id"]: p for p in parties}

    # attach edges to parties (ids only) so a single record is self-describing
    links = defaultdict(list)
    for e in edges:
        if e["k"] == "link":
            links[e["a"]].append(e["b"]); links[e["b"]].append(e["a"])
    for p in parties:
        p["links"] = sorted(set(links.get(p["id"], [])))
        if "si" in p:
            p["list"] = src_list[p["si"]] if p["si"] < len(src_list) else None
            del p["si"]
        p["page"] = f"{site_url}parties/{p['pg']}.html" if p.get("pg") else None
        p["map"] = f"{site_url}#p={p['id']}"

    # remove obsolete generated files so removed parties don't linger
    import shutil
    if os.path.isdir(API): shutil.rmtree(API)
    os.makedirs(API, exist_ok=True)

    dump(os.path.join(API, "meta.json"), {**meta, "api_version": "1", "base": site_url + "api/v1/", "license": "Data from official government lists; this merged form is CC0. No warranty; verify against the official record."})
    CHUNK = 8000
    part_urls = []
    for i in range(0, len(parties), CHUNK):
        name = f"parties-{i // CHUNK + 1}.json"; part_urls.append(site_url + "api/v1/" + name)
        dump(os.path.join(API, name), parties[i:i + CHUNK])
    dump(os.path.join(API, "parties.json"), {"count": len(parties), "parts": part_urls, "note": "Split into parts of 8,000 parties to stay under host file-size limits; fetch each part and concatenate."})
    dump(os.path.join(API, "changes.json"), changes)
    dump(os.path.join(API, "index.json"), [{"id": p["id"], "n": p["n"], "alt": p.get("alt", [])[:12], "t": p["t"], "cc": p.get("cc"), "au": p["au"], "p": p.get("p", []), "city": p.get("city"), "pg": p.get("pg")} for p in parties])

    # ---- search shards: a prefix-sharded inverted index so the query worker parses a few KB
    # instead of rebuilding a 40k-entity token index on every cold start (which blows the CPU limit).
    import unicodedata
    LEGAL = set("LLC LTD LIMITED INC CORP CORPORATION CO COMPANY GMBH AG SA SAS SARL BV NV PLC PJSC JSC OJSC CJSC OAO ZAO OOO AO PAO LLP LP SRL SPA PTE PTY PVT FZE FZCO THE OF AND PUBLIC JOINT STOCK OPEN CLOSED".split())
    def _norm(x):
        x = unicodedata.normalize("NFKD", x or "")
        x = "".join(c for c in x if not unicodedata.combining(c)).upper()
        return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9 ]+", " ", x)).strip()
    def _toks(x): return [t for t in _norm(x).split(" ") if t and t not in LEGAL and len(t) > 1]
    MAX_POSTING = 1200          # very common tokens are not selective; cap them to bound shard size
    tok2p = defaultdict(list)
    for i, p in enumerate(parties):
        seen = set()
        for name in [p["n"]] + (p.get("alt") or [])[:6]:
            for t in _toks(name):
                if t not in seen:
                    seen.add(t)
                    if len(tok2p[t]) < MAX_POSTING: tok2p[t].append(i)
    def _mini(i):
        q = parties[i]
        return [q["id"], q["n"], q.get("t"), q.get("cc"), q.get("au") or [], q.get("p") or [], (q.get("alt") or [])[:6]]
    def _blob(ts):
        recs, out = {}, {}
        for t in ts:
            for i in tok2p[t]:
                if i not in recs: recs[i] = _mini(i)
            out[t] = tok2p[t]
        return {"r": recs, "t": out}
    TARGET = 40 * 1024
    def _split(pre, ts, depth):
        body = _blob(ts)
        if len(json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode()) <= TARGET or depth >= 5 or len(ts) == 1:
            return {pre: ts}
        sub = defaultdict(list)
        for t in ts: sub[t[:depth + 1] if len(t) >= depth + 1 else t].append(t)
        if len(sub) == 1: return {pre: ts}
        out = {}
        for k, v in sub.items(): out.update(_split(k, v, depth + 1))
        return out
    buckets = defaultdict(list)
    for t in tok2p: buckets[t[:2] if len(t) >= 2 else t].append(t)
    plan = {}
    for pre, ts in buckets.items(): plan.update(_split(pre, ts, 2))
    # a token has to resolve to exactly one shard name: record the prefix lengths in use
    prefix_lens = sorted({len(k) for k in plan}, reverse=True)
    for name, ts in plan.items():
        dump(os.path.join(API, "search", name.lower() + ".json"), _blob(ts))
    # document frequency for common tokens only, so the worker can pick the most selective token in a
    # query and avoid pulling the huge "BANK" / "LIMITED" style shards when a rarer one is available.
    df = {t: len(v) for t, v in tok2p.items() if len(v) >= 60}
    dump(os.path.join(API, "search", "_meta.json"), {"prefix_lens": prefix_lens, "shards": sorted(plan), "tokens": len(tok2p),
         "max_posting": MAX_POSTING, "legal": sorted(LEGAL), "df": df})
    n_shards_search = len(plan)

    # shards
    shards = defaultdict(dict)
    for p in parties: shards[shard_of(p["id"])][p["id"]] = p
    for sh, recs in shards.items(): dump(os.path.join(API, "shards", f"{sh}.json"), recs)

    # programs
    by_prog = defaultdict(list)
    for p in parties:
        for g in p.get("p", []): by_prog[g].append(p["id"])
    dump(os.path.join(API, "programs.json"), [{"code": g, "slug": slug(g), "count": len(ids), "url": f"{site_url}api/v1/programs/{slug(g)}.json"} for g, ids in sorted(by_prog.items(), key=lambda x: -len(x[1]))])
    for g, ids in by_prog.items():
        dump(os.path.join(API, "programs", f"{slug(g)}.json"), {"code": g, "count": len(ids), "parties": [by_id[i] for i in ids]})

    # countries
    by_cc = defaultdict(list)
    for p in parties:
        if p.get("cc"): by_cc[p["cc"]].append(p["id"])
    dump(os.path.join(API, "countries.json"), [{"iso2": cc, "name": meta["iso_name"].get(cc, cc), "count": len(ids), "url": f"{site_url}api/v1/countries/{cc.lower()}.json"} for cc, ids in sorted(by_cc.items(), key=lambda x: -len(x[1]))])
    for cc, ids in by_cc.items():
        dump(os.path.join(API, "countries", f"{cc.lower()}.json"), {"iso2": cc, "name": meta["iso_name"].get(cc, cc), "count": len(ids), "parties": [by_id[i] for i in ids]})

    # RSS feed of changes
    items = []
    for e in changes.get("events", [])[:300]:
        title = ("Added: " if e["op"] == "+" else "Removed: ") + e["n"]
        desc = ", ".join(x for x in [e.get("t"), e.get("s"), ", ".join(e.get("p") or []), meta["iso_name"].get(e.get("cc"), "")] if x)
        link = f"{site_url}#p={e['id']}"
        items.append(f"<item><title>{html.escape(title)}</title><link>{html.escape(link)}</link><guid isPermaLink=\"false\">{html.escape(e['op'] + e['id'] + e['d'])}</guid><pubDate>{e['d']}</pubDate><description>{html.escape(desc)}</description></item>")
    rss = f"""<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>SanctionScope changes</title><link>{html.escape(site_url)}</link><description>Additions and removals across the US, EU, UK, UN, Australian and Canadian sanctions lists, updated nightly.</description>{''.join(items)}</channel></rss>"""
    with open(os.path.join(API, "feed.xml"), "w", encoding="utf-8") as f: f.write(rss)

    # OpenAPI-ish description
    dump(os.path.join(API, "openapi.json"), {
        "openapi": "3.0.0", "info": {"title": "SanctionScope API", "version": "1", "description": "Merged sanctions lists (US, EU, UK, UN, AU, CA), geocoded, with cross-list relationships. Static JSON plus a few query endpoints. Free, no key."},
        "servers": [{"url": site_url + "api/v1"}],
        "paths": {
            "/meta.json": {"get": {"summary": "Build info, counts, authority status"}},
            "/parties.json": {"get": {"summary": "All merged parties (large)"}},
            "/index.json": {"get": {"summary": "Compact index for client-side search"}},
            "/changes.json": {"get": {"summary": "Additions and removals by date"}},
            "/feed.xml": {"get": {"summary": "RSS of changes"}},
            "/programs.json": {"get": {"summary": "Programs with counts"}},
            "/programs/{slug}.json": {"get": {"summary": "Parties in one program"}},
            "/countries.json": {"get": {"summary": "Countries with counts"}},
            "/countries/{iso2}.json": {"get": {"summary": "Parties located in one country"}},
            "/party/{id}": {"get": {"summary": "One party by id (dynamic)"}},
            "/search": {"get": {"summary": "Name or alias search (dynamic)", "parameters": [{"name": "q", "in": "query", "required": True}, {"name": "limit", "in": "query"}]}},
            "/me": {"get": {"summary": "Tier and limits for the supplied API key"}},
            "/screen": {"post": {"summary": "Fuzzy screening (100 names per request free, 500 with a Pro key); the in-browser screener has no limit", "requestBody": {"content": {"application/json": {"schema": {"type": "object", "properties": {"names": {"type": "array", "items": {"type": "string"}}, "threshold": {"type": "number"}}}}}}}},
        },
    })

    # docs page
    import theme
    ex = site_url + "api/v1/"
    n_auth = sum(1 for v in meta["authorities"].values() if v["ok"])
    tabs_js = """<script>document.querySelectorAll('.tabs').forEach(t=>{const pres=[];let n=t.nextElementSibling;while(n&&n.tagName==='PRE'){pres.push(n);n=n.nextElementSibling;}
      t.querySelectorAll('button').forEach((b,i)=>b.onclick=()=>{t.querySelectorAll('button').forEach(x=>x.classList.remove('on'));b.classList.add('on');pres.forEach((p,j)=>p.style.display=i===j?'':'none');});pres.forEach((p,j)=>p.style.display=j?'none':'');});</script>"""
    body = f"""<h1>The sanctions data behind the map, as an API</h1>
<p class="lede">{meta['parties']:,} parties from {n_auth} authorities, merged across lists, geocoded, with the relationships between them. Rebuilt every night from the official sources. JSON, CORS enabled, no key needed to start.</p>
<div class="row"><a class="btn warm" href="#quickstart">Get started free</a><a class="btn" href="{site_url}api/subscribe">Subscribe to Pro</a><a class="btn" href="{ex}openapi.json">OpenAPI spec</a></div>
<div class="stats"><div class="stat"><b>{meta['parties']:,}</b><span>merged parties</span></div><div class="stat"><b>{n_auth}</b><span>authorities</span></div><div class="stat"><b>{meta.get('multi_listed',0):,}</b><span>on more than one list</span></div><div class="stat"><b>{meta['edges']['link']:,}</b><span>relationships</span></div><div class="stat"><b>nightly</b><span>rebuild, last {esc(meta['date'])}</span></div></div>

<h2 id="pricing">Plans</h2>
<div class="cards">
<div class="card"><div class="tag">Free</div><h3>Open access</h3><div class="price">$0<small> · no key</small></div>
<ul><li>Every static file: full dataset, programs, countries, changes, RSS</li><li>Search: 20 results per request</li><li>Screen: 50 names per request</li><li>Standard fuzzy-match depth</li><li>In-browser screener with no limit</li><li>Personal and research use</li></ul>
<a class="btn" href="#quickstart">Start with the docs</a></div>
<div class="card pro"><div class="tag">Pro</div><h3>For teams and products</h3><div class="price">$19.99<small> / month, cancel any time</small></div>
<ul><li>Everything in Free</li><li>Search: 100 results per request</li><li>Screen: 200 names per request</li><li>Deep fuzzy-match candidate search</li><li>Commercial use</li><li>Email support</li></ul>
<a class="btn warm" href="{site_url}api/subscribe">Subscribe</a> <a class="btn" href="{site_url}api/portal">Manage subscription</a></div>
</div>
<p class="meta">Pro keys are issued on the page you land on after checkout, and can be re-shown by reopening that link. Send the key as an <code>x-api-key</code> header or <code>?key=</code> parameter. <code>GET me</code> confirms the tier. Keys deactivate automatically when a subscription ends. Tax is calculated at checkout.</p>

<h2 id="quickstart">Quick start</h2>
<div class="tabs"><button class="on">curl</button><button>Python</button><button>JavaScript</button></div>
<pre><code># search across all six lists
curl "{ex}search?q=sberbank"

# one record
curl "{ex}party/ofa:31695"

# screen a list of names (Pro key optional; raises the limit to 500)
curl -X POST "{ex}screen" -H "content-type: application/json" \\
  -H "x-api-key: YOUR_KEY" \\
  -d '{{"names":["Sberbank of Russia","John Smith"],"threshold":0.85}}'

# rate limit: 429 with a retry-after header if you go too fast; send the key as a header, not ?key=</code></pre>
<pre><code>import requests
BASE = "{ex}"
hits = requests.get(BASE + "search", params={{"q": "sberbank"}}).json()["results"]
rec  = requests.get(BASE + "party/" + hits[0]["id"]).json()
scr  = requests.post(BASE + "screen", json={{"names": ["Sberbank of Russia", "John Smith"]}},
                     headers={{"x-api-key": "YOUR_KEY"}}).json()
for r in scr["results"]:
    print(r["name"], "->", [(m["name"], m["score"]) for m in r["matches"]])</code></pre>
<pre><code>const BASE = "{ex}";
const hits = (await (await fetch(BASE + "search?q=sberbank")).json()).results;
const rec  = await (await fetch(BASE + "party/" + encodeURIComponent(hits[0].id))).json();
const scr  = await (await fetch(BASE + "screen", {{
  method: "POST", headers: {{"content-type": "application/json", "x-api-key": "YOUR_KEY"}},
  body: JSON.stringify({{names: ["Sberbank of Russia", "John Smith"]}})
}})).json();</code></pre>

<h2>Query endpoints</h2>
<table><tr><th>Endpoint</th><th>What it does</th></tr>
<tr><td><code>GET search?q=&lt;text&gt;&amp;limit=20</code></td><td>Name and alias search across all lists. Diacritic-insensitive, token-based, scored 0 to 1.</td></tr>
<tr><td><code>GET party/&lt;id&gt;</code></td><td>One merged party. Ids look like <code>ofa:12345</code>, <code>eu:EU-123</code>, <code>uk:UK-RUS0001</code>.</td></tr>
<tr><td><code>POST screen</code></td><td>Body <code>{{"names": [...], "threshold": 0.85}}</code>. Returns up to five scored matches per name. Names are processed in memory and not stored. For large lists use the <a href="{site_url}screen.html">in-browser screener</a>, which runs on your own machine and has no limit.</td></tr>
<tr><td><code>GET me</code></td><td>Tier and limits for the supplied key.</td></tr></table>

<h2>Static files</h2>
<p class="sub">Regenerated nightly. Cache them; they change once a day.</p>
<table><tr><th>Path</th><th>Contents</th></tr>
<tr><td><a href="{ex}meta.json">meta.json</a></td><td>Build time, counts, per-authority status, program and country lists</td></tr>
<tr><td><a href="{ex}parties.json">parties.json</a></td><td>Manifest of parts (8,000 parties each) holding every merged party with addresses, aliases, programs, authorities, coordinates and links</td></tr>
<tr><td><a href="{ex}index.json">index.json</a></td><td>Compact index for client-side search: id, name, aliases, type, country, authorities, programs</td></tr>
<tr><td><a href="{ex}changes.json">changes.json</a> · <a href="{ex}feed.xml">feed.xml</a></td><td>Additions and removals by date, as JSON and RSS</td></tr>
<tr><td><a href="{ex}programs.json">programs.json</a> · programs/&lt;slug&gt;.json</td><td>Programs with counts; parties in one program</td></tr>
<tr><td><a href="{ex}countries.json">countries.json</a> · countries/&lt;iso2&gt;.json</td><td>Countries with counts; parties located in one country</td></tr>
<tr><td><a href="{ex}openapi.json">openapi.json</a></td><td>Machine-readable description</td></tr></table>

<h2>Record shape</h2>
<pre><code>{{
  "id": "ofa:31695", "n": "Central Bank of the Russian Federation", "t": "Entity",
  "au": ["US","EU","UK","AU","CA"],          authorities listing this party (merged by name)
  "p": ["RUSSIA-EO14024","EU:RUS", ...],     programs; non-US prefixed by authority
  "s": "OFAC other", "list": "Sectoral Sanctions Identifications List ...",
  "cc": "RU", "lat": 55.75, "lon": 37.61, "city": "Moscow",
  "inf": null,                                set when the location was inferred, not from an address
  "a": [...addresses], "alt": [...aliases], "nat": [...], "dob": "", "ids": "...", "rem": "...",
  "ly": 2022,                                 earliest published listing year across records
  "recs": [{{"au":"EU","url":"...","p":[...],"listed":"2022-02-25"}}, ...],   one per authority when merged
  "links": ["ofa:16681", ...],                "Linked To" relationships
  "page": "...", "map": "...#p=ofa:31695"
}}</code></pre>

<div class="callout">Name matching is approximate by design. A match means "look closer", never "this is the same party". Confirm against the official record, linked from every result, before acting. Full terms on the <a href="{site_url}terms.html">terms page</a>.</div>
<h2>Terms</h2><p class="sub">Source data are official government publications; the merged form is released CC0. No uptime guarantee on the free tier; please cache and keep request volume reasonable. Support for Pro subscribers at <a href="mailto:hello@sanctionscope.com">hello@sanctionscope.com</a>.</p>
{tabs_js}"""
    doc = theme.shell("API", f"Free JSON API for the merged US, EU, UK, UN, Australian and Canadian sanctions lists: {meta['parties']:,} geocoded parties with cross-list relationships. Pro tier for higher limits.", body, site_url, site_url + "api/", on="API", built=meta["date"], narrow=False)
    os.makedirs(os.path.join(SITE, "api"), exist_ok=True)
    with open(os.path.join(SITE, "api", "index.html"), "w", encoding="utf-8") as f: f.write(doc)
    return len(parties), len(shards), n_shards_search

if __name__ == "__main__":
    import sys
    print(build_api(sys.argv[1] if len(sys.argv) > 1 else ""))
