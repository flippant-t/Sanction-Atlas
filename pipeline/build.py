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
# When one entity sits on several US lists, the merged party takes its id from the highest-ranked list.
# Without a fixed rank the id came from whichever row the download listed first, so ids flipped between builds.
SOURCE_RANK = {"OFAC SDN": 0, "OFAC other": 1, "BIS Entity List": 2, "BIS other": 3, "State / other": 4}
def source_rank(key): return SOURCE_RANK.get(key, 5)
def source_key(s, auth="US"):
    if auth != "US": return auth
    t = (s or "").lower()
    if "specially designated" in t: return "OFAC SDN"
    if "non-sdn" in t or "sectoral" in t or "treasury" in t: return "OFAC other"
    if "entity list" in t: return "BIS Entity List"
    if "bureau of industry" in t or "denied" in t or "unverified" in t or "military end" in t: return "BIS other"
    return "State / other"

def party_type(t, row=None):
    s = (t or "").lower()
    if s.startswith("ind"): return "Individual"
    if s.startswith("ves"): return "Vessel"
    if s.startswith("air"): return "Aircraft"
    if s.startswith(("ent", "org", "com")): return "Entity"
    # Not every feed populates `type`, and defaulting to Entity types UK people as companies.
    # A date or place of birth only ever appears on a person, so use that before giving up.
    if row and ((row.get("dates_of_birth") or "").strip() or (row.get("places_of_birth") or "").strip()):
        return "Individual"
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
        import sources
        raw = sources.get_with_retries(CSL_URL, {"User-Agent": "sanctionscope-build"}, timeout=180)
        rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
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
# Cyrillic and Greek transliteration for cross-list matching. The EU and UK publish some names only in
# native script ("Сбербанк") while the US publishes the Latin form ("SBERBANK"); without this they never
# meet. Ambiguous letters produce several variants and every variant becomes a match key.
CYR = {"А":"A","Б":"B","В":"V","Г":"G","Д":"D","Е":"E","Ж":"ZH","З":"Z","И":"I","К":"K","Л":"L","М":"M",
       "Н":"N","О":"O","П":"P","Р":"R","С":"S","Т":"T","У":"U","Ф":"F","Ц":"TS","Ч":"CH","Ш":"SH",
       "Щ":"SHCH","Ъ":"","Ь":"","Ы":"Y","Э":"E","Є":"YE","І":"I","Ї":"YI","Ґ":"G","Ў":"U"}
CYR_ALT = {"Й":["Y","I",""], "Х":["KH","H"], "Ё":["E","YO"], "Ю":["YU","IU"], "Я":["YA","IA"]}
GREEK = {"Α":"A","Β":"V","Γ":"G","Δ":"D","Ε":"E","Ζ":"Z","Η":"I","Θ":"TH","Ι":"I","Κ":"K","Λ":"L","Μ":"M",
         "Ν":"N","Ξ":"X","Ο":"O","Π":"P","Ρ":"R","Σ":"S","Σ":"S","Τ":"T","Υ":"Y","Φ":"F","Χ":"CH","Ψ":"PS","Ω":"O"}

def translit(name, cap=4):
    """Latin spellings of a native-script name. Returns [] when the name is already Latin."""
    up = (name or "").upper()
    if not any(c in CYR or c in CYR_ALT or c in GREEK for c in up): return []
    out = [""]
    for ch in up:
        if ch in CYR_ALT:
            opts = CYR_ALT[ch]
            out = [o + v for o in out for v in opts][:cap * 4]
        else:
            out = [o + (CYR.get(ch) or GREEK.get(ch) or ch) for o in out]
    seen, res = set(), []
    for o in out:
        if o not in seen: seen.add(o); res.append(o)
    return res[:cap]

COUNTRY_WORDS = {"RUSSIA", "RUSSIAN", "FEDERATION", "IRAN", "IRANIAN", "KOREA", "KOREAN", "CHINA", "CHINESE", "BELARUS", "UKRAINE", "SYRIA", "SYRIAN", "CUBA", "VENEZUELA", "LIBYA", "IRAQ", "IRAQI", "YEMEN", "SUDAN", "MYANMAR", "BURMA", "AFGHANISTAN", "PAKISTAN", "TURKEY", "TURKISH", "INDIA", "JAPAN", "AMERICA", "AMERICAN"}

