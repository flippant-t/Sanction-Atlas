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
  /api/v1/watchlist  (Pro)          names monitored against every nightly build
  /api/v1/alerts     (Pro)          changes affecting watched names

Request limits live in one place, LIMITS below, and are written into both the docs page and
openapi.json from there. They previously drifted apart: the page said 200 names per screen
request, the curl example said 500 and the OpenAPI summary said 100 free / 500 Pro, while the
worker enforced 50 and 200. Keep this table in step with functions/api/v1/_lib.js.
"""
import html, json, os, re, hashlib, datetime as dt
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = os.path.join(ROOT, "site")
DATA = os.path.join(SITE, "data")
API = os.path.join(SITE, "api", "v1")

# Mirrors LIMITS in functions/api/v1/_lib.js. If you change one, change both.
LIMITS = {
    "free": {"screen": 50, "search": 20, "candidates": 60},
    "pro": {"screen": 200, "search": 100, "candidates": 200},
}
RATE_PER_MIN = 120

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
    # The screening scorer also reads this: a token carried by hundreds of parties is weighted down,
    # which is what stops common surnames producing hits.
    df = {t: len(v) for t, v in tok2p.items() if len(v) >= 60}
    dump(os.path.join(API, "search", "_meta.json"), {"prefix_lens": prefix_lens, "shards": sorted(plan), "tokens": len(tok2p),
         "max_posting": MAX_POSTING, "legal": sorted(LEGAL), "df": df, "entities": len(parties)})
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
            "/search": {"get": {"summary": f"Name or alias search (dynamic). {LIMITS['free']['search']} results per request, {LIMITS['pro']['search']} with a Pro key.",
                                "parameters": [{"name": "q", "in": "query", "required": True}, {"name": "limit", "in": "query"}]}},
            "/me": {"get": {"summary": "Tier and limits for the supplied API key"}},
            "/watchlist": {
                "get": {"summary": "Your watchlists (Pro)"},
                "post": {"summary": "Create or replace a watchlist of names to monitor (Pro). Matching runs in the nightly build, so the response is a confirmation, not results.",
                         "requestBody": {"content": {"application/json": {"schema": {"type": "object", "properties": {
                             "label": {"type": "string"}, "names": {"type": "array", "items": {"type": "string"}},
                             "threshold": {"type": "number"}, "near": {"type": "boolean"},
                             "email": {"type": "string"}, "webhook": {"type": "string"}}}}}}}},
            "/watchlist/{id}": {"get": {"summary": "One watchlist with its current matches (Pro)"},
                                "delete": {"summary": "Delete a watchlist, its names and its match history (Pro)"}},
            "/alerts": {"get": {"summary": "Changes affecting your watched names (Pro)", "parameters": [
                {"name": "since", "in": "query"}, {"name": "type", "in": "query"},
                {"name": "list_id", "in": "query"}, {"name": "limit", "in": "query"}]}},
            "/screen": {"post": {"summary": f"Fuzzy screening. {LIMITS['free']['screen']} names per request, {LIMITS['pro']['screen']} with a Pro key; the in-browser screener has no limit.",
                                 "requestBody": {"content": {"application/json": {"schema": {"type": "object", "properties": {"names": {"type": "array", "items": {"type": "string"}}, "threshold": {"type": "number"}}}}}}}},
        },
    })

    # docs page
    import theme
    ex = site_url + "api/v1/"
    n_auth = sum(1 for v in meta["authorities"].values() if v["ok"])
    F, P = LIMITS["free"], LIMITS["pro"]
    AUTH_NAME = {"US": "United States", "EU": "European Union", "UK": "United Kingdom",
                 "UN": "United Nations", "AU": "Australia", "CA": "Canada"}
    AUTH_LIST = {"US": "Consolidated Screening List (OFAC SDN and non-SDN, BIS Entity, Denied Persons, Unverified and MEU, State Department)",
                 "EU": "Consolidated Financial Sanctions List", "UK": "UK Sanctions List (FCDO)",
                 "UN": "Security Council Consolidated List", "AU": "DFAT Consolidated List",
                 "CA": "SEMA and autonomous sanctions"}
    # Freshness is what a compliance reader checks first, so it replaces the counters that used to
    # sit here. Every authority shown as loaded was fetched during this build; a failure is named.
    auth_rows = "".join(
        f'<tr><td>{esc(AUTH_NAME.get(a, a))}</td><td>{esc(AUTH_LIST.get(a, ""))}</td>'
        f'<td class="n">{v.get("n", 0):,}</td>'
        f'<td class="nw">{"loaded" if v.get("ok") else "<b class=failed>not loaded</b>"}</td></tr>'
        for a, v in meta["authorities"].items())
    tabs_js = """<script>document.querySelectorAll('.tabs').forEach(t=>{const pres=[];let n=t.nextElementSibling;while(n&&n.tagName==='PRE'){pres.push(n);n=n.nextElementSibling;}
      t.querySelectorAll('button').forEach((b,i)=>b.onclick=()=>{t.querySelectorAll('button').forEach(x=>x.classList.remove('on'));b.classList.add('on');pres.forEach((p,j)=>p.style.display=i===j?'':'none');});pres.forEach((p,j)=>p.style.display=j?'none':'');});</script>"""
    body = f"""<h1>Screen a name against six sanctions lists in one request</h1>
