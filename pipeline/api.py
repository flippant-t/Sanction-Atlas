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
import html, json, os, hashlib, datetime as dt
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = os.path.join(ROOT, "site")
DATA = os.path.join(SITE, "data")
API = os.path.join(SITE, "api", "v1")

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
            "/screen": {"post": {"summary": "Fuzzy screening of up to 100 names per request (dynamic)", "requestBody": {"content": {"application/json": {"schema": {"type": "object", "properties": {"names": {"type": "array", "items": {"type": "string"}}, "threshold": {"type": "number"}}}}}}}},
        },
    })

    # docs page
    ex = site_url + "api/v1/"
    doc = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>API · SanctionScope</title>
<meta name="description" content="Free JSON API for the merged US, EU, UK, UN, Australian and Canadian sanctions lists, geocoded, with cross-list relationships.">
<style>:root{{--ocean:#0e1726;--ink:#e6e1d6;--ink-2:#a39d90;--ink-3:#6b6659;--line:#26344a;--sdn:#e9b44c}}*{{box-sizing:border-box}}body{{margin:0;background:var(--ocean);color:var(--ink);font-family:"Iowan Old Style","Palatino Linotype",Palatino,Georgia,serif;font-size:15px;line-height:1.55}}
a{{color:var(--sdn)}}main{{max-width:820px;margin:0 auto;padding:28px 20px 60px}}header{{border-bottom:1px solid var(--line);padding:14px 20px;font-size:14px;display:flex;gap:18px}}header a{{color:var(--ink-2);text-decoration:none}}header a.brand{{color:var(--ink);font-size:17px}}
h1{{font-size:28px;font-weight:400;margin:0 0 6px}}h2{{font-size:14px;font-weight:600;letter-spacing:.06em;color:var(--ink-2);margin:28px 0 8px}}code,pre{{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:13px}}pre{{background:#0b1321;border:1px solid var(--line);border-radius:4px;padding:10px 12px;overflow:auto}}
table{{border-collapse:collapse;width:100%;font-size:14px}}td,th{{text-align:left;padding:7px 8px;border-top:1px solid var(--line);vertical-align:top}}th{{color:var(--ink-3);font-weight:500}}.sub{{color:var(--ink-2)}}</style></head><body>
<header><a class="brand" href="{site_url}">SanctionScope</a><a href="{site_url}programs/">Programs</a><a href="{site_url}countries/">Countries</a><a href="{site_url}parties/">Parties</a><a href="{site_url}api/">API</a></header>
<main><h1>API</h1><p class="sub">The merged dataset behind the map: {meta['parties']:,} parties from {sum(1 for v in meta['authorities'].values() if v['ok'])} authorities, geocoded, with cross-list relationships. Rebuilt nightly. Free, no key, CORS enabled. Base URL <code>{ex}</code>.</p>
<h2>Static endpoints</h2><table><tr><th>Path</th><th>What</th></tr>
<tr><td><a href="{ex}meta.json">meta.json</a></td><td>Build time, counts, per-authority status, program and country lists</td></tr>
<tr><td><a href="{ex}parties.json">parties.json</a></td><td>Manifest of parts (8,000 parties each) that together hold every merged party with addresses, aliases, programs, authorities, coordinates and links</td></tr>
<tr><td><a href="{ex}index.json">index.json</a></td><td>Compact index (id, name, aliases, type, country, authorities, programs) for client-side search</td></tr>
<tr><td><a href="{ex}changes.json">changes.json</a></td><td>Additions and removals by date since the site began tracking</td></tr>
<tr><td><a href="{ex}feed.xml">feed.xml</a></td><td>The same as RSS</td></tr>
<tr><td><a href="{ex}programs.json">programs.json</a>, programs/&lt;slug&gt;.json</td><td>Programs with counts; parties in one program</td></tr>
<tr><td><a href="{ex}countries.json">countries.json</a>, countries/&lt;iso2&gt;.json</td><td>Countries with counts; parties located in one country</td></tr>
<tr><td><a href="{ex}openapi.json">openapi.json</a></td><td>Machine-readable description</td></tr></table>
<h2>Query endpoints</h2><table><tr><th>Path</th><th>What</th></tr>
<tr><td><code>GET party/&lt;id&gt;</code></td><td>One party. Ids look like <code>ofa:12345</code>, <code>eu:EU-123</code>, <code>uk:UK-RUS0001</code>.</td></tr>
<tr><td><code>GET search?q=&lt;text&gt;&amp;limit=20</code></td><td>Name and alias search across all lists, diacritic-insensitive.</td></tr>
<tr><td><code>POST screen</code></td><td>Body <code>{{"names": ["..."], "threshold": 0.85}}</code>, up to 100 names per request. Returns scored matches per name. Names are processed in memory and not stored.</td></tr></table>
<h2>Examples</h2>
<pre>curl "{ex}search?q=sberbank"
curl "{ex}party/ofa:12345"
curl -X POST "{ex}screen" -H "content-type: application/json" -d '{{"names":["Sberbank of Russia","John Smith"]}}'
curl "{ex}countries/ru.json" | jq '.parties[] | select(.t=="Vessel") | .n'</pre>
<h2>Record shape</h2><pre>{{
  "id": "ofa:31695", "n": "Central Bank of the Russian Federation", "t": "Entity",
  "au": ["US","EU","UK","AU","CA"],          authorities listing this party (merged by name)
  "p": ["RUSSIA-EO14024","EU:RUS", ...],     programs, non-US prefixed by authority
  "s": "OFAC other", "list": "Sectoral Sanctions Identifications List (SSI)...",
  "cc": "RU", "lat": 55.75, "lon": 37.61, "city": "Moscow",
  "inf": null,                                set when the location was inferred, not from an address
  "a": ["12 Neglinnaya St, Moscow, RU"], "alt": [...], "nat": [...], "dob": "", "ids": "...", "rem": "...",
  "ly": 2022,                                 earliest published listing year across records
  "recs": [{{"au":"EU","url":"...","p":[...],"listed":"2022-02-25"}}, ...],  one per authority when merged
  "links": ["ofa:16681", ...],                "Linked To" relationships
  "page": "...", "map": "...#p=ofa:31695"
}}</pre>
<h2>Terms</h2><p class="sub">Source data are official government publications. The merged form is released CC0. Cross-list matching is by name and approximate; inferred locations are marked. Always confirm against the official record before acting on a match. No uptime guarantee; please cache and be reasonable with request volume.</p>
</main></body></html>"""
    os.makedirs(os.path.join(SITE, "api"), exist_ok=True)
    with open(os.path.join(SITE, "api", "index.html"), "w", encoding="utf-8") as f: f.write(doc)
    return len(parties), len(shards)

if __name__ == "__main__":
    import sys
    print(build_api(sys.argv[1] if len(sys.argv) > 1 else ""))
