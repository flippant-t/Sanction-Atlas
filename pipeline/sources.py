"""
Non-US sanctions lists, normalised to the same row shape build.py uses for the
US Consolidated Screening List:

    {authority, source, entity_number, type, programs, name, addresses, remarks,
     alt_names, nationalities, dates_of_birth, places_of_birth, source_information_url, ids}

Every fetcher is wrapped so a broken download or a changed format skips that
authority and reports it in meta.json instead of failing the whole build.
Add --sample-dir to build.py to test with local copies of the files.
"""
import csv, io, re, os, zipfile
import xml.etree.ElementTree as ET

UA = {"User-Agent": "Mozilla/5.0 (compatible; sanctionscope-build; +https://github.com/)"}
SOURCES = {
    "EU": ("EU Consolidated Financial Sanctions List",
           "https://webgate.ec.europa.eu/fsd/fsf/public/files/csvFullSanctionsList_1_1/content?token=dG9rZW4tMjAxNw", "eu.csv"),
    "UK": ("UK OFSI Consolidated List", "https://ofsisanctionslist.blob.core.windows.net/publicdata/ConList.csv", "uk.csv"),
    "UN": ("UN Security Council Consolidated List", "https://scsanctions.un.org/resources/xml/en/consolidated.xml", "un.xml"),
    "AU": ("Australia DFAT Consolidated List", "https://www.dfat.gov.au/sites/default/files/regulation8_consolidated.xlsx", "au.xlsx"),
    "CA": ("Canada SEMA / Autonomous Sanctions", "https://www.international.gc.ca/world-monde/assets/office_docs/international_relations-relations_internationales/sanctions/sema-lmes.xml", "ca.xml"),
}
RECORD_URL = {
    "EU": "https://www.sanctionsmap.eu/#/main", "UK": "https://www.gov.uk/government/publications/the-uk-sanctions-list",
    "UN": "https://main.un.org/securitycouncil/en/content/un-sc-consolidated-list", "AU": "https://www.dfat.gov.au/international-relations/security/sanctions/consolidated-list",
    "CA": "https://www.international.gc.ca/world-monde/international_relations-relations_internationales/sanctions/consolidated-consolide.aspx",
}

def fetch(auth, sample_dir=None):
    name, url, fname = SOURCES[auth]
    if sample_dir:
        p = os.path.join(sample_dir, fname)
        if not os.path.exists(p): raise FileNotFoundError(p)
        with open(p, "rb") as f: return f.read()
    import requests
    r = requests.get(url, timeout=240, headers=UA)
    r.raise_for_status()
    return r.content

def clean(s):
    return re.sub(r"\s+", " ", (s or "")).strip()

def joinaddr(*parts):
    return ", ".join(clean(x) for x in parts if clean(x))

def row(auth, eid, name, typ, programs, addresses, remarks="", alt=(), nat=(), dob="", pob="", ids="", url="", listed=""):
    return {"authority": auth, "source": SOURCES[auth][0], "entity_number": f"{auth}-{eid}", "type": typ,
            "programs": "; ".join(sorted({f"{auth}:{p}" for p in programs if p})),
            "name": clean(name), "addresses": "; ".join(dict.fromkeys(a for a in addresses if a)),
            "remarks": clean(remarks), "alt_names": "; ".join(dict.fromkeys(clean(a) for a in alt if clean(a) and clean(a).lower() != clean(name).lower())),
            "nationalities": "; ".join(dict.fromkeys(clean(n) for n in nat if clean(n))), "dates_of_birth": clean(dob), "places_of_birth": clean(pob),
            "ids": clean(ids), "source_information_url": url or RECORD_URL[auth], "vessel_flag": "", "start_date": clean(listed)}