<p class="lede">{meta['parties']:,} designated parties from the US, EU, UK, UN, Australian and Canadian lists, merged so one party is one record however many authorities carry it. Rebuilt every night from the official sources. No key needed to start.</p>
<div class="row"><a class="btn warm" href="#quickstart">Get started free</a><a class="btn" href="#monitoring">See monitoring</a></div>

<h2 id="try">See it work</h2>
<p class="sub">Paste this into a terminal. Nothing to install, no signup.</p>
<pre><code>curl -X POST "{ex}screen" -H "content-type: application/json" \\
  -d '{{"names": ["Vladimir Putin"]}}'</code></pre>
<p>One request, and every authority that has designated the party comes back on one record:</p>
<pre><code>{{
  "screened": 1, "flagged": 1,
  "results": [{{ "name": "Vladimir Putin", "matches": [{{
      "name": "PUTIN, Vladimir Vladimirovich",
      "type": "Individual", "country": "RU",
      "authorities": ["US", "EU", "AU", "CA"],
      "programs": ["RUSSIA-EO14024", "EU:UKR", "AU:Autonomous (Russia)", "CA:Russia"],
      "score": 1,
      "url": "{ex}party/ofa%3A35096"
  }}]}}]
}}</code></pre>
<p>Hitting OFAC's own file would have told you about the United States. The <code>authorities</code> array is the part that takes work to build, and it is why {meta.get('multi_listed',0):,} of these parties resolve to one record here rather than five.</p>

<h2 id="uses">What it is used for</h2>
<p><b>Onboarding customers and releasing payments.</b> Screen at signup, again before a payout clears. One call covers every authority, so there is no separate OFAC, EU and UK check to reconcile afterwards. <code>POST screen</code></p>
<p><b>Shipping goods or technology abroad.</b> US export-control lists sit alongside OFAC here: the BIS Entity List, Denied Persons, Unverified and Military End User lists, and State Department debarments. Screening that only covers OFAC misses every one of them, and most cheap screening only covers OFAC. <code>POST screen</code></p>
<p><b>Operating under more than one regime.</b> US, EU and UK designations have diverged since 2022. The <code>authorities</code> field answers, in one call, whether a party is a problem for your London entity, your New York entity, or both. <code>GET search</code></p>
<p><b>Fixing a vessel or checking a trade counterparty.</b> Ships are listed as parties with the IMO number in the record, so a vessel resolves the same way a company does. <code>GET search</code></p>
<p><b>Onboarding investors and fund subscribers.</b> A subscription list goes through in one batch, and the response lines up with your input so it can go on the file as evidence. <code>POST screen</code></p>
<p><b>Watching a book you already have.</b> The counterparty who cleared in January and is designated in March is the one that matters. Names left under watch are rechecked against every nightly build. <code>POST watchlist</code></p>