def match_key(name, typ):
    k = re.sub(r"[^A-Z0-9 ]", " ", norm(name).upper())
    toks = [t for t in k.split() if t]
    if typ != "Individual":
        toks = [t for t in toks if t not in LEGAL]
        if not toks or sum(len(t) for t in toks) < 6: return None
        # "BANK RUSSIA" or "IRAN TRADING" identifies nothing, but "BANK MELLI" or "SME BANK" does:
        # require one token the generic list does not cover, unless the name is long enough to stand alone.
        if len(toks) < 4 and not any(t not in GENERIC for t in toks): return None
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
        names = [p["n"]] + p["alt"][:20]
        keys = {match_key(p["n"], p["t"])}
        for t in translit(p["n"]): keys.add(match_key(t, p["t"]))
        # A country word at the end of a company name is often dropped by other authorities
        # ("Sberbank of Russia" vs "Сбербанк"). Index the stripped form too. Entity keys keep word
        # order, so this still cannot equate "Bank Melli Iran" with "Melli Bank".
        if p["t"] != "Individual":
            for base in [p["n"]] + translit(p["n"]):
                k = match_key(base, p["t"])
                if not k: continue
                body = [t for t in k[2:].split() if t not in COUNTRY_WORDS]
                if body and body != k[2:].split() and any(t not in GENERIC for t in body):
                    keys.add("E:" + " ".join(body))
        # Aliases are useful (an EU record in Cyrillic often carries the Latin spelling as an alias) but
        # dangerous for companies: OFAC lists a parent's name as an aka of its subsidiary, so "MB Bank"
        # carries "Bank Melli Iran". For entities an alias therefore only counts when it shares a
        # distinctive word with the party's own name, which keeps spelling variants and drops relatives.
        own = set()
        if p["t"] != "Individual":
            ok = match_key(p["n"], p["t"]) or ""
            own = set(ok[2:].split()) - {""}
        for a in p["alt"][:20]:
            for cand in [a] + translit(a):
                k = match_key(cand, p["t"])
                if not k: continue
                body = k[2:].split()
                if not (len(body) >= 2 or (len(body) == 1 and len(body[0]) >= 7 and body[0] not in GENERIC)):
                    continue
                if p["t"] != "Individual":
                    # the alias must carry the same distinctive words as the party's own name, not merely
                    # overlap with them: "Melli Bank plc" and "Bank Melli Iran" are different companies.
                    if own and own != set(body): continue
                keys.add(k)
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
        members = sorted((parties[i] for i in idxs), key=lambda p: (AUTH_ORDER.index(p["au"][0]), source_rank(p["s"]), p["id"]))
        base = members[0]
        if len(members) == 1:
            merged.append(base); continue
        for m in members[1:]:
            if m["au"][0] not in base["au"]: base["au"].append(m["au"][0])
            base["recs"] += m["recs"]; base["rids"] += m["rids"]
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

def row_order(r):
    auth = r.get("authority") or "US"; src = r.get("source") or ""
    return (AUTH_ORDER.index(auth) if auth in AUTH_ORDER else 99, source_rank(source_key(src, auth)),
            str(r.get("entity_number") or r.get("_id") or ""), r.get("name") or "", r.get("addresses") or "",
            json.dumps(r, sort_keys=True, default=str))

def build(rows):
    # Download order is not stable between runs. Sorting first makes ids, merges and every
    # first-match choice below depend only on the content of the lists.
    rows = sorted(rows, key=row_order)
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
            "id": pid, "n": name, "t": party_type(r.get("type"), r), "s": source_key(src, auth), "src": src, "au": [auth],
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
        # the underlying list records; change tracking works on these, so merges and splits between
        # authorities never look like additions or removals
        p["rids"] = [{"id": p["id"], "s": p["s"], "p": p["p"][:3]}]

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
STATE_DIR = os.path.join(ROOT, "state")
STATE_PATH = os.path.join(STATE_DIR, "state.json")      # outside site/, so it is never deployed
OLD_STATE_PATH = os.path.join(OUT, "state.json")
STATE_VERSION = 2
PFX_AUTH = {"ofa": "US", "bis": "US", "sta": "US", "eu": "EU", "uk": "UK", "un": "UN", "au": "AU", "ca": "CA"}
def auth_of_id(pid): return PFX_AUTH.get(pid.split(":")[0], "US")
MASS_REMOVAL = (50, 0.10)     # more removals than max(50, 10% of an authority's records) in one run is a broken feed, not delistings
RELIST_DAYS = 7               # a record back after this long is a new listing; sooner, the removal was a glitch

