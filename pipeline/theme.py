"""Shared chrome for the static pages: CSS, header and footer. Used by pages.py, api.py and screen.html."""
import html

CSS = """
:root{--bg:#f7f5f1;--bg-2:#ffffff;--bg-3:#f0ede7;--ink:#1b2331;--ink-2:#5b6472;--ink-3:#8c93a0;--line:#e0dbd2;--line-2:#cbc5b9;
      --accent:#b1700f;--accent-2:#8f5a08;--accent-fill:#e9a33c;--good:#2c7a5b;--bad:#b0392f;--blue:#2f5fb5}
*{box-sizing:border-box}html{scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--ink);font-family:"Iowan Old Style","Palatino Linotype",Palatino,"Book Antiqua",Georgia,serif;font-size:17px;line-height:1.6;-webkit-font-smoothing:antialiased}
a{color:var(--accent)}a:hover{color:var(--accent-2)}
code,pre,kbd{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:.85em}
code{background:var(--bg-3);border:1px solid var(--line);border-radius:3px;padding:1px 5px}
pre{background:var(--bg-3);border:1px solid var(--line);border-radius:4px;padding:14px 16px;overflow:auto;line-height:1.55}pre code{background:none;border:0;padding:0}
.nav{position:sticky;top:0;z-index:20;background:var(--bg-2);border-bottom:1px solid var(--line)}
.nav .in{max-width:1120px;margin:0 auto;padding:0 20px;height:56px;display:flex;align-items:center;gap:22px;font-size:15px}
.nav a{color:var(--ink-2);text-decoration:none}.nav a:hover,.nav a.on{color:var(--ink)}
.nav .brand{color:var(--ink);font-size:19px;letter-spacing:.01em;margin-right:8px}.nav .brand b{color:var(--accent);font-weight:400}
.nav .grow{flex:1}.nav .cta{background:var(--accent-fill);color:#2b1c02;padding:7px 14px;border-radius:5px;font-weight:500}.nav .cta:hover{background:#f0b862;color:#2b1c02}
main{max-width:1120px;margin:0 auto;padding:36px 20px 72px}
.narrow{max-width:820px}
/* Serif at a full page width runs well past a readable line. Prose is capped; tables, code and
   anything laid out in columns keep the full width. */
main p,main li,main dd{max-width:74ch}
main table p,main table li,.card li{max-width:none}
h1{font-size:38px;font-weight:400;letter-spacing:-.01em;line-height:1.15;margin:0 0 10px;max-width:22ch}
h2{font-size:22px;font-weight:400;margin:44px 0 12px;padding-bottom:8px;border-bottom:1px solid var(--line)}
h3{font-size:17px;font-weight:600;margin:22px 0 8px}
.lede{font-size:19px;color:var(--ink-2);margin:0 0 26px;max-width:68ch}
.sub{color:var(--ink-2)}.meta{color:var(--ink-3);font-size:13px}.small{font-size:14px}
.btn{display:inline-block;background:var(--bg-2);border:1px solid var(--line-2);color:var(--ink);padding:9px 16px;border-radius:5px;text-decoration:none;font-size:15px;cursor:pointer;font-family:inherit}
.btn:hover{border-color:var(--accent);color:var(--ink)}.btn.warm{background:var(--accent-fill);color:#2b1c02;border-color:var(--accent);font-weight:500}.btn.warm:hover{background:var(--accent-2)}
.row{display:flex;gap:12px;flex-wrap:wrap;align-items:center}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:18px;margin:18px 0}
.card{background:var(--bg-2);border:1px solid var(--line);border-radius:6px;padding:22px 22px 20px}
.card h3{margin:0 0 4px;font-size:18px;font-weight:500}.card .price{font-size:32px;margin:10px 0 2px}.card .price small{font-size:14px;color:var(--ink-2)}
.card ul{list-style:none;padding:0;margin:14px 0 18px}.card li{padding:6px 0;border-top:1px solid var(--line);font-size:15px}.card li:first-child{border-top:0}
.card.pro{border-color:var(--accent)}.card .tag{font-size:13px;color:var(--accent)}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin:22px 0}
.stat{background:var(--bg-2);border:1px solid var(--line);border-radius:6px;padding:14px 16px}.stat b{display:block;font-size:26px;font-weight:400}.stat span{color:var(--ink-2);font-size:13px}
table{border-collapse:collapse;width:100%;font-size:15px;margin:8px 0}td,th{text-align:left;padding:10px 12px 10px 0;border-top:1px solid var(--line);vertical-align:top}
th{color:var(--ink-2);font-weight:500;font-size:14px;border-top:0;border-bottom:1px solid var(--line-2)}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums;color:var(--ink-2);white-space:nowrap;padding-right:0}
ul.plain{list-style:none;padding:0;margin:0}ul.plain li{padding:7px 0;border-top:1px solid var(--line)}ul.plain li:first-child{border-top:0}
dl{display:grid;grid-template-columns:150px 1fr;gap:8px 14px;font-size:15px}dt{color:var(--ink-3)}dd{margin:0;word-break:break-word}
.tabs{display:flex;gap:4px;margin:14px 0 0}.tabs button{background:var(--bg-2);border:1px solid var(--line);border-bottom:0;color:var(--ink-2);padding:6px 12px;border-radius:4px 4px 0 0;cursor:pointer;font:inherit;font-size:14px}.tabs button.on{color:var(--ink);background:var(--bg-3)}
.tabs+pre{margin-top:0;border-top-left-radius:0}
.callout{background:var(--bg-2);border-left:3px solid var(--accent);padding:12px 16px;margin:16px 0;color:var(--ink-2)}
.cols{columns:2;column-gap:30px}
footer{border-top:1px solid var(--line);margin-top:40px}
footer .in{max-width:1120px;margin:0 auto;padding:26px 20px 34px;display:flex;flex-wrap:wrap;gap:12px 28px;align-items:center;font-size:14px;color:var(--ink-3)}
footer a{color:var(--ink-2);text-decoration:none}footer a:hover{color:var(--ink)}footer .grow{flex:1;min-width:200px}
/* Keyboard focus has to be visible: a compliance team that runs an accessibility check will look. */
a:focus-visible,button:focus-visible,input:focus-visible,summary:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:2px}
@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}*{transition:none!important;animation:none!important}}
@media(max-width:720px){.nav .in{height:auto;flex-wrap:wrap;padding:10px 16px;gap:10px 16px}.nav .brand{width:100%}h1{font-size:30px}.cols{columns:1}dl{grid-template-columns:1fr}main{padding:24px 16px 56px}}
"""