# ------------------------------------------------------------------ EU
def parse_eu(raw):
    text = raw.decode("utf-8-sig", errors="replace")
    rd = csv.DictReader(io.StringIO(text), delimiter=";")
    groups = {}
    for r in rd:
        g = r.get("Entity_LogicalId") or r.get("Entity_EU_ReferenceNumber")
        if not g: continue
        e = groups.setdefault(g, {"names": [], "addr": [], "nat": [], "dob": [], "pob": [], "prog": set(), "type": "", "remark": "", "ref": "", "ids": [], "url": "", "date": ""})
        st = (r.get("Entity_SubjectType") or "").lower()
        if st: e["type"] = "Individual" if st.startswith("p") else "Entity"
        e["prog"].add(clean(r.get("Entity_Regulation_Programme")))
        e["remark"] = e["remark"] or clean(r.get("Entity_Remark"))
        e["ref"] = e["ref"] or clean(r.get("Entity_EU_ReferenceNumber"))
        e["url"] = e["url"] or clean(r.get("Entity_Regulation_PublicationUrl"))
        e["date"] = e["date"] or clean(r.get("Entity_DesignationDate")) or clean(r.get("Entity_Regulation_EntryIntoForceDate"))
        wn = clean(r.get("NameAlias_WholeName")) or joinaddr(r.get("NameAlias_FirstName"), r.get("NameAlias_MiddleName"), r.get("NameAlias_LastName")).replace(",", "")
        if wn and wn not in e["names"]: e["names"].append(wn)
        a = joinaddr(r.get("Address_Street"), r.get("Address_City"), r.get("Address_Region"), r.get("Address_CountryDescription") or r.get("Address_CountryIso2Code"))
        if a and a not in e["addr"]: e["addr"].append(a)
        n = clean(r.get("Citizenship_CountryDescription") or r.get("Citizenship_CountryIso2Code"))
        if n and n not in e["nat"]: e["nat"].append(n)
        d = clean(r.get("BirthDate_BirthDate")) or clean(r.get("BirthDate_Year"))
        if d and d not in e["dob"]: e["dob"].append(d)
        pb = joinaddr(r.get("BirthDate_City"), r.get("BirthDate_CountryDescription"))
        if pb and pb not in e["pob"]: e["pob"].append(pb)
        idn = clean(r.get("Identification_Number"))
        if idn and idn not in e["ids"]: e["ids"].append(f"{clean(r.get('Identification_TypeDescription')) or 'ID'} {idn}")
    out = []
    for g, e in groups.items():
        if not e["names"]: continue
        out.append(row("EU", g, e["names"][0], e["type"] or "Entity", e["prog"], e["addr"], e["remark"], e["names"][1:], e["nat"],
                       "; ".join(e["dob"]), "; ".join(e["pob"]), "; ".join(e["ids"]), e["url"], e["date"]))
    return out

# ------------------------------------------------------------------ UK
def parse_uk(raw):
    text = raw.decode("utf-8-sig", errors="replace")
    lines = text.splitlines()
    start = next((i for i, l in enumerate(lines[:5]) if "Group ID" in l or "Regime" in l), 1)
    rd = csv.DictReader(io.StringIO("\n".join(lines[start:])))
    groups = {}
    for r in rd:
        g = clean(r.get("Group ID"))
        if not g: continue
        e = groups.setdefault(g, {"primary": "", "alias": [], "addr": [], "nat": [], "dob": [], "pob": [], "prog": set(), "type": "", "info": "", "pos": "", "ids": [], "date": ""})
        nm = joinaddr(r.get("Name 6"), r.get("Name 1"), r.get("Name 2"), r.get("Name 3"), r.get("Name 4"), r.get("Name 5")).replace(",", "")
        if (r.get("Alias Type") or "").lower().startswith("primary") and not e["primary"]: e["primary"] = nm
        elif nm: e["alias"].append(nm)
        gt = (r.get("Group Type") or "").lower()
        e["type"] = "Individual" if gt.startswith("ind") else "Vessel" if gt.startswith("ship") else "Entity"
        e["prog"].add(clean(r.get("Regime")))
        e["info"] = e["info"] or clean(r.get("Other Information"))
        e["pos"] = e["pos"] or clean(r.get("Position"))
        e["date"] = e["date"] or clean(r.get("Listed On"))
        a = joinaddr(r.get("Address 1"), r.get("Address 2"), r.get("Address 3"), r.get("Address 4"), r.get("Address 5"), r.get("Address 6"), r.get("Post/Zip Code"), r.get("Country"))
        if a and a not in e["addr"]: e["addr"].append(a)
        for n in re.split(r"[,;/]|\(\d\)", r.get("Nationality") or ""):
            n = clean(n)
            if n and n not in e["nat"]: e["nat"].append(n)
        d = clean(r.get("DOB"))
        if d and d not in e["dob"]: e["dob"].append(d)
        pb = joinaddr(r.get("Town of Birth"), r.get("Country of Birth"))
        if pb and pb not in e["pob"]: e["pob"].append(pb)
        for k, lab in (("Passport Number", "Passport"), ("National Identification Number", "National ID")):
            v = clean(r.get(k))
            if v and f"{lab} {v}" not in e["ids"]: e["ids"].append(f"{lab} {v}")
    out = []
    for g, e in groups.items():
        name = e["primary"] or (e["alias"][0] if e["alias"] else "")
        if not name: continue
        alias = [a for a in e["alias"] if a != name]
        rem = "; ".join(x for x in [e["pos"], e["info"]] if x)
        out.append(row("UK", g, name, e["type"], e["prog"], e["addr"], rem, alias, e["nat"], "; ".join(e["dob"]), "; ".join(e["pob"]), "; ".join(e["ids"]), "", e["date"]))
    return out

