"""
Static pages for search engines: one per program, one per country, one per
well-connected party, plus indexes, sitemap.xml and robots.txt.

Called by build.py after the data is written. Reads site/data/*.json only.
"""
import html, json, os, re, datetime as dt
from collections import defaultdict, Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = os.path.join(ROOT, "site")
DATA = os.path.join(SITE, "data")
PARTY_PAGE_MIN_LINKS = 2      # a party gets its own page if it has at least this many "Linked To" edges
PARTY_PAGE_CAP = 8000

PROGRAM_NAMES = {
    "RUSSIA-EO14024": "Russia-related sanctions (Executive Order 14024)",
    "UKRAINE-EO13662": "Ukraine/Russia-related sanctions (Executive Order 13662, sectoral)",
    "UKRAINE-EO13661": "Ukraine/Russia-related sanctions (Executive Order 13661)",
    "UKRAINE-EO13660": "Ukraine/Russia-related sanctions (Executive Order 13660)",
    "UKRAINE-EO13685": "Crimea-related sanctions (Executive Order 13685)",
    "SDGT": "Specially Designated Global Terrorists",
    "FTO": "Foreign Terrorist Organizations",
    "SDNTK": "Specially Designated Narcotics Traffickers (Kingpin Act)",
    "SDNT": "Specially Designated Narcotics Traffickers (Colombia)",
    "ILLICIT-DRUGS-EO14059": "Illicit drug trade sanctions (Executive Order 14059)",
    "IFSR": "Iranian Financial Sanctions Regulations",
    "IRAN": "Iran sanctions",
    "IRAN-EO13902": "Iran sanctions (Executive Order 13902, economic sectors)",
    "IRAN-EO13846": "Iran sanctions (Executive Order 13846)",
    "IRAN-EO13876": "Iran sanctions (Executive Order 13876, Supreme Leader's office)",
    "IRAN-EO13871": "Iran sanctions (Executive Order 13871, metals)",
    "IRAN-HR": "Iran human rights sanctions",
    "IRAN-TRA": "Iran Threat Reduction Act",
    "IRGC": "Islamic Revolutionary Guard Corps-related",
    "NPWMD": "Non-Proliferation of Weapons of Mass Destruction",
    "DPRK": "North Korea sanctions", "DPRK2": "North Korea sanctions (Executive Order 13687)",
    "DPRK3": "North Korea sanctions (Executive Order 13722)", "DPRK4": "North Korea sanctions (Executive Order 13810)",
    "DPRK-NKSPEA": "North Korea Sanctions and Policy Enhancement Act",
    "GLOMAG": "Global Magnitsky (human rights abuse and corruption)",
    "CYBER2": "Cyber-related sanctions (Executive Order 13694, as amended)",
    "BELARUS": "Belarus sanctions", "BELARUS-EO14038": "Belarus sanctions (Executive Order 14038)",
    "VENEZUELA": "Venezuela sanctions", "VENEZUELA-EO13850": "Venezuela sanctions (Executive Order 13850)",
    "VENEZUELA-EO13884": "Venezuela sanctions (Executive Order 13884)",
    "SYRIA": "Syria sanctions", "SYRIA-CAESAR": "Caesar Syria Civilian Protection Act",
    "TCO": "Transnational Criminal Organizations",
    "CUBA": "Cuba sanctions", "LIBYA3": "Libya sanctions", "IRAQ2": "Iraq sanctions", "IRAQ3": "Iraq stabilization",
    "YEMEN": "Yemen sanctions", "SOMALIA": "Somalia sanctions", "SOUTH SUDAN": "South Sudan sanctions",
    "SUDAN": "Sudan sanctions", "DARFUR": "Darfur sanctions", "CAR": "Central African Republic sanctions",
    "DRCONGO": "Democratic Republic of the Congo sanctions", "MALI-EO13882": "Mali sanctions",
    "BURMA-EO14014": "Burma (Myanmar) sanctions (Executive Order 14014)", "BALKANS": "Western Balkans sanctions",
    "BALKANS-EO14033": "Western Balkans sanctions (Executive Order 14033)",
    "HK-EO13936": "Hong Kong-related sanctions (Executive Order 13936)",
    "NICARAGUA": "Nicaragua sanctions", "NICARAGUA-NHRAA": "Nicaragua Human Rights and Anticorruption Act",
    "ELECTION-EO13848": "Foreign interference in US elections (Executive Order 13848)",
    "HRIT-IR": "Iran human rights and information technology", "HRIT-SY": "Syria human rights and information technology",
    "CAATSA - RUSSIA": "CAATSA Russia-related", "CAATSA - IRAN": "CAATSA Iran-related",
    "PAARSSR": "Protecting Americans from Russian sanctions-related activity", "ETHIOPIA-EO14046": "Ethiopia sanctions",
    "CHINESE MILITARY COMPANIES": "Chinese military-industrial complex companies (Executive Order 13959)",
    "CMIC-EO13959": "Chinese military-industrial complex companies (Executive Order 13959)",
    "NS-PLC": "Non-SDN Palestinian Legislative Council", "SSIDL": "Sectoral Sanctions Identifications List",
    "LEBANON": "Lebanon sanctions", "ZIMBABWE": "Zimbabwe sanctions", "TCO-EO14059": "Transnational criminal organizations",
    "AFGHANISTAN-EO14033": "Afghanistan-related", "HOSTAGES-EO14078": "Hostage-taking and wrongful detention",
    "FENTANYL-EO14059": "Fentanyl trafficking", "ICC-EO14203": "International Criminal Court-related",
    "MAGNIT": "Magnitsky Act (Russia human rights)", "CYBER4": "Cyber-related sanctions (Executive Order 14144)", "PEESA-EO14039": "Protecting Europe's Energy Security Act (Nord Stream)",
    "ENTITY-LIST": "BIS Entity List (export licence required)", "DENIED-PERSONS": "BIS Denied Persons List (export privileges denied)",
    "UNVERIFIED-LIST": "BIS Unverified List", "MEU-LIST": "BIS Military End User List", "ISN": "State Department nonproliferation sanctions",
    "AECA-DEBARRED": "State Department AECA debarred parties (arms export)", "CAPTA": "Correspondent Account or Payable-Through Account sanctions", "FSE": "Foreign Sanctions Evaders",
}

