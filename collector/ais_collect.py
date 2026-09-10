#!/usr/bin/env python3
"""
AIS collector for sanctioned vessels. Runs every 15 minutes in GitHub Actions.

  1. Loads the sanctioned-vessel roster (site/data/vessels.json) and the previous state from Cloudflare KV.
  2. Listens to the aisstream.io WebSocket for ~90 seconds.
     - ShipStaticData messages give IMO -> MMSI, which grows a map over time (OFAC/UK publish IMO, AIS is keyed by MMSI).
     - PositionReport messages for MMSIs on the watch list update last-known positions.
  3. Writes positions (with a 24 h trail) and the IMO/MMSI map back to KV. The site reads them through /api/v1/vessels.

Env: AISSTREAM_KEY, CF_API_TOKEN (KV write), CF_ACCOUNT_ID, CF_KV_NAMESPACE_ID. Optional: LISTEN_SECONDS (default 90).
aisstream.io is free for non-commercial use; see their terms before selling this layer.
"""
import asyncio, json, os, re, sys, time, urllib.request, urllib.error, datetime as dt

ROSTER_URL = os.environ.get("ROSTER_URL", "https://raw.githubusercontent.com/flippant-t/Sanction-Scope/main/site/data/vessels.json")
LISTEN = int(os.environ.get("LISTEN_SECONDS", "90"))
KEY = os.environ.get("AISSTREAM_KEY", "")
CF = {"token": os.environ.get("CF_API_TOKEN", ""), "acct": os.environ.get("CF_ACCOUNT_ID", ""), "ns": os.environ.get("CF_KV_NAMESPACE_ID", "")}
TRAIL_HOURS = 24

def kv_url(k): return f"https://api.cloudflare.com/client/v4/accounts/{CF['acct']}/storage/kv/namespaces/{CF['ns']}/values/{k}"
def kv_get(k, default):
    if not all(CF.values()): return default
    try:
        r = urllib.request.urlopen(urllib.request.Request(kv_url(k), headers={"Authorization": "Bearer " + CF["token"]}), timeout=30)
        return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 404: return default
        raise SystemExit(f"FAILED reading Cloudflare KV ({e.code}): token, account ID or namespace ID is wrong.")
    except Exception: return default
