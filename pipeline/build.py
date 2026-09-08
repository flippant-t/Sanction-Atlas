#!/usr/bin/env python3
"""
SanctionScope nightly build.

Downloads the US Consolidated Screening List (OFAC SDN + non-SDN, BIS Entity List,
BIS Denied Persons / Unverified / MEU, State Department lists), geocodes every
party to a city where possible, resolves "Linked To" relationships from OFAC
remarks, diffs against the previous run, and writes static JSON for the site.

Usage:
  python pipeline/build.py                 # download live data
  python pipeline/build.py --input x.csv   # use a local CSV (offline / testing)

Outputs (all under site/data/):
  parties.json   every party, compact keys, with coordinates and edges
  changes.json   additions and removals by date, and a count series
  meta.json      build time, counts, program list, source list
  state.json     internal: id -> first_seen / last_seen, used for the diff
"""
import argparse, csv, io, json, os, re, sys, unicodedata, datetime as dt
from collections import defaultdict, Counter

import geonamescache

CSL_URL = "https://data.trade.gov/downloadable_consolidated_screening_list/v1/consolidated.csv"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "site", "data")
KEEP_DAYS = 120          # how many days of add/remove events to keep in changes.json

# ---------------------------------------------------------------- countries
gc = geonamescache.GeonamesCache()
COUNTRIES = gc.get_countries()            # iso2 -> {name, isonumeric, ...}
NAME2ISO = {}
for iso, c in COUNTRIES.items():
    NAME2ISO[c["name"].lower()] = iso
ALIAS = {
    "russian federation": "RU", "russia": "RU", "ussr": "RU", "soviet union": "RU",
    "iran": "IR", "iran, islamic republic of": "IR", "islamic republic of iran": "IR",
    "korea, north": "KP", "north korea": "KP", "democratic people's republic of korea": "KP", "dprk": "KP",
    "korea, democratic people's republic of": "KP",
    "korea, south": "KR", "south korea": "KR", "republic of korea": "KR", "korea": "KR", "korea, republic of": "KR",
    "syria": "SY", "syrian arab republic": "SY", "burma": "MM", "myanmar": "MM", "myanmar (burma)": "MM",
    "congo, democratic republic of the": "CD", "democratic republic of the congo": "CD", "drc": "CD",
    "congo, the democratic republic of the": "CD", "congo (kinshasa)": "CD",
    "congo": "CG", "republic of the congo": "CG", "congo, republic of the": "CG", "congo (brazzaville)": "CG",
    "united states": "US", "usa": "US", "us": "US", "u.s.a.": "US", "united states of america": "US",
    "viet nam": "VN", "vietnam": "VN", "czech republic": "CZ", "czechia": "CZ",
    "turkiye": "TR", "türkiye": "TR", "turkey": "TR",
    "bosnia and herzegovina": "BA", "bosnia-herzegovina": "BA", "bosnia": "BA",
    "north macedonia": "MK", "macedonia": "MK", "macedonia, the former yugoslav republic of": "MK",
    "cote d'ivoire": "CI", "côte d'ivoire": "CI", "ivory coast": "CI",
    "united arab emirates": "AE", "uae": "AE", "u.a.e.": "AE",
    "united kingdom": "GB", "uk": "GB", "great britain": "GB", "england": "GB", "scotland": "GB", "wales": "GB",
    "northern ireland": "GB",
    "laos": "LA", "lao people's democratic republic": "LA", "lao pdr": "LA",
    "palestine": "PS", "palestinian": "PS", "west bank": "PS", "gaza": "PS", "gaza strip": "PS",
    "palestinian territories": "PS", "state of palestine": "PS", "region: gaza": "PS", "region: west bank": "PS",
    "taiwan": "TW", "taiwan, province of china": "TW", "republic of china": "TW",
    "china": "CN", "people's republic of china": "CN", "prc": "CN",
    "hong kong": "HK", "hong kong sar": "HK", "hong kong, china": "HK", "macau": "MO", "macao": "MO",
    "moldova": "MD", "moldova, republic of": "MD", "republic of moldova": "MD", "transnistria": "MD",
    "tanzania": "TZ", "tanzania, united republic of": "TZ", "united republic of tanzania": "TZ",
    "brunei": "BN", "brunei darussalam": "BN", "cape verde": "CV", "cabo verde": "CV",
    "micronesia, federated states of": "FM", "federated states of micronesia": "FM", "micronesia": "FM",
    "saint vincent": "VC", "st. vincent and the grenadines": "VC", "st vincent and the grenadines": "VC",
    "saint kitts": "KN", "st. kitts and nevis": "KN", "st kitts and nevis": "KN", "st. lucia": "LC", "st lucia": "LC",
    "virgin islands, british": "VG", "british virgin islands": "VG", "bvi": "VG",
    "virgin islands, u.s.": "VI", "us virgin islands": "VI", "u.s. virgin islands": "VI", "virgin islands": "VI",
    "curaçao": "CW", "curacao": "CW", "sint maarten": "SX", "st. maarten": "SX",
    "holy see": "VA", "vatican city": "VA", "vatican": "VA",
    "crimea": "UA", "crimea region of ukraine": "UA", "ukraine (crimea)": "UA",
    "the netherlands": "NL", "holland": "NL", "netherlands antilles": "CW",
    "libyan arab jamahiriya": "LY", "slovak republic": "SK",
    "bolivia, plurinational state of": "BO", "venezuela, bolivarian republic of": "VE",
    "kyrgyz republic": "KG", "byelarus": "BY", "the sudan": "SD",
    "gambia, the": "GM", "the gambia": "GM", "bahamas, the": "BS", "the bahamas": "BS",
    "kingdom of saudi arabia": "SA", "ksa": "SA", "trinidad": "TT",
    "north cyprus": "CY", "northern cyprus": "CY", "turkish republic of northern cyprus": "CY",
    "kosovo": "XK", "serbia and montenegro": "RS", "yugoslavia": "RS", "somaliland": "SO",
    "canary islands": "ES", "reunion": "RE", "réunion": "RE",
    "eswatini": "SZ", "swaziland": "SZ", "timor-leste": "TL", "east timor": "TL",
    "western sahara": "EH", "cayman": "KY", "iraqi": "IQ", "united states minor outlying islands": "UM",
    "undetermined": None, "unknown": None, "n/a": None, "none": None, "": None,
}

def norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", s).strip().lower()

def country_iso(token):
    raw = (token or "").strip()
    if len(raw) == 2 and raw.upper() in COUNTRIES: return raw.upper()     # CSL uses ISO2 codes: "RU", "IR", "AF"
    if raw.upper() == "XK": return "XK"
    k = norm(raw).rstrip(".")
    if k in ALIAS: return ALIAS[k]
    if k in NAME2ISO: return NAME2ISO[k]
    return None

# ------------------------------------------------------------------- cities
CITY = defaultdict(list)       # (iso2, normname) -> [(pop, lat, lon, name)]
CITY_ANY = defaultdict(list)   # normname -> [(pop, lat, lon, name, iso2)]
for c in gc.get_cities().values():
    rec = (c["population"], c["latitude"], c["longitude"], c["name"])
    names = {c["name"]} | set(c.get("alternatenames") or [])
    for n in names:
        k = norm(n)
        if len(k) < 3: continue
        CITY[(c["countrycode"], k)].append(rec)
        CITY_ANY[k].append(rec + (c["countrycode"],))
for d in (CITY, CITY_ANY):
    for k in d: d[k].sort(reverse=True)
# a few extra spellings that show up constantly in OFAC data
EXTRA = {("RU", "moskva"): ("RU", "moscow"), ("RU", "st. petersburg"): ("RU", "saint petersburg"),
         ("RU", "st petersburg"): ("RU", "saint petersburg"), ("RU", "sankt-peterburg"): ("RU", "saint petersburg"),
         ("IR", "teheran"): ("IR", "tehran"), ("CN", "hong kong"): ("HK", "hong kong"),
         ("AE", "jebel ali"): ("AE", "dubai"), ("KP", "pyongyang"): ("KP", "pyongyang"),
         ("PA", "panama"): ("PA", "panama city"), ("GT", "guatemala"): ("GT", "guatemala city"),
         ("KW", "kuwait"): ("KW", "kuwait city"), ("MX", "mexico"): ("MX", "mexico city"),
         ("SG", "singapore"): ("SG", "singapore"), ("BH", "bahrain"): ("BH", "manama"),
         ("CN", "kowloon"): ("HK", "kowloon"), ("CN", "wan chai"): ("HK", "hong kong"), ("CN", "tsim sha tsui"): ("HK", "hong kong"), ("CN", "sheung wan"): ("HK", "hong kong"), ("CN", "macau"): ("MO", "macau"), ("CN", "macao"): ("MO", "macau")}

CITY_STATES = {"HK", "SG", "MO", "MC", "VA", "GI", "BH", "QA", "KW", "LU", "MT", "DJ"}
# small offshore / secrecy hubs missing from the 15k-population gazetteer
for (cc, nm, lat, lon) in [("VG", "road town", 18.4286, -64.6185), ("VG", "tortola", 18.4286, -64.6185),
        ("KY", "grand cayman", 19.2866, -81.3674), ("KN", "charlestown", 17.1380, -62.6217), ("KN", "nevis", 17.1380, -62.6217),
        ("SC", "victoria", -4.6236, 55.4522), ("SC", "mahe", -4.6236, 55.4522), ("MH", "majuro", 7.0897, 171.3803),
        ("MH", "ajeltake", 7.0897, 171.3803), ("VU", "port vila", -17.7338, 168.3219), ("WS", "apia", -13.8333, -171.7667),
        ("LI", "vaduz", 47.1410, 9.5209), ("LI", "schaan", 47.1655, 9.5100), ("IM", "douglas", 54.1500, -4.4800),
        ("JE", "st helier", 49.1881, -2.1057), ("JE", "saint helier", 49.1881, -2.1057), ("GG", "st peter port", 49.4594, -2.5353),
        ("AI", "the valley", 18.2170, -63.0578), ("TC", "providenciales", 21.7833, -72.2667), ("BZ", "belize city", 17.4995, -88.1976),
        ("CW", "willemstad", 12.1091, -68.9316), ("AE", "ras al khaimah", 25.7895, 55.9432), ("AE", "ajman", 25.4111, 55.4354),
        ("AE", "fujairah", 25.1288, 56.3265), ("AE", "umm al quwain", 25.5647, 55.5552), ("IR", "kish island", 26.5578, 54.0194),
        ("KP", "rason", 42.2556, 130.2831), ("KP", "sinuiju", 40.1006, 124.3982), ("KP", "nampo", 38.7375, 125.4072),
        ("KP", "hamhung", 39.9183, 127.5364), ("KP", "chongjin", 41.7956, 129.7758), ("KP", "wonsan", 39.1528, 127.4436),
        ("SY", "deir ez-zor", 35.3359, 40.1408), ("SY", "raqqa", 35.9500, 39.0100), ("YE", "hodeidah", 14.7978, 42.9545),
        ("YE", "al hudaydah", 14.7978, 42.9545), ("YE", "saada", 16.9400, 43.7636), ("SD", "port sudan", 19.6158, 37.2164),
        ("MV", "male", 4.1755, 73.5093)]:
    CITY[(cc, nm)].append((1, lat, lon, nm.title()))

NOISE = re.compile(r"\b(p\.?o\.? ?box|suite|ste\.?|floor|fl\.?|unit|building|bldg|street|str\.?|st\.|road|rd\.|avenue|ave\.|district|province|region|oblast|governorate|state|county|prefecture|area|no\.)\b", re.I)

def clean_tok(t):
    t = re.sub(r"\d[\d\-\s/]*", " ", t)          # postal codes, house numbers
    t = re.sub(r"[()\"']", " ", t)
    t = NOISE.sub(" ", t)
    return norm(t)