<h2 id="coverage">What is loaded right now</h2>
<p class="sub">Every list is downloaded fresh from the publishing authority each night. If a download or parse fails, that authority is marked failed here rather than quietly serving yesterday's copy.</p>
<table><tr><th>Authority</th><th>List</th><th class="n">Records</th><th class="nw">This build</th></tr>
{auth_rows}</table>
<p class="meta">All figures from the build of {esc(meta['date'])}. {meta['parties']:,} merged parties in total, {meta.get('multi_listed',0):,} carried by more than one authority, {meta['edges']['link']:,} relationships taken from the official records. <a href="{ex}meta.json">meta.json</a> carries the same figures for machines.</p>

<h2 id="pricing">What it costs</h2>
<p><b>Screening is free, unlimited, and needs no key.</b> Unlimited requests, {F['screen']} names in each, every static file, and an in-browser screener with no limit at all. That is not a trial. If screening is what you need, you are done, and nothing below applies to you.</p>
<p>Pro is $19.99 a month and exists for one reason: it keeps watching after you stop asking.</p>
<table><tr><th>Pro adds</th><th>&nbsp;</th></tr>
<tr><td><b>Monitoring</b><br><span class="meta">Leave names under watch. Every nightly build rechecks them against all six lists and raises an alert by email or webhook when a watched name starts matching, when an existing match is amended, and when one is delisted. The counterparty who cleared in January and is designated in March is the case this exists for.</span></td><td class="nw">Included</td></tr>
<tr><td><b>Deeper candidate search</b><br><span class="meta">{P['candidates']} possible matches scored per name instead of {F['candidates']}, which surfaces more distant spelling variants.</span></td><td class="nw">{P['candidates']} per name</td></tr>
<tr><td><b>Bigger batches</b><br><span class="meta">For a client book or a subscription list going through in one pass.</span></td><td class="nw">{P['screen']} names per request<br>{P['search']} search results</td></tr>
<tr><td><b>Commercial use and support</b></td><td class="nw">Included</td></tr></table>
<div class="row"><a class="btn warm" href="{site_url}api/subscribe">Subscribe, $19.99 a month</a><a class="btn" href="{site_url}api/portal">Manage subscription</a></div>
<p class="meta">Cancel any time; access runs to the end of the paid period. Keys are issued on the page you land on after checkout and can be re-shown by reopening that link. Send the key as an <code>x-api-key</code> header rather than <code>?key=</code>, which leaks into logs and browser history. <code>GET me</code> confirms the tier. Keys deactivate automatically when a subscription ends. Tax is calculated at checkout.</p>

<h2 id="monitoring">Monitoring</h2>
<p>Screening answers a question about today. Monitoring answers it every night without being asked. Post the names you want watched; every nightly build rechecks them against all six lists and raises an alert when something changes.</p>
<pre><code># put a book of counterparties under watch
curl -X POST "{ex}watchlist" -H "content-type: application/json" \\
  -H "x-api-key: YOUR_KEY" \\
  -d '{{"label": "Counterparties Q3",
       "names": ["Acme Trading LLC", "Ivan Petrov"],
       "threshold": 0.85,
       "email": "compliance@example.com"}}'

# what changed since a date
curl "{ex}alerts?since=2026-09-01" -H "x-api-key: YOUR_KEY"</code></pre>
<p class="meta">Matching runs inside the nightly build rather than at request time, so the response to a POST is a confirmation and the matches appear on the watchlist itself. Set <code>near</code> to true to also hear about matches below your threshold, which helps while you are calibrating. Deleting a watchlist deletes its names and its match history with it.</p>

<h2 id="quickstart">Quick start</h2>
<div class="tabs"><button class="on">curl</button><button>Python</button><button>JavaScript</button></div>
<pre><code># search across all six lists
curl "{ex}search?q=sberbank"

# one record
curl "{ex}party/ofa:31695"

