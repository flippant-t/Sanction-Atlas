"""Watchlist monitoring, run at the end of the nightly build.

A Pro customer uploads a list of counterparty names. Every night this matches those names
against the rebuilt data and reports what changed: newly listed, delisted, newly linked to
a listed party, or newly picked up by another authority.

Why it lives here and not in a Cloudflare Function: the free Workers plan gives 10 ms of
CPU and 50 subrequests per invocation, and the search path already spends most of that.
Matching thousands of names against 37,000 parties does not fit. The build already holds
every party in memory and has no such limit.

Env (all optional; without them this is a no-op and the build carries on):
    CF_API_TOKEN, CF_ACCOUNT_ID, CF_KV_NAMESPACE_ID   the KEYS namespace
    RESEND_KEY, ALERT_FROM                            email digests
    SITE_URL                                          links in alerts
    MONITOR_DRY_RUN=1                                 match and print, write nothing

KV layout (keys namespace, shared with API keys and AIS):
    wl:<apikey>:<listid>       watchlist, written by the Function when a customer saves one
    wlstate:<apikey>:<listid>  what has already been reported, written here
    alerts:<apikey>            newest-first alert feed, capped, written here
"""
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import matching

CF = {"token": os.environ.get("CF_API_TOKEN", ""), "acct": os.environ.get("CF_ACCOUNT_ID", ""),
      "ns": os.environ.get("CF_KV_NAMESPACE_ID", "")}
RESEND_KEY = os.environ.get("RESEND_KEY", "")
ALERT_FROM = os.environ.get("ALERT_FROM", "alerts@sanctionscope.com")
DRY_RUN = os.environ.get("MONITOR_DRY_RUN") == "1"

KEEP_ALERTS = 200          # per customer, newest first
MAX_NAMES = 5000           # per list, enforced again here in case KV holds an older record
MAX_LISTS = 10
NEAR_BAND = 0.10           # a near miss is this far below the customer's threshold
DISCLAIMER = "Name match only. Confirm against the official record before acting."


# ---------------------------------------------------------------- Cloudflare KV
def _kv(path, method="GET", data=None, ctype="application/json"):
    url = f"https://api.cloudflare.com/client/v4/accounts/{CF['acct']}/storage/kv/namespaces/{CF['ns']}/{path}"
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Authorization": "Bearer " + CF["token"], "Content-Type": ctype})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def kv_list(prefix):
    """Every key under a prefix. One list operation per 1000 keys; the free tier allows 1000 a day."""
    out, cursor = [], ""
    while True:
        q = urllib.parse.urlencode({"prefix": prefix, "limit": 1000, **({"cursor": cursor} if cursor else {})})
        res = json.loads(_kv("keys?" + q))
        out += [k["name"] for k in res.get("result", [])]
        cursor = (res.get("result_info") or {}).get("cursor") or ""
        if not cursor:
            return out


def kv_get(key, default=None):
    try:
        return json.loads(_kv("values/" + urllib.parse.quote(key, safe="")))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return default
        raise
    except Exception:
        return default


def kv_put(key, obj):
    if DRY_RUN:
        print(f"  [dry run] would write {key}")
        return
    _kv("values/" + urllib.parse.quote(key, safe=""), "PUT",
        json.dumps(obj, separators=(",", ":")).encode(), "text/plain")


# ---------------------------------------------------------------- alerts
def _alert_id(*parts):
    return "al_" + hashlib.sha1("|".join(parts).encode()).hexdigest()[:10]


def _party_out(p, site):
    return {k: v for k, v in {
        "id": p["id"], "name": p["n"], "type": p.get("t"), "country": p.get("cc"),
        "authorities": p.get("au") or [], "programs": (p.get("p") or [])[:6],
        "url": f"{site}/api/v1/party/" + urllib.parse.quote(p["id"], safe=""),
    }.items() if v not in (None, [], "")}