def geocode(addr):
    """addr: 'Street, City, State Postal, Country' -> (iso2, lat, lon, city_name) with lat/lon None if only country."""
    toks = [t.strip() for t in addr.split(",") if t.strip()]
    iso = None; ctoks = set()
    for i in range(len(toks) - 1, max(-1, len(toks) - 4), -1):
        # two-token country names first: "Korea, North", "Iran, Islamic Republic of", "Congo, Democratic Republic of the"
        if i >= 1:
            iso = country_iso(toks[i - 1] + ", " + toks[i])
            if iso: ctoks = {i - 1, i}; break
        iso = country_iso(toks[i])
        if iso: ctoks = {i}; break
    # candidate city tokens: everything except the country tokens (but a city-state's name is also its city)
    cands = []
    STREETY = re.compile(r"\b(rue|str|strasse|ul|ulitsa|prospekt|pr|via|calle|avenida|carrera|road|rd|street|st|avenue|ave|lane|ln|blvd|boulevard|highway|hwy|floor|fl|suite|ste|unit|room|rm|block|bldg|building|tower|plaza|po box|p o box|no|km)\b", re.I)
    for i, t in enumerate(toks):
        k = clean_tok(t)
        if not k: continue
        if i in ctoks:
            # "Panama, Panama" / "Hong Kong" / "Singapore": the country token doubles as the city
            if (len(toks) > 1 or iso in CITY_STATES) and EXTRA.get((iso, k), (iso, k)) in CITY:
                cands.insert(0, k)   # lowest priority: real city tokens win
            continue
        t_iso = country_iso(t)
        if t_iso and t_iso != iso: continue          # another country's name inside the address: not a city
        # street lines ("8 Rue de la Bruyere") must not feed word-runs into the gazetteer: "bruyere" is not a city
        if not (re.search(r"\d", t) or STREETY.search(t) or len(k.split()) > 4):
            parts = k.split()
            for n in (1, 2):          # shorter runs first; the list is scanned in reverse so longer matches win
                for j in range(len(parts) - n + 1):
                    cands.append(" ".join(parts[j:j + n]))
        cands.append(k)
    seen = set()
    for k in reversed(cands):
        if k in seen or len(k) < 3: continue
        seen.add(k)
        key = EXTRA.get((iso, k), (iso, k))
        if key[0] and key in CITY:
            pop, lat, lon, name = CITY[key][0]
            return key[0], lat, lon, name
    if iso is None:
        # no country recognised: accept a big city match anywhere (pop >= 250k)
        for k in reversed(cands):
            if k in CITY_ANY and CITY_ANY[k][0][0] >= 250000:
                pop, lat, lon, name, cc = CITY_ANY[k][0]
                return cc, lat, lon, name
    return iso, None, None, None

# ------------------------------------------------------------------ parsing
AUTH_ORDER = ["US", "EU", "UK", "UN", "AU", "CA"]
def source_key(s, auth="US"):
    if auth != "US": return auth
    t = (s or "").lower()
    if "specially designated" in t: return "OFAC SDN"
    if "non-sdn" in t or "sectoral" in t or "treasury" in t: return "OFAC other"
    if "entity list" in t: return "BIS Entity List"
    if "bureau of industry" in t or "denied" in t or "unverified" in t or "military end" in t: return "BIS other"
    return "State / other"

def party_type(t):
    s = (t or "").lower()
    if s.startswith("ind"): return "Individual"
    if s.startswith("ves"): return "Vessel"
    if s.startswith("air"): return "Aircraft"
    return "Entity"

def split(s):
    return [x.strip() for x in (s or "").split(";") if x.strip()]

def name_key(s):
    return re.sub(r"[^A-Z0-9 ]", "", norm(s).upper()).strip()

