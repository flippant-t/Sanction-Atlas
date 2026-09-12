"""Day-by-day simulation of the nightly monitor against a fake KV. No network."""
import os, sys, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "pipeline"))
import monitor

KV = {}
monitor.CF = {"token": "t", "acct": "a", "ns": "n"}
monitor.kv_list = lambda pfx: sorted(k for k in KV if k.startswith(pfx))
monitor.kv_get = lambda k, d=None: json.loads(json.dumps(KV[k])) if k in KV else d
monitor.kv_put = lambda k, v: KV.__setitem__(k, json.loads(json.dumps(v)))
SENT = []
monitor.send_email = lambda to, subj, text: SENT.append((to, subj, text)) or True

def party(pid, name, cc="RU", au=("US",), links=(), alt=()):
    return {"id": pid, "n": name, "t": "Entity", "cc": cc, "au": list(au),
            "p": ["RUSSIA-EO14024"], "links": list(links), "alt": list(alt)}

ACME = party("ofa:1", "ACME TRADING FZE", "AE")
SBER = party("ofa:2", "PUBLIC JOINT STOCK COMPANY SBERBANK OF RUSSIA")
NOISE = [party(f"ofa:{i}", f"UNRELATED HOLDING {i}") for i in range(100, 160)]
MAHAN = party("ofa:3", "MAHAN AIR", "IR")

def changes(day, events): return {"date": day, "events": events}
def ev(d, op, pid, au="US"): return {"d": d, "op": op, "id": pid, "au": au}

def run(parties, ch):
    SENT.clear()
    return monitor.run(parties, ch, "https://sanctionscope.com")

def alerts(apikey="ss_k1"):
    return (KV.get(f"alerts:{apikey}") or {}).get("alerts", [])

def types(day=None):
    return sorted(a["type"] for a in alerts() if day is None or a["date"] == day)

def check(label, got, want):
    ok = got == want
    print(("PASS " if ok else "FAIL ") + label, got if ok else f"got {got}, want {want}")
    assert ok

KV["wl:ss_k1:wl_1"] = {"id": "wl_1", "label": "Q3 onboarding", "threshold": 0.85,
                       "names": ["Sberbank of Russia", "Acme Trading FZE", "Quiet Company Ltd"],
                       "email": "ops@customer.com", "active": True}

# day 1: baseline. Two of three names match, but no per-name alerts go out.
r = run([SBER, ACME] + NOISE, changes("2026-09-12", []))
check("1 baseline emits one summary", types(), ["baseline"])
check("1 counted both current matches", (alerts()[0]["watched"], alerts()[0]["matching"]), (3, 2))
check("1 one email sent", len(SENT), 1)
check("1 state remembers matches", sorted(k for k, v in KV["wlstate:ss_k1:wl_1"]["matches"].items() if v),
      ["Acme Trading FZE", "Sberbank of Russia"])

# day 2: nothing changed. Silence.
r = run([SBER, ACME] + NOISE, changes("2026-09-13", []))
check("2 quiet night, no alerts", r["alerts"], 0)
check("2 no email on a quiet night", len(SENT), 0)

# day 3: the third name gets listed
QUIET = party("eu:9", "QUIET COMPANY LTD", "CY", au=("EU",))
r = run([SBER, ACME, QUIET] + NOISE, changes("2026-09-14", [ev("2026-09-14", "+", "eu:9", "EU")]))
check("3 new listing alerts once", types("2026-09-14"), ["added"])
a = [x for x in alerts() if x["date"] == "2026-09-14"][0]
check("3 alert names the watched entry", a["watched_name"], "Quiet Company Ltd")
check("3 alert carries the party", a["party"]["id"], "eu:9")
check("3 email sent", len(SENT), 1)

# day 4: same data again. Must not repeat.
r = run([SBER, ACME, QUIET] + NOISE, changes("2026-09-15", []))
check("4 no duplicate for a standing match", r["alerts"], 0)

# day 5: Acme is delisted
r = run([SBER, QUIET] + NOISE, changes("2026-09-16", [ev("2026-09-16", "-", "ofa:1")]))
check("5 delisting alerts", types("2026-09-16"), ["removed"])

# day 6: Sberbank gains a link to a newly listed party
SBER2 = dict(SBER, links=["ofa:3"])
r = run([SBER2, QUIET, MAHAN] + NOISE, changes("2026-09-17", [ev("2026-09-17", "+", "ofa:3")]))
check("6 new link alerts", types("2026-09-17"), ["linked"])

# day 7: Sberbank picked up by the EU
SBER3 = dict(SBER2, au=["US", "EU"])
r = run([SBER3, QUIET, MAHAN] + NOISE, changes("2026-09-18", [ev("2026-09-18", "+", "ofa:2", "EU")]))
check("7 new authority alerts", types("2026-09-18"), ["changed"])

# day 8: near miss, opt-in off then on
KV["wl:ss_k1:wl_1"]["names"].append("Mahan Air Cargo")
run([SBER3, QUIET, MAHAN] + NOISE, changes("2026-09-19", []))
check("8 near misses off by default", types("2026-09-19"), [])
KV["wl:ss_k1:wl_1"]["near"] = True
KV["wlstate:ss_k1:wl_1"]["matches"].pop("Mahan Air Cargo", None)
run([SBER3, QUIET, MAHAN] + NOISE, changes("2026-09-20", []))
check("8 near misses on when asked", types("2026-09-20"), ["near"])

# a second customer is isolated
KV["wl:ss_k2:wl_9"] = {"id": "wl_9", "threshold": 0.85, "names": ["Mahan Air"], "active": True}
run([SBER3, QUIET, MAHAN] + NOISE, changes("2026-09-21", []))
check("9 second customer gets its own baseline", [a["type"] for a in alerts("ss_k2")], ["baseline"])
check("9 first customer unaffected", types("2026-09-21"), [])

# a broken list does not stop the others
KV["wl:ss_k3:wl_x"] = {"id": "wl_x", "threshold": "not a number", "names": ["X"], "active": True}
KV["wl:ss_k4:wl_y"] = {"id": "wl_y", "threshold": 0.85, "names": ["Mahan Air"], "active": True}
r = run([SBER3, QUIET, MAHAN] + NOISE, changes("2026-09-22", []))
check("10 bad list skipped, good one still processed", [a["type"] for a in alerts("ss_k4")], ["baseline"])

# feed cap
KV["alerts:ss_k4"] = {"generated": "2026-09-22", "alerts": [{"id": f"al_{i}", "type": "added", "date": "2026-09-01"} for i in range(250)]}
KV["wl:ss_k4:wl_y"]["names"] = ["Sberbank of Russia"]
KV["wlstate:ss_k4:wl_y"]["matches"] = {}
run([SBER3, QUIET, MAHAN] + NOISE, changes("2026-09-23", []))
check("11 feed capped at 200", len(alerts("ss_k4")), 200)
check("11 newest kept first", alerts("ss_k4")[0]["date"], "2026-09-23")

print("\ndigest preview:\n" + "-"*60)
print(SENT[0][2] if SENT else monitor.digest([a for a in alerts() if a["date"]=="2026-09-14"], "https://sanctionscope.com"))
