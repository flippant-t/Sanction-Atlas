#!/usr/bin/env python3
"""
Physical world texture for the globe, drawn once at build time from Natural Earth 50m vectors.

The map used to be a flat cream landmass on blue. This paints a plausible physical earth instead:
land shaded green to tan by latitude (a crude but readable biome model), deserts and mountain ranges
picked out from Natural Earth's geography regions, glaciers and ice in white, lakes and major rivers
in water blue, and a soft coastal halo. It is a single image, so the globe can never half-load.

Run: python3 pipeline/make_land.py [out.png]
"""
import json, math, os, sys, urllib.request
from PIL import Image, ImageDraw, ImageFilter

W = int(os.environ.get("LAND_PX", "8192"))
MAXLAT = 85.0511287798
NE = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/"
CACHE = os.environ.get("NE_CACHE", "/tmp/ne")
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "site", "vendor", "land-mercator.png")

OCEAN = (168, 202, 231)
SHALLOW = (190, 219, 240)
COAST_HALO = (206, 228, 244)
ICE = (247, 249, 251)
LAKE = (176, 208, 234)
RIVER = (170, 202, 230)
BORDER = (196, 188, 172)

def fetch(name):
    os.makedirs(CACHE, exist_ok=True)
    p = os.path.join(CACHE, name + ".json")
    if not os.path.exists(p):
        urllib.request.urlretrieve(NE + name + ".geojson", p)
    with open(p, encoding="utf-8") as f:
        return json.load(f)