def load_rows(args):
    status = {}
    if args.input:
        with open(args.input, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        status["US"] = {"ok": True, "n": len(rows), "error": ""}
    else:
        import requests
        r = requests.get(CSL_URL, timeout=180, headers={"User-Agent": "sanctionscope-build"})
        r.raise_for_status()
        rows = list(csv.DictReader(io.StringIO(r.content.decode("utf-8-sig"))))
        status["US"] = {"ok": True, "n": len(rows), "error": ""}
    for r in rows: r.setdefault("authority", "US")   # a local CSV may carry an authority column (testing)
    if not args.us_only:
        import sources
        more, st = sources.load_all(args.sample_dir, only=args.only.split(",") if args.only else None)
        rows += more; status.update(st)
    return rows, status

LEGAL = {"LLC", "LTD", "LIMITED", "INC", "CORP", "CORPORATION", "CO", "COMPANY", "GMBH", "AG", "SA", "SAS", "SARL", "BV", "NV", "PLC", "PJSC", "JSC", "OJSC", "CJSC",
         "OAO", "ZAO", "OOO", "AO", "PAO", "TOO", "LLP", "LP", "SRL", "SPA", "SL", "PTE", "PTY", "PVT", "FZE", "FZCO", "FZC", "DMCC", "THE", "OF", "AND", "GROUP", "HOLDING", "HOLDINGS",
         "PUBLIC", "JOINT", "STOCK", "OPEN", "CLOSED", "OBSHCHESTVO", "OGRANICHENNOY", "OTVETSTVENNOSTYU", "AKTSIONERNOE", "PUBLICHNOE", "ZAKRYTOE", "OTKRYTOE", "S", "OTVETSTVENNOSTIU"}
GENERIC = {"BANK", "RUSSIA", "RUSSIAN", "FEDERATION", "TRADING", "TRADE", "INTERNATIONAL", "INDUSTRIES", "INDUSTRIAL", "INDUSTRY", "TECHNOLOGY", "TECHNOLOGIES",
           "SERVICES", "SERVICE", "SHIPPING", "ENGINEERING", "ENTERPRISE", "ENTERPRISES", "IRAN", "IRANIAN", "KOREA", "KOREAN", "NATIONAL", "GENERAL", "CENTRAL",
           "STATE", "DEVELOPMENT", "INVESTMENT", "INVESTMENTS", "PETROLEUM", "OIL", "GAS", "ELECTRONICS", "ELECTRONIC", "MACHINERY", "EQUIPMENT", "SCIENTIFIC",
           "RESEARCH", "INSTITUTE", "CENTER", "CENTRE", "PLANT", "FACTORY", "WORKS", "PRODUCTION", "SYSTEMS", "SYSTEM", "AVIATION", "MARINE", "MARITIME", "LOGISTICS",
           "TRANSPORT", "EXPORT", "IMPORT", "FINANCE", "FINANCIAL", "CAPITAL", "CREDIT", "INSURANCE", "SECURITY", "DEFENSE", "DEFENCE", "MILITARY", "SHIP", "AIR",
           "UNITED", "GLOBAL", "WORLD", "NEW", "FIRST", "MINISTRY", "DEPARTMENT", "BUREAU", "OFFICE", "AGENCY", "ORGANIZATION", "ORGANISATION", "FOUNDATION",
           "FUND", "UNION", "ASSOCIATION", "COUNCIL", "COMMITTEE", "CORPS", "FORCE", "FORCES", "ARMY", "NAVY", "GUARD", "GUARDS", "PEOPLES", "PEOPLE", "DEMOCRATIC",
           "REPUBLIC", "ISLAMIC", "REVOLUTIONARY", "SYRIAN", "SYRIA", "CHINA", "CHINESE", "BELARUS", "BELARUSIAN", "UKRAINE", "UKRAINIAN", "VENEZUELA", "CUBA",
           "MYANMAR", "BURMA", "LIBYA", "LIBYAN", "IRAQ", "IRAQI", "AFGHAN", "TURKISH", "ARAB", "GULF", "PACIFIC", "ATLANTIC", "EAST", "WEST", "NORTH", "SOUTH",
           "MOSCOW", "TEHRAN", "PYONGYANG", "DUBAI", "HONG", "KONG", "SHANGHAI", "BEIJING"}
def match_key(name, typ):
    k = re.sub(r"[^A-Z0-9 ]", " ", norm(name).upper())
    toks = [t for t in k.split() if t]
    if typ != "Individual":
        toks = [t for t in toks if t not in LEGAL]
        if not toks or sum(len(t) for t in toks) < 6: return None
        if len(toks) < 3 and not any(t not in GENERIC for t in toks): return None   # "BANK RUSSIA", "IRAN TRADING": too generic to identify anything
        return "E:" + " ".join(toks)
    toks = sorted(t for t in toks if len(t) > 1)
    if len(toks) < 2: return None
    return "I:" + " ".join(toks)

def years(s):
    return set(re.findall(r"(?<!\d)(1[89]\d\d|20\d\d)(?!\d)", s or ""))

def merge_across_authorities(parties):
    """Union parties from different authorities that share a normalised name (or alias)
    and do not conflict on type or birth year. Same-authority records are never merged."""
    parent = list(range(len(parties)))
    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]; i = parent[i]
        return i
    def union(i, j):
        a, b = find(i), find(j)
        if a != b: parent[max(a, b)] = min(a, b)
    index = {}
    for i, p in enumerate(parties):
        keys = {match_key(p["n"], p["t"])}
        # aliases join the match only when specific enough (three or more tokens)
        keys |= {k for k in (match_key(a, p["t"]) for a in p["alt"][:20]) if k and len(k.split()) >= 4}
        for k in keys:
            if not k: continue
            for j in index.get(k, []):
                q = parties[j]
                # the same authority listing one company under two regimes is one company; people are only merged across lists
                if q["au"][0] == p["au"][0] and not (p["t"] == "Entity" and q["t"] == "Entity"): continue
                if p["t"] == "Individual" and q["t"] == "Individual":
                    ya, yb = years(p["dob"]), years(q["dob"])
                    if ya and yb and not (ya & yb): continue
                union(i, j)
            index.setdefault(k, []).append(i)
    groups = {}
    for i in range(len(parties)): groups.setdefault(find(i), []).append(i)
    merged = []
    for root, idxs in groups.items():
        members = sorted((parties[i] for i in idxs), key=lambda p: AUTH_ORDER.index(p["au"][0]))
        base = members[0]
        if len(members) == 1:
            merged.append(base); continue
        for m in members[1:]:
            if m["au"][0] not in base["au"]: base["au"].append(m["au"][0])
            base["recs"] += m["recs"]
            base["p"] = sorted(set(base["p"]) | set(m["p"]))
            have = {norm(x["raw"]) for x in base["a"]}
            for a in m["a"]:
                if norm(a["raw"]) not in have: base["a"].append(a); have.add(norm(a["raw"]))
            for a in [m["n"]] + m["alt"]:
                if name_key(a) != name_key(base["n"]) and a not in base["alt"]: base["alt"].append(a)
            for n in m["nat"]:
                if n not in base["nat"]: base["nat"].append(n)
            if m["dob"] and m["dob"] not in base["dob"]: base["dob"] = "; ".join(x for x in [base["dob"], m["dob"]] if x)
            if m["pob"] and not base["pob"]: base["pob"] = m["pob"]
            if m["rem"]: base["rem"] = "\n".join(x for x in [base["rem"], f"[{m['au'][0]}] {m['rem']}"] if x)
            if m["ids"]: base["ids"] = "; ".join(x for x in [base["ids"], m["ids"]] if x)
            if m["ves"] and not base["ves"]: base["ves"] = m["ves"]
            if m["flag"] and not base["flag"]: base["flag"] = m["flag"]
        base["au"] = sorted(set(base["au"]), key=AUTH_ORDER.index)
        merged.append(base)
    return merged

# country-name spotting in free text: longest names first so "South Sudan" beats "Sudan", "North Korea" beats "Korea"
_CTEXT = sorted([(k, v) for k, v in list(NAME2ISO.items()) + list(ALIAS.items()) if v and len(k) > 3 and k not in ("us", "uk", "prc", "drc", "uae", "ksa", "bvi", "dprk", "korea", "congo")], key=lambda x: -len(x[0]))
_CTEXT_RE = re.compile(r"\b(" + "|".join(re.escape(k) for k, _ in _CTEXT) + r")\b", re.I)
_CTEXT_MAP = {k: v for k, v in _CTEXT}
def country_in_text(text):
    if not text: return None
    hits = Counter(_CTEXT_MAP[m.lower()] for m in _CTEXT_RE.findall(norm(text)) if m.lower() in _CTEXT_MAP)
    return hits.most_common(1)[0][0] if hits else None