def slug(s):
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s[:80] or "x"

def esc(s): return html.escape(str(s or ""))

def load(name):
    with open(os.path.join(DATA, name), encoding="utf-8") as f: obj = json.load(f)
    if name == "parties.json" and "parts" in obj:
        obj["parties"] = []
        for part in obj["parts"]:
            with open(os.path.join(DATA, part), encoding="utf-8") as f: obj["parties"] += json.load(f)
    return obj

import theme

def page(title, desc, body, rel, canonical, extra_head="", on=""):
    return theme.shell(title, desc, body, rel, canonical, on=on, built=_BUILT.get("d", ""), extra_head=extra_head)
_BUILT = {}

def write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f: f.write(content)

AUTH_NAME = {"US": "United States", "EU": "European Union", "UK": "United Kingdom", "UN": "United Nations", "AU": "Australia", "CA": "Canada"}
def party_link(p, rel):
    if p.get("pg"): return f'<a href="{rel}parties/{p["pg"]}.html">{esc(p["n"])}</a>'
    return f'<a href="{rel}#p={esc(p["id"])}">{esc(p["n"])}</a>'

def build_pages(site_url):
    site_url = (site_url or "").rstrip("/") + "/" if site_url else ""
    meta = load("meta.json"); data = load("parties.json"); changes = load("changes.json")
    parties, edges = data["parties"], data["edges"]
    byid = {p["id"]: p for p in parties}
    iso_name = meta["iso_name"]
    today = meta["date"]; _BUILT["d"] = today

    # link degree
    deg = Counter(); nbrs = defaultdict(list)
    for e in edges:
        if e["k"] != "link": continue
        deg[e["a"]] += 1; deg[e["b"]] += 1; nbrs[e["a"]].append(e["b"]); nbrs[e["b"]].append(e["a"])

    # which parties get pages
    paged = sorted((p for p in parties if deg[p["id"]] >= PARTY_PAGE_MIN_LINKS), key=lambda p: -deg[p["id"]])[:PARTY_PAGE_CAP]
    used = set()
    for p in paged:
        base = slug(p["n"]); s = base; i = 2
        while s in used: s = f"{base}-{i}"; i += 1
        used.add(s); p["pg"] = s
    # write pg back into parties.json so the map can link to pages
    if "parts" in data:
        CH = 8000
        for i, part in enumerate(data["parts"]):
            with open(os.path.join(DATA, part), "w", encoding="utf-8") as f:
                json.dump(data["parties"][i * CH:(i + 1) * CH], f, separators=(",", ":"), ensure_ascii=False)
    else:
        with open(os.path.join(DATA, "parties.json"), "w", encoding="utf-8") as f:
            json.dump(data, f, separators=(",", ":"), ensure_ascii=False)

    recent = [e for e in changes.get("events", []) if e["op"] == "+"]
    urls = []

    # ---------------- programs
    by_prog = defaultdict(list)
    for p in parties:
        for g in p.get("p", []): by_prog[g].append(p)
    prog_rows = []
    for code, plist in sorted(by_prog.items(), key=lambda x: -len(x[1])):
        s = slug(code); rel = "../../"
        name = PROGRAM_NAMES.get(code, code)
        types = Counter(p["t"] for p in plist); cc = Counter(p.get("cc") for p in plist if p.get("cc"))
        cities = Counter(p.get("city") for p in plist if p.get("city"))
        top = sorted(plist, key=lambda p: -deg[p["id"]])[:25]
        adds = [e for e in recent if code in (e.get("p") or [])][:25]
        body = f"""<h1>{esc(code)}</h1><p class="sub">{esc(name)} · {len(plist):,} parties on the Consolidated Screening List as of {today}</p>
<a class="cta" href="{rel}#prog={esc(code)}">View on the map</a>
<h2>Breakdown</h2><table><tr><th>Kind</th><th class="n">Parties</th></tr>{''.join(f'<tr><td>{esc(k)}</td><td class="n">{v:,}</td></tr>' for k,v in types.most_common())}</table>
<h2>Top countries</h2><table>{''.join(f'<tr><td><a href="{rel}countries/{k.lower()}.html">{esc(iso_name.get(k,k))}</a></td><td class="n">{v:,}</td></tr>' for k,v in cc.most_common(15))}</table>
<h2>Top cities</h2><table>{''.join(f'<tr><td>{esc(k)}</td><td class="n">{v:,}</td></tr>' for k,v in cities.most_common(15))}</table>
<h2>Most connected parties</h2><ul class="plain">{''.join(f'<li>{party_link(p,rel)} <span class="meta">{esc(p["t"])}, {esc(p.get("city") or iso_name.get(p.get("cc"),""))}, {deg[p["id"]]} links</span></li>' for p in top)}</ul>
""" + (f"""<h2>Recently added</h2><ul class="plain">{''.join(f'<li>{esc(e["n"])} <span class="meta">{esc(e["d"])}, {esc(e.get("t",""))}, {esc(iso_name.get(e.get("cc"),""))}</span></li>' for e in adds)}</ul>""" if adds else "")
        write(os.path.join(SITE, "programs", s, "index.html"), page(f"{code} sanctions program", f"{name}: {len(plist):,} sanctioned parties, top countries and cities, most connected entities. Updated nightly from the US Consolidated Screening List.", body, rel, f"{site_url}programs/{s}/"))
        urls.append(f"programs/{s}/"); prog_rows.append((code, name, len(plist), s))
    body = f"""<h1>Sanctions programs</h1><p class="sub">{len(prog_rows)} programs on the Consolidated Screening List, {meta['parties']:,} parties in total.</p>
<table><tr><th>Program</th><th>What it is</th><th class="n">Parties</th></tr>{''.join(f'<tr><td><a href="{s}/">{esc(c)}</a></td><td>{esc(n)}</td><td class="n">{k:,}</td></tr>' for c,n,k,s in prog_rows)}</table>"""
    write(os.path.join(SITE, "programs", "index.html"), page("Sanctions programs", "Every OFAC, BIS and State Department sanctions program with party counts.", body, "../", f"{site_url}programs/", on="Programs"))
    urls.append("programs/")

    # ---------------- countries
    by_cc = defaultdict(list)
    for p in parties:
        if p.get("cc"): by_cc[p["cc"]].append(p)
    c_rows = []
    for cc, plist in sorted(by_cc.items(), key=lambda x: -len(x[1])):
        rel = "../"; name = iso_name.get(cc, cc)
        progs = Counter(g for p in plist for g in p.get("p", [])); types = Counter(p["t"] for p in plist); aus = Counter(a for p in plist for a in p.get("au", ["US"]))
        cities = Counter(p.get("city") for p in plist if p.get("city"))
        top = sorted(plist, key=lambda p: -deg[p["id"]])[:25]
        adds = [e for e in recent if e.get("cc") == cc][:25]
        body = f"""<h1>Sanctioned parties in {esc(name)}</h1><p class="sub">{len(plist):,} parties with an address, nationality or flag in {esc(name)} as of {today}</p>
<a class="cta" href="{rel}#c={cc.lower()}">Open on the map</a>
<h2>Listed by</h2><table>{''.join(f'<tr><td>{esc(AUTH_NAME.get(k,k))}</td><td class="n">{v:,}</td></tr>' for k,v in aus.most_common())}</table>
<h2>Programs</h2><table>{''.join(f'<tr><td><a href="{rel}programs/{slug(k)}/">{esc(k)}</a> <span class="meta">{esc(PROGRAM_NAMES.get(k,""))}</span></td><td class="n">{v:,}</td></tr>' for k,v in progs.most_common(15))}</table>
<h2>Kind</h2><table>{''.join(f'<tr><td>{esc(k)}</td><td class="n">{v:,}</td></tr>' for k,v in types.most_common())}</table>
<h2>Cities</h2><table>{''.join(f'<tr><td>{esc(k)}</td><td class="n">{v:,}</td></tr>' for k,v in cities.most_common(20))}</table>
<h2>Most connected parties</h2><ul class="plain">{''.join(f'<li>{party_link(p,rel)} <span class="meta">{esc(p["t"])}, {esc(p.get("city") or "")}, {deg[p["id"]]} links</span></li>' for p in top)}</ul>
""" + (f"""<h2>Recently added</h2><ul class="plain">{''.join(f'<li>{esc(e["n"])} <span class="meta">{esc(e["d"])}, {esc(e.get("t",""))}, {esc(", ".join(e.get("p") or []))}</span></li>' for e in adds)}</ul>""" if adds else "")
        write(os.path.join(SITE, "countries", f"{cc.lower()}.html"), page(f"Sanctioned parties in {name}", f"{len(plist):,} US-sanctioned or export-controlled parties located in {name}: programs, cities, most connected entities. Updated nightly.", body, rel, f"{site_url}countries/{cc.lower()}.html"))
        urls.append(f"countries/{cc.lower()}.html"); c_rows.append((cc, name, len(plist)))
    body = f"""<h1>Countries</h1><p class="sub">Where the {meta['placed']:,} placeable parties sit.</p><div class="cols"><ul class="plain">{''.join(f'<li><a href="{cc.lower()}.html">{esc(n)}</a> <span class="meta">{k:,}</span></li>' for cc,n,k in c_rows)}</ul></div>"""
    write(os.path.join(SITE, "countries", "index.html"), page("Sanctioned parties by country", "US sanctions and export-control list parties by country.", body, "../", f"{site_url}countries/", on="Countries"))
    urls.append("countries/")

    # ---------------- parties
    for p in paged:
        rel = "../"
        nb = sorted({byid[i]["id"]: byid[i] for i in nbrs[p["id"]] if i in byid}.values(), key=lambda q: -deg[q["id"]])
        loc = p.get("city") or iso_name.get(p.get("cc"), "")
        rows = []
        def dd(k, v):
            if v: rows.append(f"<dt>{esc(k)}</dt><dd>{v}</dd>")
        dd("Kind", esc(p["t"])); dd("Listed by", esc(", ".join(AUTH_NAME.get(a, a) for a in p.get("au", ["US"]))) + (" (matched across lists by name)" if len(p.get("au", [])) > 1 else "")); dd("List", esc(p["s"]) + (" · " + esc(meta["src_list"][p["si"]]) if "si" in p and meta.get("src_list") else ""))
        dd("Programs", ", ".join(f'<a href="{rel}programs/{slug(g)}/">{esc(g)}</a>' for g in p.get("p", [])))
        dd("Address", "<br>".join(esc(a) for a in p.get("a", []))); dd("Also known as", esc("; ".join(p.get("alt", []))))
        dd("Born", esc(p.get("dob"))); dd("Nationality", esc(", ".join(p.get("nat", [])))); dd("Vessel", esc(p.get("ves"))); dd("Listed", esc(p.get("listed")))
        dd("Identifiers", esc(p.get("ids"))); dd("Remarks", esc(p.get("rem")))
        if p.get("recs"): dd("Official records", "<br>".join(f'<a href="{esc(r["url"])}" rel="noopener">{esc(AUTH_NAME.get(r["au"], r["au"]))}</a>' for r in p["recs"] if r.get("url")))
        elif p.get("url"): dd("Official record", f'<a href="{esc(p["url"])}" rel="noopener">{esc(p["url"])}</a>')
        body = f"""<h1>{esc(p["n"])}</h1><p class="sub">{esc(p["t"])} · {esc(loc)} · {deg[p["id"]]} linked parties</p>
<a class="cta" href="{rel}#p={esc(p["id"])}">View on the map</a>
<h2>Record</h2><dl>{''.join(rows)}</dl>
<h2>Linked to</h2><ul class="plain">{''.join(f'<li>{party_link(q,rel)} <span class="meta">{esc(q["t"])}, {esc(q.get("city") or iso_name.get(q.get("cc"),""))}</span></li>' for q in nb)}</ul>"""
        desc = f"{p['n']} ({p['t']}, {loc}) is listed by {', '.join(AUTH_NAME.get(a, a) for a in p.get('au', ['US']))} under {', '.join(p.get('p', [])[:3])}, linked to {deg[p['id']]} other sanctioned parties."
        write(os.path.join(SITE, "parties", f"{p['pg']}.html"), page(p["n"], desc, body, rel, f"{site_url}parties/{p['pg']}.html"))
        urls.append(f"parties/{p['pg']}.html")
    body = f"""<h1>Most connected parties</h1><p class="sub">{len(paged):,} parties with at least {PARTY_PAGE_MIN_LINKS} "Linked To" relationships on the list.</p>
<ul class="plain">{''.join(f'<li><a href="{p["pg"]}.html">{esc(p["n"])}</a> <span class="meta">{esc(p["t"])}, {esc(p.get("city") or iso_name.get(p.get("cc"),""))}, {deg[p["id"]]} links</span></li>' for p in paged)}</ul>"""
    write(os.path.join(SITE, "parties", "index.html"), page("Most connected sanctioned parties", "Sanctioned entities and individuals ranked by how many other listed parties they are linked to.", body, "../", f"{site_url}parties/", on="Parties"))
    urls.append("parties/")

    # ---------------- about / trust page
    AUTH_LONG = {"US": "US Consolidated Screening List (OFAC SDN and non-SDN, BIS Entity List, Denied Persons, Unverified and MEU lists, State Department ISN and AECA)",
                 "EU": "EU Consolidated Financial Sanctions List", "UK": "UK Sanctions List (FCDO)", "UN": "UN Security Council Consolidated List",
                 "AU": "Australia DFAT Consolidated List", "CA": "Canada SEMA and autonomous sanctions list"}
    auth = meta.get("authorities", {})
    inf = Counter(p.get("inf") for p in parties if p.get("inf"))
    n_multi = meta.get("multi_listed", 0)
    body = f"""<h1>About SanctionScope</h1><p class="lede">Where the data comes from, what we do to it, and what the site does and does not claim. An independent project, not affiliated with any government.</p>
<div class="stats"><div class="stat"><b>{meta['parties']:,}</b><span>parties</span></div><div class="stat"><b>{sum(1 for v in auth.values() if v.get('ok'))}</b><span>authorities loaded</span></div><div class="stat"><b>{n_multi:,}</b><span>on more than one list</span></div><div class="stat"><b>{meta['with_city']:,}</b><span>placed to a city</span></div></div>
<h2>Sources, as of this build ({today})</h2>
<table><tr><th>Authority</th><th>List</th><th class="n">Records</th><th>Status</th></tr>{''.join(f'<tr><td>{esc(AUTH_NAME.get(a,a))}</td><td>{esc(AUTH_LONG.get(a,""))}</td><td class="n">{v.get("n",0):,}</td><td>{"loaded" if v.get("ok") else "failed: " + esc(v.get("error","")[:80])}</td></tr>' for a, v in auth.items())}</table>
<p class="meta">Every list is downloaded fresh from the publishing authority each night. If a download or parse fails, that authority is skipped for the night and shown here and on the map as failed; nothing stale is silently kept beyond the previous night's copy.</p>
<h2>What we do to the data</h2>
<p><b>Merging.</b> {meta['parties']:,} parties on this site come from {sum(v.get('n',0) for v in auth.values()):,} source records. Records from different authorities are merged when their normalised names match: legal suffixes are stripped, word order is ignored for people, and two people are never merged if their published birth years conflict. {n_multi:,} parties currently appear on more than one list. Matching is by name and is approximate; every merged party shows its separate official records so you can check.</p>
<p><b>Placement.</b> {meta['with_city']:,} parties are placed at a city named in a listed address, using the GeoNames gazetteer. Parties with a country but no recognised city are spread within the country. {sum(inf.values()):,} parties that publish no address at all are placed by inference: {inf.get('program',0):,} from the country of their sanctions program, {inf.get('group',0):,} from a curated table of where armed groups and criminal organisations operate, {inf.get('remarks',0):,} from a country named in their record. Inferred placements are drawn hollow, labelled in the record, and can be switched off.</p>
<p><b>Links.</b> "Linked To" relationships are taken verbatim from OFAC remarks. No relationships are inferred.</p>
<p><b>Categories and the intensity index.</b> The "why listed" groups and the 0 to 10 country intensity index are this site's own visualisation aids, computed from party counts, program counts and recent activity. They are not official classifications or legal assessments.</p>
<p><b>Change tracking.</b> Additions and removals are detected by comparing each night's build with the previous one, starting from the day the site went live. Designation dates published by the EU, UK, UN, Australian and Canadian lists are shown where available; OFAC does not publish them in the feed we use.</p>
<h2>What this is not</h2>
<p>Not legal advice, not a compliance tool of record, and not a substitute for the official lists. A name match here means "look closer", never "this is the same person". Confirm against the official record, linked from every party, before acting.</p>
<h2>Reuse and attribution</h2>
<p>The source lists are public government publications. The merged dataset is released under CC0 through the <a href="{site_url}api/">API</a>. Attribution to SanctionScope is appreciated, not required.</p>
<p class="small">Source acknowledgements: US Consolidated Screening List, International Trade Administration, US Department of Commerce. EU Consolidated Financial Sanctions List, European Commission, Directorate-General for Financial Stability, Financial Services and Capital Markets Union. UK Sanctions List, Foreign, Commonwealth and Development Office, used under the Open Government Licence v3.0. UN Security Council Consolidated List, United Nations. Consolidated List, Australian Department of Foreign Affairs and Trade, CC BY 4.0. Consolidated Canadian Autonomous Sanctions List, Global Affairs Canada, Open Government Licence Canada. City coordinates from GeoNames, CC BY 4.0.</p>
<h2>Contact</h2><p>Corrections and questions: <a href="mailto:hello@sanctionscope.com">hello@sanctionscope.com</a>. See also the <a href="{site_url}terms.html">terms of service</a> and <a href="{site_url}privacy.html">privacy policy</a>.</p>"""
    write(os.path.join(SITE, "about.html"), page("About", "Where SanctionScope's data comes from, how the six sanctions lists are merged and placed on the map, and what the site does and does not claim.", body, "", f"{site_url}about.html", on="About"))
    urls.append("about.html")

    # ---------------- terms + privacy
    terms = f"""<h1>Terms of service</h1><p class="lede">Plain-language terms for using sanctionscope.com and its API. Last updated {today}.</p>
<h2>What SanctionScope is</h2><p>SanctionScope is a reference and research tool. It republishes, merges and visualises sanctions and export-control lists published by government authorities. It is not legal advice, not a compliance system of record, and not a substitute for the official lists or for professional advice. A name match on this site means only that a listed name resembles the name you searched; it does not establish that they are the same person or entity.</p>
<h2>Accuracy and warranty</h2><p>The data is provided as is and as available. We rebuild it nightly from the official sources but do not guarantee that it is complete, current, correctly merged or correctly placed on the map. Cross-list matching and location inference are automated and approximate, and are labelled as such. You are responsible for verifying any result against the official record, which is linked from every party, before relying on it.</p>
<h2>Limitation of liability</h2><p>To the fullest extent permitted by law, SanctionScope and its operator are not liable for any loss, damage, cost or claim arising from use of or reliance on the site, the API or the data, including decisions to transact or not transact with any party, regulatory outcomes, or business interruption. Our total liability to you for any claim is limited to the amount you paid us in the twelve months before the claim.</p>
<h2>Free use</h2><p>The site, the in-browser screener and the free API tier may be used without an account, at your own risk, subject to reasonable request volumes. We may rate-limit or block abusive traffic.</p>
<h2>API Pro subscription</h2><p>API Pro is a monthly subscription billed through Stripe. You receive an API key that raises the request limits described on the <a href="{site_url}api/">API page</a>. Keys are personal to the subscriber and must not be shared or resold. You may cancel at any time through the <a href="{site_url}api/portal">billing portal</a>; access continues until the end of the paid period. Fees are non-refundable except where required by law. Prices are shown before any applicable tax, which Stripe calculates at checkout. We may change prices with 30 days' notice by email.</p>
<h2>Acceptable use</h2><p>You may not use the site or API to harass, defame or discriminate against any person, to build a competing sanctions-list product by bulk copying the merged data while representing it as your own, or in any way that breaks the law. The underlying merged dataset is released under CC0; these terms govern use of the service, not ownership of public data.</p>
<h2>Changes and termination</h2><p>We may change the service, these terms or the data sources at any time. We may suspend keys that breach these terms. Material changes to the terms take effect 30 days after being posted here.</p>
<h2>Governing law</h2><p>These terms are governed by the laws of the State of Florida, United States, without regard to conflict-of-law rules. Disputes will be brought in the courts of that state.</p>
<h2>Contact</h2><p><a href="mailto:hello@sanctionscope.com">hello@sanctionscope.com</a></p>"""
    write(os.path.join(SITE, "terms.html"), page("Terms of service", "Terms for using SanctionScope and its API: what the service is, accuracy, liability, subscriptions and acceptable use.", terms, "", f"{site_url}terms.html"))
    privacy = f"""<h1>Privacy policy</h1><p class="lede">What we collect, which is very little, and why. Last updated {today}.</p>
<h2>What we do not collect</h2><p>The site sets no cookies of its own and runs no analytics or advertising scripts. Names you type into the in-browser screener are processed on your own device and are never sent to us. Names sent to the screening API are processed in memory to produce a response and are not stored or logged beyond the ordinary short-lived request logs of our hosting provider.</p>
<h2>What we collect from API Pro subscribers</h2><p>When you subscribe, Stripe collects your email, payment details and billing address under <a href="https://stripe.com/privacy">Stripe's privacy policy</a>; we never see your card number. Stripe passes us your email address and a customer identifier, which we store together with your API key so that we can issue, check and deactivate the key. We keep this for as long as you have an account and for up to 90 days after cancellation, then delete it. We use your email only for service messages such as price changes or outages; no marketing.</p>
<h2>Hosting and processors</h2><p>The site is served by Cloudflare, which processes visitor IP addresses to deliver content and prevent abuse under <a href="https://www.cloudflare.com/privacypolicy/">Cloudflare's privacy policy</a>. Payments are processed by Stripe. Nobody else receives your data.</p>
<h2>Data about listed parties</h2><p>The names, addresses and other details of sanctioned parties shown on this site are republished from official government sanctions lists in the public interest. If you believe a record about you is inaccurate, the correction has to be made by the listing authority; each record links to its official source. We will correct errors we introduce, such as a wrong merge or placement, on request.</p>
<h2>Your rights</h2><p>You can ask what we hold about you, ask us to delete it, or cancel your subscription, by emailing <a href="mailto:hello@sanctionscope.com">hello@sanctionscope.com</a>. If you are in the EU, UK or California you have additional statutory rights, which we will honour on request.</p>
<h2>Changes</h2><p>Changes to this policy are posted here with a new date.</p>"""
    write(os.path.join(SITE, "privacy.html"), page("Privacy policy", "What SanctionScope collects and does not collect, how API subscriber data is handled, and your rights.", privacy, "", f"{site_url}privacy.html"))
    urls += ["terms.html", "privacy.html"]

    # ---------------- sitemap + robots
    if site_url:
        sm = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
              f"<url><loc>{esc(site_url)}</loc><lastmod>{today}</lastmod><changefreq>daily</changefreq></url>"]
        sm += [f"<url><loc>{esc(site_url + u)}</loc><lastmod>{today}</lastmod></url>" for u in urls]
        sm.append("</urlset>")
        write(os.path.join(SITE, "sitemap.xml"), "\n".join(sm))
        write(os.path.join(SITE, "robots.txt"), f"User-agent: *\nAllow: /\nSitemap: {site_url}sitemap.xml\n")
    return len(prog_rows), len(c_rows), len(paged)

if __name__ == "__main__":
    import sys
    print(build_pages(sys.argv[1] if len(sys.argv) > 1 else ""))