# ------------------------------------------------------------------ UN
def parse_un(raw):
    root = ET.fromstring(raw)
    def tx(el, tag):
        x = el.find(tag)
        return clean(x.text) if x is not None and x.text else ""
    out = []
    for ind in root.iter("INDIVIDUAL"):
        name = " ".join(x for x in [tx(ind, "FIRST_NAME"), tx(ind, "SECOND_NAME"), tx(ind, "THIRD_NAME"), tx(ind, "FOURTH_NAME")] if x)
        if not name: continue
        alias = [tx(a, "ALIAS_NAME") for a in ind.findall("INDIVIDUAL_ALIAS")]
        addr = [joinaddr(tx(a, "STREET"), tx(a, "CITY"), tx(a, "STATE_PROVINCE"), tx(a, "COUNTRY")) for a in ind.findall("INDIVIDUAL_ADDRESS")]
        nat = [clean(v.text) for n in ind.findall("NATIONALITY") for v in n.findall("VALUE") if v.text]
        dob = [tx(d, "DATE") or tx(d, "YEAR") for d in ind.findall("INDIVIDUAL_DATE_OF_BIRTH")]
        pob = [joinaddr(tx(p, "CITY"), tx(p, "STATE_PROVINCE"), tx(p, "COUNTRY")) for p in ind.findall("INDIVIDUAL_PLACE_OF_BIRTH")]
        ids = [joinaddr(tx(d, "TYPE_OF_DOCUMENT"), tx(d, "NUMBER"), tx(d, "ISSUING_COUNTRY")) for d in ind.findall("INDIVIDUAL_DOCUMENT")]
        rem = "; ".join(x for x in [tx(ind, "DESIGNATION/VALUE"), tx(ind, "COMMENTS1")] if x)
        out.append(row("UN", tx(ind, "DATAID"), name, "Individual", [tx(ind, "UN_LIST_TYPE")], addr, rem, alias, nat, "; ".join(dob), "; ".join(pob), "; ".join(ids),
                       "https://main.un.org/securitycouncil/en/content/un-sc-consolidated-list", tx(ind, "LISTED_ON")))
    for ent in root.iter("ENTITY"):
        name = tx(ent, "FIRST_NAME")
        if not name: continue
        alias = [tx(a, "ALIAS_NAME") for a in ent.findall("ENTITY_ALIAS")]
        addr = [joinaddr(tx(a, "STREET"), tx(a, "CITY"), tx(a, "STATE_PROVINCE"), tx(a, "COUNTRY")) for a in ent.findall("ENTITY_ADDRESS")]
        rem = tx(ent, "COMMENTS1")
        out.append(row("UN", tx(ent, "DATAID"), name, "Entity", [tx(ent, "UN_LIST_TYPE")], addr, rem, alias, [], "", "", "",
                       "https://main.un.org/securitycouncil/en/content/un-sc-consolidated-list", tx(ent, "LISTED_ON")))
    return out