# screen a list of names ({F['screen']} per request free, {P['screen']} with a Pro key)
curl -X POST "{ex}screen" -H "content-type: application/json" \\
  -d '{{"names":["Sberbank of Russia","John Smith"],"threshold":0.85}}'

# rate limit: {RATE_PER_MIN} requests a minute per IP, then 429 with a retry-after header</code></pre>
<pre><code>import requests
BASE = "{ex}"
hits = requests.get(BASE + "search", params={{"q": "sberbank"}}).json()["results"]
rec  = requests.get(BASE + "party/" + hits[0]["id"]).json()
scr  = requests.post(BASE + "screen", json={{"names": ["Sberbank of Russia", "John Smith"]}}).json()
for r in scr["results"]:
    print(r["name"], "->", [(m["name"], m["score"]) for m in r["matches"]])</code></pre>
<pre><code>const BASE = "{ex}";
const hits = (await (await fetch(BASE + "search?q=sberbank")).json()).results;
const rec  = await (await fetch(BASE + "party/" + encodeURIComponent(hits[0].id))).json();
const scr  = await (await fetch(BASE + "screen", {{
  method: "POST", headers: {{"content-type": "application/json"}},
  body: JSON.stringify({{names: ["Sberbank of Russia", "John Smith"]}})
}})).json();</code></pre>

<div class="callout">Name matching is approximate by design. A match means "look closer", never "this is the same party", and an empty result is not a clearance. Ownership is not resolved: a company owned at or above 50 percent by designated parties is blocked under the OFAC 50 percent rule even though it appears on no list, and it will not be returned here. Confirm against the official record, linked from every result, before acting. Full terms on the <a href="{site_url}terms.html">terms page</a>.</div>

<h2 id="reference">Reference</h2>
<h3>Endpoints</h3>
<table><tr><th class="nw">Endpoint</th><th>What it does</th></tr>
<tr><td class="nw"><code>GET search</code></td><td>Name and alias search across all lists, accent-insensitive, word order ignored, scored 0 to 1. <code>?q=</code> and <code>&amp;limit=</code>.</td></tr>
<tr><td class="nw"><code>GET party/&lt;id&gt;</code></td><td>One merged party. Ids look like <code>ofa:12345</code>, <code>eu:EU-123</code>, <code>uk:UK-RUS0001</code>.</td></tr>
<tr><td class="nw"><code>POST screen</code></td><td>Body <code>{{"names": [...], "threshold": 0.85}}</code>. Up to five scored matches per name, and an empty array for a name with none, so the response lines up with your input. Names are processed in memory and not stored. For large lists use the <a href="{site_url}screen.html">in-browser screener</a>, which runs on your own machine and has no limit.</td></tr>
<tr><td class="nw"><code>GET watchlist</code><br><code>POST watchlist</code><br><code>GET watchlist/&lt;id&gt;</code><br><code>DELETE watchlist/&lt;id&gt;</code></td><td>Names monitored against every nightly build. Pro.</td></tr>
<tr><td class="nw"><code>GET alerts</code></td><td>Additions, amendments and delistings affecting watched names. <code>?since=</code>, and filter by <code>type</code> or <code>list_id</code>. Pro.</td></tr>
<tr><td class="nw"><code>GET me</code></td><td>Tier and limits for the supplied key.</td></tr></table>

<h3>Matching and thresholds</h3>
<p>Names are compared after accents are stripped and punctuation flattened, with word order ignored, so <code>Putin Vladimir</code> and <code>Vladimir Putin</code> score alike and the French spelling <code>Poutine</code> still matches. Each word is then weighted by how rare it is across the corpus: a surname carried by hundreds of designated parties counts for little, a distinctive one counts for a lot. Words in the listed name that your query does not account for pull the score down, so matching two words of a four-word name is weak even when both are exact.</p>
<table><tr><th class="nw">Threshold</th><th>What to expect</th></tr>
<tr><td class="nw">0.90 and up</td><td>Close to exact. Few false positives, will miss transliteration variants.</td></tr>
<tr><td class="nw">0.85</td><td>The default. Catches spelling and word-order variation without burying you.</td></tr>
<tr><td class="nw">0.75 to 0.80</td><td>A wider net for a review queue. Expect more to clear.</td></tr>
<tr><td class="nw">Below 0.70</td><td>Research only, not a screening posture.</td></tr></table>
<p class="meta">Scores are comparable between requests but not across versions of the matcher; when the scoring changes it is noted here.</p>

