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
import asyncio, json, os, sys, time, urllib.request, datetime as dt

ROSTER_URL = os.environ.get("ROSTER_URL", "https://raw.githubusercontent.com/flippant-t/Sanction-Scope/main/site/data/vessels.json")
LISTEN = int(os.environ.get("LISTEN_SECONDS", "90"))
KEY = os.environ.get("AISSTREAM_KEY", "")
CF = {"token": os.environ.get("CF_API_TOKEN", ""), "acct": os.environ.get("CF_ACCOUNT_ID", ""), "ns": os.environ.get("CF_KV_NAMESPACE_ID", "")}
TRAIL_HOURS = 24

def kv_url(k): return f"https://api.cloudflare.com/client/v4/accounts/{CF['acct']}/storage/kv/namespaces/{CF['ns']}/values/{k}"
def kv_get(k, default):
    try:
        r = urllib.request.urlopen(urllib.request.Request(kv_url(k), headers={"Authorization": "Bearer " + CF["token"]}), timeout=30)
        return json.loads(r.read().decode())
    except Exception: return default
def kv_put(k, obj):
    data = json.dumps(obj, separators=(",", ":")).encode()
    req = urllib.request.Request(kv_url(k), data=data, method="PUT", headers={"Authorization": "Bearer " + CF["token"], "Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=60).read()

def load_roster():
    if os.path.exists("site/data/vessels.json"):
        with open("site/data/vessels.json", encoding="utf-8") as f: return json.load(f)
    return json.loads(urllib.request.urlopen(ROSTER_URL, timeout=60).read().decode())

async def listen(watch_mmsi, imo_wanted, imo2mmsi, positions):
    import websockets
    sub = {"APIKey": KEY, "BoundingBoxes": [[[-90, -180], [90, 180]]], "FilterMessageTypes": ["PositionReport", "ShipStaticData"]}
    seen_static = seen_pos = 0
    t_end = time.time() + LISTEN
    async with websockets.connect("wss://stream.aisstream.io/v0/stream", max_size=None) as ws:
        await ws.send(json.dumps(sub))
        while time.time() < t_end:
            try: raw = await asyncio.wait_for(ws.recv(), timeout=max(1, t_end - time.time()))
            except asyncio.TimeoutError: break
            try: m = json.loads(raw)
            except Exception: continue
            meta = m.get("MetaData", {}); mmsi = str(meta.get("MMSI", "")); mt = m.get("MessageType")
            if mt == "ShipStaticData":
                sd = m["Message"]["ShipStaticData"]; imo = str(sd.get("ImoNumber") or "")
                seen_static += 1
                if imo in imo_wanted and imo2mmsi.get(imo) != mmsi:
                    imo2mmsi[imo] = mmsi; watch_mmsi[mmsi] = imo_wanted[imo]
                if mmsi in watch_mmsi:
                    p = positions.setdefault(mmsi, {}); p["name"] = (sd.get("Name") or "").strip(); p["dest"] = (sd.get("Destination") or "").strip(); p["type"] = sd.get("Type")
            elif mt == "PositionReport" and mmsi in watch_mmsi:
                pr = m["Message"]["PositionReport"]; seen_pos += 1
                p = positions.setdefault(mmsi, {})
                ts = meta.get("time_utc", "")[:19]
                p.update({"lat": round(pr.get("Latitude", 0), 5), "lon": round(pr.get("Longitude", 0), 5), "sog": pr.get("Sog"), "cog": pr.get("Cog"), "hdg": pr.get("TrueHeading"), "nav": pr.get("NavigationalStatus"), "ts": ts, "id": watch_mmsi[mmsi]})
                tr = p.setdefault("trail", []); 
                if not tr or tr[-1][2] != ts: tr.append([p["lat"], p["lon"], ts])
    return seen_static, seen_pos

def main():
    if not KEY: sys.exit("AISSTREAM_KEY missing")
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
    kv_put("ais:imo2mmsi", imo2mmsi); kv_put("ais:positions", positions)
    kv_put("ais:meta", {"updated": dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"), "watch": len(watch_mmsi), "mapped": len(imo2mmsi), "positions": len(positions), "roster": len(roster)})
    print(f"roster {len(roster)}, imo->mmsi known {len(imo2mmsi)}, watching {len(watch_mmsi)} mmsi, positions {len(positions)}; this run: {seen_static} static msgs, {seen_pos} position reports")

if __name__ == "__main__":
    main()