def esc(s): return html.escape(str(s or ""))

def header(rel, on=""):
    # "Methods" described the page accurately and got no clicks: someone assessing a data source
    # looks for coverage, not methodology. Vessels moved to the footer while AIS coverage is
    # partial; it was taking a top-level slot from the two pages that earn money.
    items = [("Map", ""), ("Screen a list", "screen.html"), ("API", "api/"), ("Coverage", "about.html")]
    # On a page at the site root, rel is "" and the Map link would come out as href="",
    # which reloads the current page instead of going to the map. "./" is the root there.
    links = "".join(f'<a href="{(rel + h) or "./"}"{" class=on" if k == on else ""}>{k}</a>' for k, h in items)
    return f'<div class="nav"><div class="in"><a class="brand" href="{rel or "./"}">Sanction<b>Scope</b></a>{links}<span class="grow"></span><a class="cta" href="{rel}api/subscribe">API Pro</a></div></div>'

def footer(rel, built=""):
    return f'''<footer><div class="in"><span class="grow">SanctionScope. US, EU, UK, UN, Australian and Canadian sanctions lists on one map{(", rebuilt " + esc(built)) if built else ""}. Not legal advice; verify against the official record.</span>
<a href="{rel}programs/">Programs</a><a href="{rel}countries/">Countries</a><a href="{rel}parties/">Parties</a><a href="{rel}vessels/">Vessels</a><a href="{rel}api/v1/feed.xml">RSS</a><a href="{rel}terms.html">Terms</a><a href="{rel}privacy.html">Privacy</a><a href="mailto:hello@sanctionscope.com">Contact</a></div></footer>'''

def shell(title, desc, body, rel, canonical, on="", built="", extra_head="", narrow=True):
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)} · SanctionScope</title><meta name="description" content="{esc(desc)}"><link rel="canonical" href="{esc(canonical)}">
<meta property="og:title" content="{esc(title)}"><meta property="og:description" content="{esc(desc)}"><meta property="og:image" content="{esc(rel)}og.png">
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><circle cx='16' cy='16' r='14' fill='%230e1726'/><circle cx='16' cy='16' r='5' fill='%23e9b44c'/></svg>">
<style>{CSS}</style>{extra_head}</head><body>
{header(rel, on)}
<main{' class="narrow"' if narrow else ''}>{body}</main>
{footer(rel, built)}
</body></html>"""