def kv_put(k, obj):
    data = json.dumps(obj, separators=(",", ":")).encode()
    req = urllib.request.Request(kv_url(k), data=data, method="PUT", headers={"Authorization": "Bearer " + CF["token"], "Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=60).read()

def load_roster():
    if os.path.exists("site/data/vessels.json"):
        with open("site/data/vessels.json", encoding="utf-8") as f: return json.load(f)
    return json.loads(urllib.request.urlopen(ROSTER_URL, timeout=60).read().decode())

MMSI_RE = re.compile(r'"MMSI":\s*(\d+)')

async def stream(sub, handler, seconds):
    """Read from aisstream for `seconds`, reconnecting if the server drops us (it drops slow readers)."""
    import websockets
    t_end = time.time() + seconds; n = 0
    while time.time() < t_end:
        try:
            async with websockets.connect("wss://stream.aisstream.io/v0/stream", max_size=None, open_timeout=30, ping_interval=None, compression=None) as ws:
                await ws.send(json.dumps(sub))
                first = True
                while time.time() < t_end:
                    try: raw = await asyncio.wait_for(ws.recv(), timeout=max(1, t_end - time.time()))
                    except asyncio.TimeoutError: break
                    if first:
                        first = False
                        if raw.startswith('{"error"'): sys.exit("FAILED: aisstream.io rejected the subscription: " + raw[:200] + " (check AISSTREAM_KEY)")
                        print("connected to aisstream.io")
                    n += 1; handler(raw)
        except SystemExit: raise
        except Exception as e:
            left = t_end - time.time()
            if left > 5:
                print(f"connection dropped ({type(e).__name__}); reconnecting, {int(left)}s left"); await asyncio.sleep(2)
            else: break
    return n

async def listen(watch_mmsi, imo_wanted, imo2mmsi, positions):
    seen = {"static": 0, "pos": 0}
    def handle(raw):
        # cheap prefilter: only parse JSON for static data (to learn IMO->MMSI) or for MMSIs we watch
        m = MMSI_RE.search(raw); mmsi = m.group(1) if m else ""
        is_static = '"ShipStaticData"' in raw
        if not is_static and mmsi not in watch_mmsi: return
        try: msg = json.loads(raw)
        except Exception: return
        meta = msg.get("MetaData", {}); mt = msg.get("MessageType")
        if mt == "ShipStaticData":
            sd = msg["Message"]["ShipStaticData"]; imo = str(sd.get("ImoNumber") or "")
            seen["static"] += 1
            if imo in imo_wanted and imo2mmsi.get(imo) != mmsi:
                imo2mmsi[imo] = mmsi; watch_mmsi[mmsi] = imo_wanted[imo]
            if mmsi in watch_mmsi:
                p = positions.setdefault(mmsi, {}); p["name"] = (sd.get("Name") or "").strip(); p["dest"] = (sd.get("Destination") or "").strip(); p["type"] = sd.get("Type"); p["id"] = watch_mmsi[mmsi]
        elif mt == "PositionReport" and mmsi in watch_mmsi:
            pr = msg["Message"]["PositionReport"]; seen["pos"] += 1
            p = positions.setdefault(mmsi, {}); ts = meta.get("time_utc", "")[:19]
            p.update({"lat": round(pr.get("Latitude", 0), 5), "lon": round(pr.get("Longitude", 0), 5), "sog": pr.get("Sog"), "cog": pr.get("Cog"), "hdg": pr.get("TrueHeading"), "nav": pr.get("NavigationalStatus"), "ts": ts, "id": watch_mmsi[mmsi]})
            tr = p.setdefault("trail", [])
            if not tr or tr[-1][2] != ts: tr.append([p["lat"], p["lon"], ts])
    # phase 1: worldwide static data only (a small fraction of traffic) to learn IMO -> MMSI
    n1 = await stream({"APIKey": KEY, "BoundingBoxes": [[[-90, -180], [90, 180]]], "FilterMessageTypes": ["ShipStaticData"]}, handle, max(20, LISTEN // 3))
    # phase 2: only the vessels we watch, positions and static data
    n2 = 0
    if watch_mmsi:
        mm = list(watch_mmsi)[:5000]
        n2 = await stream({"APIKey": KEY, "BoundingBoxes": [[[-90, -180], [90, 180]]], "FiltersShipMMSI": mm, "FilterMessageTypes": ["PositionReport", "ShipStaticData"]}, handle, LISTEN - LISTEN // 3)
    print(f"messages read: {n1} static-phase, {n2} watch-phase")
    return seen["static"], seen["pos"]

def main():
    missing = [k for k, v in {"AISSTREAM_KEY": KEY, "CF_API_TOKEN": CF["token"], "CF_ACCOUNT_ID": CF["acct"], "CF_KV_NAMESPACE_ID": CF["ns"]}.items() if not v]
    if not KEY: sys.exit("FAILED: the AISSTREAM_KEY secret is not set in GitHub (Settings > Secrets and variables > Actions).")
    if missing: print("WARNING: Cloudflare secrets missing (" + ", ".join(missing) + "); positions will be listened for but not saved.")
    roster = load_roster()["vessels"]
    imo_wanted = {v["imo"]: v["id"] for v in roster if v.get("imo")}
    imo2mmsi = kv_get("ais:imo2mmsi", {})
    positions = kv_get("ais:positions", {})
    watch_mmsi = {v["mmsi"]: v["id"] for v in roster if v.get("mmsi")}
    for imo, mmsi in imo2mmsi.items():
        if imo in imo_wanted: watch_mmsi[mmsi] = imo_wanted[imo]
    seen_static, seen_pos = asyncio.run(listen(watch_mmsi, imo_wanted, imo2mmsi, positions))
    # prune trails and drop positions older than 30 days
    cutoff = (dt.datetime.utcnow() - dt.timedelta(hours=TRAIL_HOURS)).strftime("%Y-%m-%dT%H:%M:%S")
    old = (dt.datetime.utcnow() - dt.timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S")
    for mmsi in list(positions):
        p = positions[mmsi]
        if "trail" in p: p["trail"] = [t for t in p["trail"] if t[2] >= cutoff][-200:]
        if p.get("ts") and p["ts"] < old: del positions[mmsi]
    with open("ais_positions.json", "w") as f: json.dump({"positions": positions, "imo2mmsi": imo2mmsi}, f)
    if not missing:
        try:
            kv_put("ais:imo2mmsi", imo2mmsi); kv_put("ais:positions", positions)
            kv_put("ais:meta", {"updated": dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"), "watch": len(watch_mmsi), "mapped": len(imo2mmsi), "positions": len(positions), "roster": len(roster)})
            print("saved to Cloudflare KV")
        except Exception as ex:
            sys.exit(f"FAILED writing to Cloudflare KV: {ex}. Check CF_API_TOKEN has 'Workers KV Storage: Edit', and that CF_ACCOUNT_ID / CF_KV_NAMESPACE_ID are right.")
    print(f"roster {len(roster)}, imo->mmsi known {len(imo2mmsi)}, watching {len(watch_mmsi)} mmsi, positions {len(positions)}; this run: {seen_static} static msgs, {seen_pos} position reports")

if __name__ == "__main__":
    main()