def evaluate(wl, index, by_id, removed_today, links_today, auth_today, state, today, site):
    """Compare a watchlist against today's data. Returns (alerts, new_state).

    First run for a list is the baseline: record what matches now, emit no per-name alerts,
    send one summary instead. Without that a customer's whole match set arrives as alerts on
    day one, every night, and they mute the emails.
    """
    threshold = float(wl.get("threshold") or 0.85)
    names = (wl.get("names") or [])[:MAX_NAMES]
    want_near = bool(wl.get("near"))
    seen = {k: set(v) for k, v in (state.get("matches") or {}).items()}   # name -> party ids at or above threshold
    seen_near = {k: set(v) for k, v in (state.get("near") or {}).items()}  # reported once, same as a real match
    alerts, matches, near_matches = [], {}, {}

    for name in names:
        hits = index.match(name, threshold - (NEAR_BAND if want_near else 0), limit=8)
        before, before_near = seen.get(name, set()), seen_near.get(name, set())
        now, now_near = set(), set()
        for p, s in hits:
            near = s < threshold
            (now_near if near else now).add(p["id"])
            if p["id"] in (before_near if near else before):
                continue                                   # already reported, do not send it again
            alerts.append({
                "id": _alert_id(wl["id"], name, p["id"], "near" if near else "added"), "date": today,
                "type": "near" if near else "added", "list_id": wl["id"], "list": wl.get("label"),
                "watched_name": name, "score": s, "party": _party_out(p, site),
                "note": ("Below your threshold of %s, sent because near misses are on. " % threshold + DISCLAIMER)
                        if near else DISCLAIMER,
            })
        matches[name] = sorted(now)
        if now_near:
            near_matches[name] = sorted(now_near)

        for pid in before - now:
            p = by_id.get(pid)
            if pid in removed_today:
                alerts.append({
                    "id": _alert_id(wl["id"], name, pid, "removed"), "date": today, "type": "removed",
                    "list_id": wl["id"], "list": wl.get("label"), "watched_name": name,
                    "party": _party_out(p, site) if p else {"id": pid},
                    "note": "No longer on any list we track. " + DISCLAIMER,
                })
            # a party that merely stopped matching (a name edit upstream) is not an event

        for pid in before & now:
            for kind, today_map, text in (
                ("linked", links_today, "Newly linked to another listed party."),
                ("changed", auth_today, "Newly listed by another authority."),
            ):
                extra = today_map.get(pid)
                if not extra:
                    continue
                alerts.append({
                    "id": _alert_id(wl["id"], name, pid, kind, ",".join(sorted(extra))), "date": today,
                    "type": kind, "list_id": wl["id"], "list": wl.get("label"), "watched_name": name,
                    "detail": sorted(extra), "party": _party_out(by_id[pid], site) if pid in by_id else {"id": pid},
                    "note": text + " " + DISCLAIMER,
                })

    new_state = {"matches": matches, "near": near_matches, "last_run": today, "baseline_done": True}
    if not state.get("baseline_done"):
        n = sum(1 for v in matches.values() if v)
        return ([{
            "id": _alert_id(wl["id"], "baseline", today), "date": today, "type": "baseline",
            "list_id": wl["id"], "list": wl.get("label"),
            "watched": len(names), "matching": n,
            "note": f"Monitoring started. {n} of {len(names)} names currently match a listed party. "
                    "From now on you will only hear about changes. " + DISCLAIMER,
        }], new_state)
    return alerts, new_state