<h3>Limits and errors</h3>
<table><tr><th class="nw">Status</th><th>What it means</th></tr>
<tr><td class="nw">400</td><td>The body was not JSON, or <code>names</code> was missing or empty.</td></tr>
<tr><td class="nw">401</td><td>The key is unknown or no longer active.</td></tr>
<tr><td class="nw">403</td><td>A Pro feature was requested without a Pro key.</td></tr>
<tr><td class="nw">405</td><td>Wrong method. Screening is POST, the rest are GET.</td></tr>
<tr><td class="nw">413</td><td>Too many names for your tier in one request; the cap is in the response.</td></tr>
<tr><td class="nw">429</td><td>Over {RATE_PER_MIN} requests a minute from one IP. A <code>retry-after</code> header says how long to wait.</td></tr></table>
<p class="meta">Errors carry an <code>error</code> field in plain language and, where there is one, a <code>hint</code> saying what to do instead. A very large batch can return <code>partial: true</code>, meaning some names were matched against a reduced candidate set; split the batch and treat that result as incomplete.</p>

<h3>Static files</h3>
<p class="sub">Regenerated nightly. Cache them; they change once a day.</p>
<table><tr><th class="nw">Path</th><th>Contents</th></tr>
<tr><td class="nw"><a href="{ex}meta.json">meta.json</a></td><td>Build time, counts, per-authority status, program and country lists</td></tr>
<tr><td class="nw"><a href="{ex}parties.json">parties.json</a></td><td>Manifest of parts (8,000 parties each) holding every merged party with addresses, aliases, programs, authorities, coordinates and links</td></tr>
<tr><td class="nw"><a href="{ex}index.json">index.json</a></td><td>Compact index for client-side search: id, name, aliases, type, country, authorities, programs</td></tr>
<tr><td class="nw"><a href="{ex}changes.json">changes.json</a><br><a href="{ex}feed.xml">feed.xml</a></td><td>Additions and removals by date, as JSON and RSS</td></tr>
<tr><td class="nw"><a href="{ex}programs.json">programs.json</a></td><td>Programs with counts; <code>programs/&lt;slug&gt;.json</code> for parties in one program</td></tr>
<tr><td class="nw"><a href="{ex}countries.json">countries.json</a></td><td>Countries with counts; <code>countries/&lt;iso2&gt;.json</code> for parties in one country</td></tr>
<tr><td class="nw"><a href="{ex}openapi.json">openapi.json</a></td><td>Machine-readable description</td></tr></table>

<h3>Record shape</h3>
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

<h3>Terms</h3>
<p class="sub">Source data are official government publications; the merged form is released CC0. No uptime guarantee on the free tier; please cache and keep request volume reasonable. Support for Pro subscribers at <a href="mailto:hello@sanctionscope.com">hello@sanctionscope.com</a>.</p>
{tabs_js}"""
    doc = theme.shell("API", f"Sanctions screening and monitoring API across the US, EU, UK, UN, Australian and Canadian lists: {meta['parties']:,} merged parties, free tier with no key, Pro monitoring from $19.99 a month.", body, site_url, site_url + "api/", on="API", built=meta["date"], narrow=False)
    os.makedirs(os.path.join(SITE, "api"), exist_ok=True)
    with open(os.path.join(SITE, "api", "index.html"), "w", encoding="utf-8") as f: f.write(doc)
    return len(parties), len(shards), n_shards_search

if __name__ == "__main__":
    import sys
    print(build_api(sys.argv[1] if len(sys.argv) > 1 else ""))