# (regex on name or alias, ISO2, label). Where a group operates, for groups that publish no address.
GROUP_AREAS = [
    (r"\bal[- ]?qa[i']?da in the arabian peninsula|\bAQAP\b|ansar al[- ]sharia in yemen|houthi|ansar ?allah|\bhuthi", "YE", "Yemen"),
    (r"al[- ]?qa[i']?da in the islamic maghreb|\bAQIM\b|jama'?at nusrat al[- ]islam|\bJNIM\b|ansar (al[- ])?dine|macina|islamic state in the greater sahara|\bISGS\b", "ML", "Mali and the Sahel"),
    (r"al[- ]?shabaab|harakat shabaab", "SO", "Somalia"),
    (r"boko haram|islamic state west africa|\bISWAP\b|ansaru", "NG", "Nigeria"),
    (r"\bISIL\b|\bISIS\b|islamic state of iraq|islamic state in iraq|da'?esh|al[- ]nusrah|nusra front|hay'?at tahrir al[- ]sham|\bHTS\b|hurras al[- ]din|ahrar al[- ]sham|jaysh al[- ]islam", "SY", "Syria and Iraq"),
    (r"kata'?ib hi?zb|asa'?ib ahl|harakat (hi?zb)?allah al[- ]nujaba|badr organi[sz]ation|ansar al[- ]islam|islamic state.*iraq|popular mobili[sz]ation|kata'?ib sayyid", "IQ", "Iraq"),
    (r"\bhamas\b|izz ?al[- ]din|qassam|palestinian islamic jihad|\bPIJ\b|popular front for the liberation of palestine|\bPFLP\b|al[- ]aqsa martyrs|palestinian", "PS", "Gaza and the West Bank"),
    (r"hi?zb[ao]ll?ah(?! al)|\bhezbollah\b|jihad al[- ]bina|al[- ]qard al[- ]hassan|al[- ]manar", "LB", "Lebanon"),
    (r"\btaliban\b|haqqani|islamic emirate of afghanistan|\bal[- ]?qa[i']?da\b(?! in)|\bal[- ]?qaeda\b(?! in)", "AF", "Afghanistan and Pakistan"),
    (r"tehrik[- ]e[- ]taliban|\bTTP\b|lashkar[- ]e[- ]tayyiba|lashkar[- ]e[- ]taiba|\bLeT\b|jaish[- ]e[- ]mohamm?ed|\bJeM\b|harakat ul[- ]mujahid|jamaat[- ]ud[- ]dawa|lashkar[- ]i?[- ]jhangvi|al[- ]qa[i']?da in the indian subcontinent|\bAQIS\b", "PK", "Pakistan"),
    (r"abu sayyaf|maute|bangsamoro islamic freedom|jemaah islami|\bJI\b(?![A-Z])|mujahidin indonesia timur|ansharut daulah", "PH", "Philippines and Indonesia"),
    (r"kurdistan workers'? party|\bPKK\b|kongra[- ]gel|revolutionary people'?s liberation party|\bDHKP", "TR", "Turkey"),
    (r"\bFARC\b|revolutionary armed forces of colombia|ejercito de liberacion nacional|\bELN\b|clan del golfo|gulf clan|segunda marquetalia", "CO", "Colombia"),
    (r"sendero luminoso|shining path", "PE", "Peru"),
    (r"\bETA\b|euskadi ta askatasuna|basque fatherland", "ES", "Spain"),
    (r"real IRA|continuity IRA|irish republican army|\bIRA\b|ulster", "GB", "Northern Ireland"),
    (r"allied democratic forces|\bADF\b|m23|fdlr|codeco|mai[- ]mai", "CD", "Democratic Republic of the Congo"),
    (r"lord'?s resistance army|\bLRA\b", "UG", "Uganda and Central Africa"),
    (r"rapid support forces|\bRSF\b|janjaweed", "SD", "Sudan"),
    (r"wagner|africa corps|redut", "RU", "Russia (operating abroad)"),
    (r"islamic revolutionary guard|\bIRGC\b|quds force|basij|ministry of intelligence and security|\bMOIS\b", "IR", "Iran"),
    (r"korea .*(mining|trading|development|bank)|choson|korean people'?s army|reconnaissance general bureau|munitions industry|workers'? party of korea|koryo|ryonbong|tangun", "KP", "North Korea"),
    (r"sinaloa|jalisco|cartel|c[aá]rtel|los zetas|beltr[aá]n|guerreros unidos|la familia|caballeros templarios|nueva plaza", "MX", "Mexico"),
    (r"primeiro comando|comando vermelho", "BR", "Brazil"),
    (r"tren de aragua", "VE", "Venezuela"),
    (r"\bMS-?13\b|mara salvatrucha|barrio 18", "SV", "El Salvador"),
    (r"yakuza|yamaguchi[- ]gumi|inagawa|sumiyoshi", "JP", "Japan"),
    (r"'?ndrangheta|camorra|cosa nostra|sacra corona", "IT", "Italy"),
    (r"thieves[- ]in[- ]law|brothers'? circle|solntsev", "RU", "Russia"),
    (r"kinahan", "IE", "Ireland"),
    (r"14k|sun yee on|wo shing wo|triad", "HK", "Hong Kong"),
]