def merc(lon, lat):
    lat = max(-MAXLAT, min(MAXLAT, lat))
    return ((lon + 180) / 360 * W,
            (1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * W)

def rings_of(geom):
    t = geom.get("type")
    if t == "Polygon": return [geom["coordinates"]]
    if t == "MultiPolygon": return geom["coordinates"]
    return []

def lines_of(geom):
    t = geom.get("type")
    if t == "LineString": return [geom["coordinates"]]
    if t == "MultiLineString": return geom["coordinates"]
    return []

def _clip_half(ring, keep_east):
    """Clip an unwrapped ring (lons 0..360) to one side of the antimeridian."""
    out = []
    inside = (lambda p: p[0] >= 180) if keep_east else (lambda p: p[0] <= 180)
    n = len(ring)
    for i in range(n):
        a, b = ring[i], ring[(i + 1) % n]
        ia, ib = inside(a), inside(b)
        if ia: out.append(a)
        if ia != ib and b[0] != a[0]:
            t = (180 - a[0]) / (b[0] - a[0])
            out.append([180, a[1] + t * (b[1] - a[1])])
    return out

def fix(ring):
    """Rings that cross the antimeridian are cut in two rather than unwrapped: unwrapping leaves a
    shape spanning most of the world, which paints stripes across the ocean."""
    # A ring crosses the antimeridian only if two consecutive points jump more than 180 degrees.
    # Testing the overall span instead treats Afro-Eurasia (-17.5 to 180) as a wrap and shreds it.
    if not ring or not any(abs(ring[i][0] - ring[i - 1][0]) > 180 for i in range(1, len(ring))):
        return [ring]
    sh = [[lon + 360 if lon < 0 else lon, lat] for lon, lat in ring]
    west = _clip_half(sh, False)
    east = [[lon - 360, lat] for lon, lat in _clip_half(sh, True)]
    return [r for r in (west, east) if len(r) > 3]

def shifted(pts):
    """Yield the shape and its copy one world to the left, so shapes at the date line are not stretched.
    Rings that span almost the whole width are degenerate wrap-arounds and would paint a stripe across
    the ocean, so they are dropped."""
    for sh in (0, -W):
        p = [(x + sh, y) for x, y in pts]
        if max(x for x, _ in p) >= 0 and min(x for x, _ in p) <= W:
            yield p

def biome(lat):
    """A crude latitude model: tropics green, subtropical desert belts tan, temperate green, boreal
    grey-green, polar pale. Enough to read as a physical map without pretending to be real land cover."""
    a = abs(lat)
    stops = [(0, (122, 148, 96)), (12, (132, 155, 98)), (20, (176, 166, 118)), (28, (196, 182, 132)),
             (35, (170, 168, 118)), (45, (140, 156, 108)), (58, (134, 148, 116)), (68, (166, 172, 164)),
             (78, (214, 218, 218)), (90, (236, 240, 242))]
    for i in range(len(stops) - 1):
        a0, c0 = stops[i]; a1, c1 = stops[i + 1]
        if a0 <= a <= a1:
            t = 0 if a1 == a0 else (a - a0) / (a1 - a0)
            return tuple(round(c0[k] + (c1[k] - c0[k]) * t) for k in range(3))
    return stops[-1][1]

def main():
    land = fetch("ne_50m_land")
    lakes = fetch("ne_50m_lakes")
    ice = fetch("ne_50m_glaciated_areas")
    rivers = fetch("ne_50m_rivers_lake_centerlines")
    regions = fetch("ne_50m_geography_regions_polys")
    borders = fetch("ne_50m_admin_0_boundary_lines_land")

    base = Image.new("RGB", (W, W), OCEAN)
    d = ImageDraw.Draw(base)

    # a soft halo just off the coast, drawn under the land, reads as shallow water
    halo = Image.new("L", (W, W), 0)
    hd = ImageDraw.Draw(halo)
    for f in land["features"]:
        for poly in rings_of(f["geometry"]):
            for _r in fix(poly[0]):
              for p in shifted([merc(*q) for q in _r]):
                  if len(p) > 2: hd.polygon(p, fill=255)
    halo_sharp = halo.point(lambda v: 255 if v > 127 else 0)
    halo = halo.filter(ImageFilter.GaussianBlur(W // 900))
    base.paste(Image.new("RGB", (W, W), SHALLOW), (0, 0), halo.point(lambda v: min(255, v * 2)))
    base.paste(Image.new("RGB", (W, W), COAST_HALO), (0, 0), halo.point(lambda v: 255 if v > 200 else 0))

    # land, banded by latitude
    STEP = 2.0
    lat = -86.0
    bands = []
    while lat < 86.0:
        bands.append((lat, lat + STEP, biome(lat + STEP / 2)))
        lat += STEP
    # The land mask is derived from the same shapes as the coastal halo, which renders correctly;
    # building it separately kept producing full-width bands where a ring crossed the antimeridian.
    land_mask = halo_sharp
    hole = ImageDraw.Draw(land_mask)
    for f in land["features"]:
        for poly in rings_of(f["geometry"]):
            for ring in poly[1:]:
                for _r in fix(ring):
                    for p in shifted([merc(*q) for q in _r]):
                        if len(p) > 2: hole.polygon(p, fill=0)

    bandimg = Image.new("RGB", (W, W), (140, 156, 108))
    bd = ImageDraw.Draw(bandimg)
    for lo, hi, col in bands:
        y0 = merc(0, hi)[1]; y1 = merc(0, lo)[1]
        bd.rectangle([0, y0, W, y1], fill=col)
    bandimg = bandimg.filter(ImageFilter.GaussianBlur(W // 700))
    base.paste(bandimg, (0, 0), land_mask)

    # deserts and mountains from Natural Earth's named regions, clipped to land
    tint = Image.new("RGB", (W, W), (0, 0, 0))
    tmask = Image.new("L", (W, W), 0)
    td = ImageDraw.Draw(tint); tm = ImageDraw.Draw(tmask)
    for f in regions["features"]:
        cls = (f["properties"].get("FEATURECLA") or "")
        if cls == "Desert": col, alpha = (206, 190, 142), 210
        elif cls in ("Range/mtn", "Plateau"): col, alpha = (150, 138, 112), 120
        else: continue
        for poly in rings_of(f["geometry"]):
            for _r in fix(poly[0]):
              for p in shifted([merc(*q) for q in _r]):
                  if len(p) > 2: td.polygon(p, fill=col); tm.polygon(p, fill=alpha)
    tmask = tmask.filter(ImageFilter.GaussianBlur(W // 500))
    tmask = Image.composite(tmask, Image.new("L", (W, W), 0), land_mask)
    base.paste(tint, (0, 0), tmask)

    # glaciers and ice
    for f in ice["features"]:
        for poly in rings_of(f["geometry"]):
            for _r in fix(poly[0]):
              for p in shifted([merc(*q) for q in _r]):
                  if len(p) > 2: d.polygon(p, fill=ICE)
    # lakes and rivers
    for f in lakes["features"]:
        for poly in rings_of(f["geometry"]):
            for _r in fix(poly[0]):
              for p in shifted([merc(*q) for q in _r]):
                  if len(p) > 2: d.polygon(p, fill=LAKE)
    rw = max(1, W // 3000)
    for f in rivers["features"]:
        for line in lines_of(f["geometry"]):
            for _r in fix(line):
                for p in shifted([merc(*q) for q in _r]):
                    if len(p) > 1: d.line(p, fill=RIVER, width=rw)
    # country borders, faint
    bw = max(1, W // 4000)
    for f in borders["features"]:
        for line in lines_of(f["geometry"]):
            for _r in fix(line):
                for p in shifted([merc(*q) for q in _r]):
                    if len(p) > 1: d.line(p, fill=BORDER, width=bw)

    base = base.filter(ImageFilter.SMOOTH)
    base.convert("RGB").save(OUT, optimize=True, quality=90)
    print("wrote", OUT, os.path.getsize(OUT) // 1024, "KB")
    chk = Image.open(OUT).convert("RGB")
    for name, (lon, lat) in {"mid-Atlantic": (-30, 20), "Sahara": (10, 22), "Amazon": (-62, -4),
                             "Siberia": (90, 62), "Greenland": (-42, 72)}.items():
        print(" ", name, chk.getpixel(tuple(int(v) for v in merc(lon, lat))))

if __name__ == "__main__":
    main()