# ---------------------------------------------------------------- email
def send_email(to, subject, text):
    if not RESEND_KEY or not to:
        return False
    if DRY_RUN:
        print(f"  [dry run] would email {to}: {subject}")
        return True
    body = json.dumps({"from": ALERT_FROM, "to": [to], "subject": subject, "text": text}).encode()
    req = urllib.request.Request("https://api.resend.com/emails", data=body, method="POST",
                                 headers={"Authorization": "Bearer " + RESEND_KEY,
                                          "Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=30).read()
        return True
    except Exception as ex:
        print(f"  email to {to} failed: {type(ex).__name__}")
        return False


def digest(alerts, site):
    """Plain text. One line per alert, grouped by type, no marketing."""
    order = ["added", "removed", "linked", "changed", "near", "baseline"]
    head = {"added": "Newly listed", "removed": "Removed from the lists",
            "linked": "New link to a listed party", "changed": "Now listed by another authority",
            "near": "Close matches, below your threshold", "baseline": "Monitoring started"}
    out = []
    for t in order:
        group = [a for a in alerts if a["type"] == t]
        if not group:
            continue
        out.append(head[t] + ":")
        for a in group:
            if t == "baseline":
                out.append(f"  {a['matching']} of {a['watched']} names currently match a listed party.")
                continue
            p = a.get("party") or {}
            bits = [x for x in [p.get("country"), ", ".join(p.get("authorities") or [])] if x]
            out.append(f"  {a['watched_name']} -> {p.get('name', '?')}"
                       + (f" ({'; '.join(bits)})" if bits else "")
                       + (f"  score {a['score']}" if a.get("score") else ""))
            if p.get("url"):
                out.append(f"    {p['url']}")
        out.append("")
    out += [DISCLAIMER, f"Manage your lists: {site}/api/v1/watchlist"]
    return "\n".join(out)


# ---------------------------------------------------------------- main
def run(parties, changes, site_url="", links=None):
    """Called by build.py once the day's data is written. Never raises into the build."""
    if not all(CF.values()):
        print("monitoring: Cloudflare KV not configured, skipped")
        return {"lists": 0, "alerts": 0}
    site = (site_url or "https://sanctionscope.com").rstrip("/")
    today = changes.get("date") or time.strftime("%Y-%m-%d")

    events = changes.get("events") or []
    removed_today = {e["id"] for e in events if e["d"] == today and e["op"] == "-"}
    added_today = {e["id"] for e in events if e["d"] == today and e["op"] == "+"}
    # authorities and links a matched party gained today
    auth_today = {e["id"]: {e.get("au")} for e in events
                  if e["d"] == today and e["op"] == "+" and e.get("au") and e["id"] not in removed_today}
    links_today = {}
    for p in parties:
        rel = (links or {}).get(p["id"]) or p.get("links") or []
        new_links = [q for q in rel if q in added_today]
        if new_links:
            links_today[p["id"]] = new_links[:5]

    index = matching.Index(parties)
    by_id = {p["id"]: p for p in parties}
    try:
        keys = kv_list("wl:")
    except Exception as ex:
        print(f"monitoring: could not read watchlists ({type(ex).__name__}), skipped")
        return {"lists": 0, "alerts": 0}

    per_customer, n_lists = {}, 0
    for key in keys:
        _, apikey, listid = key.split(":", 2)
        wl = kv_get(key)
        if not wl or wl.get("active") is False:
            continue
        n_lists += 1
        state = kv_get(f"wlstate:{apikey}:{listid}", {}) or {}
        try:
            alerts, new_state = evaluate(wl, index, by_id, removed_today, links_today,
                                         auth_today, state, today, site)
        except Exception as ex:
            print(f"  list {listid}: failed ({type(ex).__name__}: {ex}), left untouched")
            continue
        kv_put(f"wlstate:{apikey}:{listid}", new_state)
        if alerts:
            per_customer.setdefault(apikey, {"alerts": [], "emails": set()})
            per_customer[apikey]["alerts"] += alerts
            if wl.get("email"):
                per_customer[apikey]["emails"].add(wl["email"])

    total = 0
    for apikey, box in per_customer.items():
        feed = kv_get(f"alerts:{apikey}", {}) or {}
        old = feed.get("alerts") or []
        have = {a["id"] for a in old}
        fresh = [a for a in box["alerts"] if a["id"] not in have]
        if not fresh:
            continue
        kv_put(f"alerts:{apikey}", {"generated": today, "alerts": (fresh + old)[:KEEP_ALERTS]})
        total += len(fresh)
        n = len(fresh)
        subject = (f"SanctionScope: {n} sanctions alert{'s' if n != 1 else ''}"
                   if fresh[0]["type"] != "baseline" else "SanctionScope: monitoring started")
        for addr in box["emails"]:
            send_email(addr, subject, digest(fresh, site))

    print(f"monitoring: {n_lists} watchlists, {total} new alerts"
          + (" (dry run, nothing written)" if DRY_RUN else ""))
    return {"lists": n_lists, "alerts": total}