def build(rows):
    parties = []
    for r in rows:
        name = (r.get("name") or "").strip()
        if not name: continue
        src = r.get("source") or ""
        auth = r.get("authority") or "US"
        pid = f"{source_key(src, auth)[:3].lower()}:{r.get('entity_number') or r.get('_id') or name_key(name)}"
        addrs = []
        for a in split(r.get("addresses")):
            iso, lat, lon, city = geocode(a)
            addrs.append({"raw": a, "cc": iso, "lat": lat, "lon": lon, "city": city})
        nat = [n for n in split(r.get("nationalities")) + split(r.get("citizenships"))]
        progs = split(r.get("programs"))
        if not progs:
            # BIS and State entries usually carry no program code; use the list itself so it can be filtered and categorised
            sl = src.lower()
            progs = ["ENTITY-LIST" if "entity list" in sl else "DENIED-PERSONS" if "denied" in sl else "UNVERIFIED-LIST" if "unverified" in sl
                     else "MEU-LIST" if "military end" in sl else "ISN" if "nonproliferation" in sl else "AECA-DEBARRED" if "debar" in sl
                     else "CAPTA" if "capta" in sl else "FSE" if "foreign sanctions evaders" in sl else "NS-" + re.sub(r"[^A-Z0-9]+", "-", src.split(" - ")[0].upper()).strip("-")[:30] if "non-sdn" in sl
                     else re.sub(r"[^A-Z0-9]+", "-", src.split(" - ")[0].upper()).strip("-")[:30]]
        p = {
            "id": pid, "n": name, "t": party_type(r.get("type")), "s": source_key(src, auth), "src": src, "au": [auth],
            "recs": [{"au": auth, "src": src, "url": r.get("source_information_url") or r.get("source_list_url") or "", "p": progs, "listed": r.get("start_date") or ""}],
            "p": progs, "a": addrs, "ti": r.get("title") or "",
            "alt": split(r.get("alt_names")), "dob": r.get("dates_of_birth") or "",
            "nat": nat, "pob": r.get("places_of_birth") or "", "rem": r.get("remarks") or "",
            "ids": r.get("ids") or "", "url": r.get("source_information_url") or r.get("source_list_url") or "",
            "ves": " / ".join(x for x in [r.get("vessel_type"), r.get("vessel_flag"), r.get("call_sign"), r.get("vessel_owner")] if x),
            "listed": r.get("start_date") or "", "fr": r.get("federal_register_notice") or "",
            "flag": r.get("vessel_flag") or "",
        }
        parties.append(p)

    # de-duplicate ids (CSL occasionally repeats an entity_number across sources)
    seen = {}
    for p in parties:
        base = p["id"]; i = 1
        while p["id"] in seen:
            i += 1; p["id"] = f"{base}#{i}"
        seen[p["id"]] = p

    parties = merge_across_authorities(parties)

    # placement: first address with a city, else first with a country, else nationality country
    for p in parties:
        loc = None
        for a in p["a"]:
            if a["lat"] is not None: loc = a; break
        if loc is None:
            for a in p["a"]:
                if a["cc"]: loc = a; break
        if loc is None:
            for n in p["nat"]:
                iso = country_iso(n)
                if iso:
                    loc = {"raw": f"(nationality: {COUNTRIES.get(iso, {}).get('name', n)})", "cc": iso, "lat": None, "lon": None, "city": None, "fb": True}
                    break
        if loc is None and p["pob"]:
            iso, lat, lon, city = geocode(p["pob"])
            if iso:
                loc = {"raw": f"(place of birth: {p['pob']})", "cc": iso, "lat": lat, "lon": lon, "city": city, "fb": True}
        if loc is None and p["flag"]:
            iso = country_iso(p["flag"])
            if iso:
                loc = {"raw": f"(flag: {COUNTRIES.get(iso, {}).get('name', p['flag'])})", "cc": iso, "lat": None, "lon": None, "city": None, "fb": True}
        if loc: p["a"].append(loc)
        p["cc"] = loc["cc"] if loc else None
        p["lat"] = loc["lat"] if loc else None
        p["lon"] = loc["lon"] if loc else None

    # a country-specific program is the next best hint for a party with no address, nationality or flag
    REGIME_CC = {"SYRIA": "SY", "CUBA": "CU", "VENEZUELA": "VE", "DPRK": "KP", "IRAN": "IR", "BELARUS": "BY", "RUSSIA": "RU", "LIBYA": "LY", "IRAQ": "IQ",
        "YEMEN": "YE", "SOMALIA": "SO", "SOUTH SUDAN": "SS", "SUDAN": "SD", "DARFUR": "SD", "MALI": "ML", "DRCONGO": "CD", "CAR": "CF", "BURMA": "MM", "MYANMAR": "MM",
        "NICARAGUA": "NI", "ZIMBABWE": "ZW", "LEBANON": "LB", "ETHIOPIA": "ET", "HK-": "HK", "HAITI": "HT", "AFGHANISTAN": "AF", "UKRAINE": "UA", "CRIMEA": "UA",
        "TALIBAN": "AF", "GUINEA-BISSAU": "GW", "TUNISIA": "TN", "TURKIYE": "TR", "MOLDOVA": "MD", "CHINESE": "CN", "CMIC": "CN",
        # EU programme codes and UN list names
        "EU:YEM": "YE", "EU:SYR": "SY", "EU:LBY": "LY", "EU:IRQ": "IQ", "EU:AFG": "AF", "EU:SOM": "SO", "EU:MLI": "ML", "EU:CAF": "CF", "EU:COD": "CD", "EU:SSD": "SS",
        "EU:SDN": "SD", "EU:BDI": "BI", "EU:GIN": "GN", "EU:GNB": "GW", "EU:HTI": "HT", "EU:NIC": "NI", "EU:VEN": "VE", "EU:MMR": "MM", "EU:BLR": "BY", "EU:RUS": "RU",
        "EU:UKR": "UA", "EU:PRK": "KP", "EU:IRN": "IR", "EU:TUN": "TN", "EU:ZWE": "ZW", "EU:TUR": "TR", "EU:MDA": "MD", "EU:BIH": "BA", "EU:LBN": "LB", "EU:EGY": "EG", "EU:NER": "NE",
        "UN:DPRK": "KP", "UN:SOMALIA": "SO", "UN:LIBYA": "LY", "UN:YEMEN": "YE", "UN:IRAQ": "IQ", "UN:MALI": "ML", "UN:SOUTH SUDAN": "SS", "UN:CAR": "CF", "UN:DRC": "CD",
        "UN:SUDAN": "SD", "UN:HAITI": "HT", "UN:GUINEA-BISSAU": "GW", "UN:TALIBAN": "AF"}
    REGIME_CC.update({"NS-PLC": "PS", "HAMAS": "PS", "HIZBALLAH": "LB", "LEBANON": "LB", "IRGC": "IR", "IFSR": "IR", "IRAN": "IR", "DPRK": "KP", "SOMALIA": "SO"})
    for p in parties:
        if p["cc"]: continue
        for g in p["p"]:
            gu = g.upper()
            hit = REGIME_CC.get(gu) or next((cc for k, cc in REGIME_CC.items() if ":" not in k and gu.startswith(k)), None) \
                  or next((cc for k, cc in REGIME_CC.items() if ":" in k and gu.startswith(k)), None) or country_in_text(g.split(":", 1)[-1])
            if hit:
                p["cc"] = hit; p["inf"] = "program"
                p["a"].append({"raw": f"(country of the sanctions program: {COUNTRIES.get(hit, {}).get('name', hit)})", "cc": hit, "lat": None, "lon": None, "city": None, "fb": True})
                break

    # ---- 2. well-known armed groups and networks: area of operations (curated; shown as inferred)
    for p in parties:
        if p["cc"]: continue
        names = [p["n"]] + p["alt"][:15]
        for pat, cc, label in GROUP_AREAS:
            if any(re.search(pat, n, re.I) for n in names):
                p["cc"] = cc; p["inf"] = "group"
                p["a"].append({"raw": f"(area of operations: {label})", "cc": cc, "lat": None, "lon": None, "city": None, "fb": True})
                break

    # ---- 3. country names mentioned in remarks or aliases ("operates in Yemen", "based in Lebanon")
    for p in parties:
        if p["cc"]: continue
        text = " ".join([p["rem"]] + p["alt"][:10] + [p["ti"]])
        hit = country_in_text(text)
        if hit:
            p["cc"] = hit; p["inf"] = "remarks"
            p["a"].append({"raw": f"(country mentioned in the record: {COUNTRIES.get(hit, {}).get('name', hit)})", "cc": hit, "lat": None, "lon": None, "city": None, "fb": True})

    # last resort: a party with no location at all sits next to the party it is "Linked To"
    by_name0 = {}
    for p in parties:
        if p["cc"]:
            by_name0.setdefault(name_key(p["n"]), p)
            for a in p["alt"]: by_name0.setdefault(name_key(a), p)
    link_re0 = re.compile(r"Linked To:\s*([^;)]+)", re.I)
    for _ in range(2):   # two passes so chains resolve
        for p in parties:
            if p["cc"]: continue
            for m in link_re0.finditer(p["rem"]):
                q = by_name0.get(name_key(m.group(1).rstrip(". ")))
                if q and q["cc"]:
                    p["cc"], p["lat"], p["lon"] = q["cc"], q["lat"], q["lon"]
                    p["a"].append({"raw": f"(placed with linked party: {q['n']})", "cc": q["cc"], "lat": q["lat"], "lon": q["lon"], "city": None, "fb": True})
                    by_name0.setdefault(name_key(p["n"]), p)
                    break

    # edges
    by_name = {}
    for p in parties:
        by_name.setdefault(name_key(p["n"]), p)
        for a in p["alt"]: by_name.setdefault(name_key(a), p)
    edges = []
    link_re = re.compile(r"Linked To:\s*([^;)]+)", re.I)
    for p in parties:
        if not p["cc"]: continue
        for m in link_re.finditer(p["rem"]):
            q = by_name.get(name_key(m.group(1).rstrip(". ")))
            if q and q is not p:
                edges.append({"k": "link", "a": p["id"], "b": q["id"]})
        # footprint: other distinct locations of the same party
        first = (p["cc"], p["city"] if False else None)
        seen_loc = {(p["cc"], p["lat"], p["lon"])}
        for a in p["a"]:
            key = (a["cc"], a["lat"], a["lon"])
            if not a["cc"] or key in seen_loc: continue
            seen_loc.add(key)
            edges.append({"k": "foot", "a": p["id"], "cc": a["cc"], "lat": a["lat"], "lon": a["lon"], "l": a["city"] or COUNTRIES.get(a["cc"], {}).get("name", a["cc"])})
        if p["t"] == "Individual":
            done = set()
            for n in p["nat"]:
                iso = country_iso(n)
                if not iso or iso == p["cc"] or iso in done: continue
                done.add(iso)
                edges.append({"k": "nat", "a": p["id"], "cc": iso, "l": COUNTRIES.get(iso, {}).get("name", iso)})
    return parties, edges