def save_state(state):
    os.makedirs(STATE_DIR, exist_ok=True)
    # one record per line with sorted keys, so the nightly commit is a small diff instead of a new 18 MB blob
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        f.write("{\n")
        head = {k: v for k, v in state.items() if k != "seen"}
        for k in sorted(head): f.write(json.dumps(k) + ":" + json.dumps(head[k], separators=(",", ":"), ensure_ascii=False) + ",\n")
        f.write('"seen":{\n')
        items = sorted(state["seen"].items())
        for i, (k, v) in enumerate(items):
            f.write(json.dumps(k) + ":" + json.dumps(v, separators=(",", ":"), sort_keys=True, ensure_ascii=False) + (",\n" if i < len(items) - 1 else "\n"))
        f.write("}}\n")

def diff(parties, today, status):
    """Track additions and removals per underlying list record (not per merged party), so
    - a source that fails to download never produces removals,
    - a merge or split between authorities never looks like a change,
    - re-running the build on the same day never repeats events,
    - a record that comes back retracts its removal."""
    state = None
    for path in (STATE_PATH, OLD_STATE_PATH):
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f: state = json.load(f)
            break
    warnings = []
    baseline = state is None or state.get("version") != STATE_VERSION
    if baseline:
        # First run of this tracker. Earlier history was built on unstable ids and is not trustworthy,
        # so start clean: record everything as seen today and emit no events.
        old = state or {}
        state = {"version": STATE_VERSION, "seen": {}, "events": [], "series": old.get("series", []),
                 "first_day_of_site": old.get("first_day_of_site", today), "tracking_since": today}
        warnings.append("change history reset: record-level tracking starts " + today)
    seen = state["seen"]
    ok = {a for a, st in status.items() if st.get("ok")}
    now = {}
    for p in parties:
        for r in p.get("rids") or [{"id": p["id"], "s": p["s"], "p": p["p"][:3]}]:
            now[r["id"]] = (p, r)
    events = state["events"]
    def ev(op, rid, rec):
        e = {"d": today, "op": op, "id": rec["pid"], "n": rec["n"], "s": rec["s"], "p": rec["p"], "cc": rec["cc"], "t": rec["t"], "au": auth_of_id(rid), "r": rid}
        if not any(x["d"] == today and x["op"] == op and x.get("r") == rid for x in events): events.append(e)

    prev_auths = {auth_of_id(rid) for rid in seen}
    added, removed, relisted = [], [], []
    for rid, (p, r) in now.items():
        rec = {"pid": p["id"], "n": p["n"], "s": r["s"], "p": r["p"], "cc": p["cc"], "t": p["t"], "last": today}
        old = seen.get(rid)
        if old is None:
            rec["first"] = today; seen[rid] = rec
            if not baseline: added.append(rid)
            continue
        gone = old.get("gone")
        rec["first"] = old.get("first", today); seen[rid] = rec
        if gone:
            back_after = (dt.date.fromisoformat(today) - dt.date.fromisoformat(gone)).days
            # withdraw the removal: it was a glitch, or it is superseded by the re-listing below
            state["events"] = events = [e for e in events if not (e["op"] == "-" and e.get("r") == rid and e["d"] >= gone)]
            if back_after > RELIST_DAYS: relisted.append(rid)

    # removals: only for authorities that loaded this run, once per record
    candidates = defaultdict(list)
    for rid, rec in seen.items():
        if rid in now or rec.get("gone"): continue
        a = auth_of_id(rid)
        if a not in ok: continue                  # its list did not load: absence means nothing
        candidates[a].append(rid)
    # A list loading for the first time (or for the first time since tracking began) is onboarding, and a
    # burst of new ids from one authority means its id scheme changed. Neither is a designation.
    by_auth_add = Counter(auth_of_id(rid) for rid in added)
    quiet = set()
    for a, n in by_auth_add.items():
        active = sum(1 for rid, rec in seen.items() if auth_of_id(rid) == a and not rec.get("gone")) - n   # before this run
        if a not in prev_auths:
            quiet.add(a); warnings.append(f"{a}: first load, {n} records recorded without events")
        elif n > max(MASS_REMOVAL[0], MASS_REMOVAL[1] * active):
            quiet.add(a); warnings.append(f"{a}: {n} new record ids in one run; treated as an id change, no events recorded")
    added = [rid for rid in added if auth_of_id(rid) not in quiet]
    for a in quiet:
        for rid in candidates.pop(a, []): seen[rid]["gone"] = today     # the old ids of a re-keyed list, retired silently
    for a, rids in candidates.items():
        active = sum(1 for rid, rec in seen.items() if auth_of_id(rid) == a and not rec.get("gone"))
        if len(rids) > max(MASS_REMOVAL[0], MASS_REMOVAL[1] * active):
            warnings.append(f"{a}: {len(rids)} of {active} records missing in one run; treated as a broken feed, no removals recorded")
            continue
        removed += rids

    for rid in added + relisted: ev("+", rid, seen[rid])
    for rid in removed:
        seen[rid]["gone"] = today; ev("-", rid, seen[rid])

    # forget records that have been gone longer than the event window
    cutoff = (dt.date.fromisoformat(today) - dt.timedelta(days=KEEP_DAYS)).isoformat()
    for rid in [rid for rid, rec in seen.items() if rec.get("gone") and rec["gone"] < cutoff]: del seen[rid]
    state["events"] = [e for e in events if e["d"] >= cutoff]
    if not state["series"] or state["series"][-1]["d"] != today: state["series"].append({"d": today, "n": len(parties)})
    else: state["series"][-1]["n"] = len(parties)
    state["series"] = state["series"][-400:]
    state["last_run"] = today
    save_state(state)
    if os.path.exists(OLD_STATE_PATH): os.remove(OLD_STATE_PATH)
    # the site does not need the record id or the per-record details
    out = [{k: v for k, v in e.items() if k != "r"} for e in sorted(state["events"], key=lambda e: e["d"], reverse=True)]
    return {"events": out, "series": state["series"], "first_run": baseline,
            "added_today": len(added) + len(relisted), "removed_today": len(removed), "warnings": warnings,
            "tracking_since": state.get("tracking_since")}

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
    changes = diff(parties, today, status)

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

    # vessel roster for the AIS collector: every vessel with an IMO (7 digits) or MMSI (9 digits) anywhere in its record
    # OFAC writes these as "IMO 9187629; MMSI, 572469210", so the separator after MMSI is a comma, not a
    # colon or a space. The old pattern required whitespace or a colon and therefore matched nothing, which
    # left every vessel without an MMSI - and AIS position reports carry MMSI, not IMO, so nothing could be
    # tracked until the collector happened to learn the mapping off the global feed. Accept any short run of
    # punctuation, and keep only MMSIs whose first three digits are a real maritime country code.
    imo_re = re.compile(r"\bIMO\s*(?:No\.?|Number)?[:\s]*(\d{7})\b", re.I)
    mmsi_re = re.compile(r"\bMMSI\b[^0-9A-Za-z]{0,12}(\d{9})(?!\d)", re.I)
    roster = []
    for p in parties:
        if p["t"] != "Vessel": continue
        blob = " ".join([p["ids"], p["rem"], p["ves"]] + p["alt"])
        imos = sorted(set(imo_re.findall(blob))); mmsis = sorted({m for m in mmsi_re.findall(blob) if 200 <= int(m[:3]) <= 799})
        if not imos and not mmsis: continue
        roster.append({"id": p["id"], "n": p["n"], "imo": imos[0] if imos else None, "mmsi": mmsis[0] if mmsis else None, "au": p["au"], "flag": p.get("flag") or "", "cc": p["cc"], "s": p["s"]})
    with open(os.path.join(OUT, "vessels.json"), "w", encoding="utf-8") as f:
        json.dump({"date": today, "count": len(roster), "vessels": roster}, f, separators=(",", ":"), ensure_ascii=False)
    meta_vessels = len(roster)
    meta = {
        "built": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"), "date": today,
        "source": CSL_URL, "parties": len(parties), "placed": placed, "with_city": with_city,
        "edges": {k: sum(1 for e in edges if e["k"] == k) for k in ("link", "foot", "nat")},
        "programs": sorted(programs.items(), key=lambda x: -x[1]), "sources": dict(sources), "types": dict(types),
        "countries": dict(countries), "iso_numeric": iso_numeric, "iso_name": iso_name, "src_list": src_list,
        "added_today": changes["added_today"], "removed_today": changes["removed_today"],
        "authorities": {a: {"n": auth_counts.get(a, 0), **status.get(a, {"ok": False, "n": 0, "error": "not loaded"})} for a in AUTH_ORDER},
        "change_warnings": changes["warnings"], "tracking_since": changes["tracking_since"],
        "multi_listed": multi, "dated": sum(1 for p in compact if p.get("ly")), "vessels_with_imo": meta_vessels,
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
    # compact map parts: only what the map needs to draw, filter and search; full records come from the API shards on click
    prog_idx = {g: i for i, (g, _) in enumerate(sorted(programs.items(), key=lambda x: -x[1]))}
    for old in os.listdir(OUT):
        if old.startswith("map-") and old.endswith(".json"): os.remove(os.path.join(OUT, old))
    mparts = []
    for i in range(0, len(compact), 10000):
        name = f"map-{i // 10000 + 1}.json"; mparts.append(name)
        chunk = []
        for p in compact[i:i + 10000]:
            m = {"id": p["id"], "n": p["n"], "t": p["t"][0], "s": p["s"], "au": "".join(a[0] + a[1] for a in p["au"]) if False else p["au"], "cc": p.get("cc"),
                 "p": [prog_idx[g] for g in p.get("p", []) if g in prog_idx]}
            if p.get("lat") is not None: m["lat"] = round(p["lat"], 4); m["lon"] = round(p["lon"], 4)
            if p.get("city"): m["city"] = p["city"]
            if p.get("inf"): m["inf"] = 1
            if p.get("ly"): m["ly"] = p["ly"]
            if p.get("alt"): m["alt"] = [a for a in p["alt"][:5] if len(a) < 60]
            chunk.append(m)
        with open(os.path.join(OUT, name), "w", encoding="utf-8") as f:
            json.dump(chunk, f, separators=(",", ":"), ensure_ascii=False)
    with open(os.path.join(OUT, "map.json"), "w", encoding="utf-8") as f:
        json.dump({"parts": mparts, "count": len(compact), "programs": [g for g, _ in sorted(programs.items(), key=lambda x: -x[1])], "edges": edges}, f, separators=(",", ":"), ensure_ascii=False)
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
    for w in changes["warnings"]: print("  WARNING " + w)
    import pages
    n_prog, n_cc, n_party = pages.build_pages(args.site_url)
    print(f"static pages: {n_prog} programs, {n_cc} countries, {n_party} parties" + (", sitemap written" if args.site_url else ", no --site-url so no sitemap"))
    import api
    res = api.build_api(args.site_url)
    n_api, n_sh = res[0], res[1]
    n_search = res[2] if len(res) > 2 else 0
    print(f"api: {n_api} parties in {n_sh} record shards and {n_search} search shards under site/api/v1/")
    # Pro watchlist monitoring. Runs here because a Cloudflare Function cannot match thousands
    # of names against every party inside 10 ms of CPU. Never allowed to break the build.
    try:
        import monitor
        link_map = defaultdict(list)
        for e in edges:
            if e["k"] == "link":
                link_map[e["a"]].append(e["b"]); link_map[e["b"]].append(e["a"])
        monitor.run(compact, {"date": today, "events": changes["events"]}, args.site_url, dict(link_map))
    except Exception as ex:
        print(f"monitoring: skipped ({type(ex).__name__}: {str(ex)[:150]})")

    with_mmsi = sum(1 for v in roster if v.get("mmsi"))
    print(f"vessels: {meta_vessels} on the AIS roster, {with_mmsi} with an MMSI from the record (tracked at once), {meta_vessels - with_mmsi} IMO only")

if __name__ == "__main__":
    main()