# ------------------------------------------------------------------ AU
def parse_au(raw):
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]
    rows = ws.iter_rows(values_only=True)
    header = None
    groups = {}
    for r in rows:
        vals = ["" if v is None else str(v) for v in r]
        if header is None:
            if any("Name of Individual" in v for v in vals): header = [clean(v) for v in vals]
            continue
        d = dict(zip(header, vals))
        ref = clean(d.get("Reference"))
        if not ref: continue
        e = groups.setdefault(ref, {"primary": "", "alias": [], "addr": [], "nat": [], "dob": [], "pob": [], "prog": set(), "type": "", "info": "", "date": ""})
        nm = clean(d.get("Name of Individual or Entity"))
        if (d.get("Name Type") or "").lower().startswith("orig") and not e["primary"]: e["primary"] = nm
        elif nm: e["alias"].append(nm)
        t = (d.get("Type") or "").lower(); e["type"] = "Individual" if t.startswith("ind") else "Entity"
        for c in re.split(r"[;/]", d.get("Committees") or d.get("Listing Information") or ""):
            c = clean(c)
            if c: e["prog"].add(c[:40])
        a = clean(d.get("Address"))
        if a and a not in e["addr"]: e["addr"].append(a)
        for n in re.split(r"[,;/]", d.get("Citizenship") or ""):
            n = clean(n)
            if n and n not in e["nat"]: e["nat"].append(n)
        dob = clean(d.get("Date of Birth"))
        if dob and dob not in e["dob"]: e["dob"].append(dob)
        pb = clean(d.get("Place of Birth"))
        if pb and pb not in e["pob"]: e["pob"].append(pb)
        e["info"] = e["info"] or clean(d.get("Additional Information"))
        e["date"] = e["date"] or clean(d.get("Control Date"))
    out = []
    for ref, e in groups.items():
        name = e["primary"] or (e["alias"][0] if e["alias"] else "")
        if not name: continue
        out.append(row("AU", ref, name, e["type"], e["prog"] or {"Autonomous"}, e["addr"], e["info"], [a for a in e["alias"] if a != name], e["nat"], "; ".join(e["dob"]), "; ".join(e["pob"]), "", "", e["date"]))
    return out

# ------------------------------------------------------------------ CA
def parse_ca(raw):
    root = ET.fromstring(raw)
    out = []
    def tx(el, tag):
        x = el.find(tag)
        return clean(x.text) if x is not None and x.text else ""
    i = 0
    for rec in root.iter("record"):
        i += 1
        ent = tx(rec, "Entity"); gn = tx(rec, "GivenName"); ln = tx(rec, "LastName")
        country = tx(rec, "Country")
        if ent:
            name, typ = ent, "Entity"
        elif gn or ln:
            name, typ = f"{gn} {ln}".strip(), "Individual"
        else:
            continue
        alias = [clean(a) for a in re.split(r";|\n", tx(rec, "Aliases")) if clean(a)]
        prog = country or "Autonomous"
        rem = "; ".join(x for x in [tx(rec, "Title"), tx(rec, "Schedule") and f"Schedule {tx(rec, 'Schedule')}", tx(rec, "Item") and f"Item {tx(rec, 'Item')}"] if x)
        # Canada gives no address; the regime country is the best available placement, marked as such
        out.append(row("CA", tx(rec, "Item") or i, name, typ, [prog], [], rem, alias, [country] if typ == "Individual" and country else [], tx(rec, "DateOfBirth"), "", "", "", tx(rec, "DateOfListing")))
        if typ == "Entity" and country:
            out[-1]["addresses"] = country
    return out

PARSERS = {"EU": parse_eu, "UK": parse_uk, "UN": parse_un, "AU": parse_au, "CA": parse_ca}

def load_all(sample_dir=None, only=None):
    """Returns (rows, status) where status[auth] = {'ok': bool, 'n': int, 'error': str}."""
    rows, status = [], {}
    for auth in SOURCES:
        if only and auth not in only: continue
        try:
            raw = fetch(auth, sample_dir)
            parsed = PARSERS[auth](raw)
            if len(parsed) < 50: raise ValueError(f"only {len(parsed)} records parsed; format probably changed")
            rows.extend(parsed); status[auth] = {"ok": True, "n": len(parsed), "error": ""}
        except Exception as ex:  # a broken source must not sink the build
            status[auth] = {"ok": False, "n": 0, "error": f"{type(ex).__name__}: {str(ex)[:200]}"}
    return rows, status