# ------------------------------------------------------------------ changes
def diff(parties, today):
    path = os.path.join(OUT, "state.json")
    state = {"seen": {}, "series": [], "events": []}
    if os.path.exists(path):
        with open(path) as f: state = json.load(f)
    seen = state["seen"]; now_ids = {p["id"]: p for p in parties}
    PFX = {"ofa": "US", "bis": "US", "sta": "US", "eu": "EU", "uk": "UK", "un": "UN", "au": "AU", "ca": "CA"}
    auth_of_id = lambda pid: PFX.get(pid.split(":")[0], "US")
    prev_auths = set(state.get("auths") or {auth_of_id(pid) for pid in seen})
    now_auths = {a for p in parties for a in p["au"]}
    onboarding = now_auths - prev_auths if prev_auths else set()
    # first date each authority appeared; additions on that date for that authority were onboarding, not designations
    first_day = {}
    for pid, rec in seen.items():
        a = auth_of_id(pid); first_day[a] = min(first_day.get(a, "9999"), rec.get("first", "9999"))
    state["events"] = [e for e in state.get("events", []) if not (e["op"] == "+" and first_day.get(auth_of_id(e["id"])) == e["d"] and e["d"] != state.get("first_day_of_site", ""))]
    added, removed = [], []
    for pid, p in now_ids.items():
        if pid not in seen:
            seen[pid] = {"first": today, "last": today, "n": p["n"], "s": p["s"], "p": p["p"][:3], "cc": p["cc"], "t": p["t"]}
            added.append(pid)
        else:
            seen[pid]["last"] = today
    for pid, rec in list(seen.items()):
        if pid not in now_ids and rec.get("last") == state.get("last_run"):
            removed.append(pid)
    first_run = not state.get("last_run")
    if not first_run:
        for pid in added:
            if onboarding and set(now_ids[pid]["au"]) <= onboarding: continue   # first load of a new list, not a new designation
            r = seen[pid]; state["events"].append({"d": today, "op": "+", "id": pid, "n": r["n"], "s": r["s"], "p": r["p"], "cc": r["cc"], "t": r["t"]})
        for pid in removed:
            r = seen[pid]; state["events"].append({"d": today, "op": "-", "id": pid, "n": r["n"], "s": r["s"], "p": r["p"], "cc": r["cc"], "t": r["t"]})
    cutoff = (dt.date.fromisoformat(today) - dt.timedelta(days=KEEP_DAYS)).isoformat()
    state["events"] = [e for e in state["events"] if e["d"] >= cutoff]
    if not state["series"] or state["series"][-1]["d"] != today:
        state["series"].append({"d": today, "n": len(parties)})
    else:
        state["series"][-1]["n"] = len(parties)
    state["series"] = state["series"][-400:]
    state["last_run"] = today
    state["auths"] = sorted(now_auths | prev_auths)
    state.setdefault("first_day_of_site", today)
    with open(path, "w") as f: json.dump(state, f, separators=(",", ":"))
    return {"events": sorted(state["events"], key=lambda e: e["d"], reverse=True), "series": state["series"],
            "first_run": first_run, "added_today": len(added), "removed_today": len(removed)}

# --------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", help="local consolidated.csv instead of downloading")
    ap.add_argument("--date", help="override build date (YYYY-MM-DD)")
    ap.add_argument("--sample-dir", help="folder with local copies of eu.csv, uk.csv, un.xml, au.xlsx, ca.xml (offline testing)")
    ap.add_argument("--us-only", action="store_true", help="skip the non-US lists")
    ap.add_argument("--only", help="comma-separated subset of EU,UK,UN,AU,CA to load")
    ap.add_argument("--site-url", default=os.environ.get("SITE_URL", ""), help="public base URL, used for canonical links and sitemap.xml")
    args = ap.parse_args()
    today = args.date or dt.date.today().isoformat()
    os.makedirs(OUT, exist_ok=True)

    rows, status = load_rows(args)
    parties, edges = build(rows)
    auth_counts = defaultdict(int)
    for p in parties:
        for a in p["au"]: auth_counts[a] += 1
    multi = sum(1 for p in parties if len(p["au"]) > 1)
    placed = sum(1 for p in parties if p["cc"])
    with_city = sum(1 for p in parties if p["lat"] is not None)
    changes = diff(parties, today)

    programs = defaultdict(int); sources = defaultdict(int); types = defaultdict(int); countries = defaultdict(int)
    for p in parties:
        sources[p["s"]] += 1; types[p["t"]] += 1
        for g in p["p"]: programs[g] += 1
        if p["cc"]: countries[p["cc"]] += 1

    # compact party records for the site
    src_list = sorted({p["src"] for p in parties})
    src_idx = {v: i for i, v in enumerate(src_list)}
    compact = []
    for p in parties:
        compact.append({k: v for k, v in {
            "id": p["id"], "n": p["n"], "t": p["t"], "s": p["s"], "si": src_idx[p["src"]], "p": p["p"], "au": p["au"],
            "recs": [{"au": r["au"], "url": r["url"], "p": r["p"], "listed": r["listed"]} for r in p["recs"]] if len(p["recs"]) > 1 else None,
            "cc": p["cc"], "lat": p["lat"], "lon": p["lon"], "inf": p.get("inf"), "city": next((a["city"] for a in p["a"] if a["lat"] is not None), None),
            "a": [a["raw"] for a in p["a"]], "ti": p["ti"], "alt": p["alt"], "dob": p["dob"], "nat": p["nat"],
            "pob": p["pob"], "rem": p["rem"], "ids": p["ids"], "url": p["url"], "ves": p["ves"], "listed": p["listed"],
            "ly": (lambda ys: min(ys) if ys else None)([int(y) for r in p["recs"] for y in re.findall(r"(?<!\d)(19\d\d|20\d\d)(?!\d)", r.get("listed") or "")]),
        }.items() if v not in ("", None, [])})

    iso_numeric = {iso: c["isonumeric"] for iso, c in COUNTRIES.items()}
    iso_name = {iso: c["name"] for iso, c in COUNTRIES.items()}
    iso_numeric["XK"] = -99; iso_name["XK"] = "Kosovo"

    meta = {
        "built": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"), "date": today,
        "source": CSL_URL, "parties": len(parties), "placed": placed, "with_city": with_city,
        "edges": {k: sum(1 for e in edges if e["k"] == k) for k in ("link", "foot", "nat")},
        "programs": sorted(programs.items(), key=lambda x: -x[1]), "sources": dict(sources), "types": dict(types),
        "countries": dict(countries), "iso_numeric": iso_numeric, "iso_name": iso_name, "src_list": src_list,
        "added_today": changes["added_today"], "removed_today": changes["removed_today"],
        "authorities": {a: {"n": auth_counts.get(a, 0), **status.get(a, {"ok": False, "n": 0, "error": "not loaded"})} for a in AUTH_ORDER},
        "multi_listed": multi, "dated": sum(1 for p in compact if p.get("ly")),
    }
    # Hosts cap single files (Cloudflare Pages: 25 MB), so the party list is written in parts plus a manifest.
    for old in os.listdir(OUT):
        if old.startswith("parties-") and old.endswith(".json"): os.remove(os.path.join(OUT, old))
    CHUNK = 8000
    parts = []
    for i in range(0, len(compact), CHUNK):
        name = f"parties-{i // CHUNK + 1}.json"; parts.append(name)
        with open(os.path.join(OUT, name), "w", encoding="utf-8") as f:
            json.dump(compact[i:i + CHUNK], f, separators=(",", ":"), ensure_ascii=False)
    with open(os.path.join(OUT, "parties.json"), "w", encoding="utf-8") as f:
        json.dump({"parts": parts, "count": len(compact), "edges": edges}, f, separators=(",", ":"), ensure_ascii=False)
    with open(os.path.join(OUT, "changes.json"), "w") as f:
        json.dump({"events": changes["events"], "series": changes["series"]}, f, separators=(",", ":"), ensure_ascii=False)
    with open(os.path.join(OUT, "meta.json"), "w") as f:
        json.dump(meta, f, separators=(",", ":"), ensure_ascii=False)
    print(f"{len(parties)} parties, {placed} placed, {with_city} to a city, "
          f"{meta['edges']} edges, +{changes['added_today']} -{changes['removed_today']}")
    for a in AUTH_ORDER:
        st = meta["authorities"][a]
        print(f"  {a}: {'ok' if st['ok'] else 'FAILED'} {st['n']} parties {st['error']}")
    print(f"  {multi} parties listed by more than one authority")
    import pages
    n_prog, n_cc, n_party = pages.build_pages(args.site_url)
    print(f"static pages: {n_prog} programs, {n_cc} countries, {n_party} parties" + (", sitemap written" if args.site_url else ", no --site-url so no sitemap"))
    import api
    n_api, n_sh = api.build_api(args.site_url)
    print(f"api: {n_api} parties in {n_sh} shards under site/api/v1/")

if __name__ == "__main__":
    main()
