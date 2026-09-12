"""Fails if pipeline/matching.py and functions/api/v1/_lib.js score a pair differently.

Monitoring (Python, nightly) and /api/v1/screen (JS, at the edge) must agree, or the same
customer gets two different answers. Needs node on PATH; skips with a warning if absent.
"""
import json, os, shutil, subprocess, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "pipeline"))
import matching

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PAIRS = [
    ("Sberbank of Russia", ["PUBLIC JOINT STOCK COMPANY SBERBANK OF RUSSIA"]),
    ("sberbank", ["PUBLIC JOINT STOCK COMPANY SBERBANK OF RUSSIA"]),
    ("Bank Melli Iran", ["MELLI BANK PLC", "BANK MELLI"]),
    ("Mahan Air", ["MAHAN AIRWAYS", "MAHAN AIR GENERAL TRADING LLC"]),
    ("MAHAN AIR", ["MAHAN AIR"]),
    ("Acme Trading FZE", ["ACME TRADING FZE"]),
    ("Jose Marti", ["JOSE MARTI"]),
    ("José Martí", ["JOSE MARTI"]),
    ("Ivan Ivanov", ["IVANOV, Ivan Ivanovich"]),
    ("Kim Jong Un", ["KIM, Jong Un"]),
    ("O'Brien & Sons, Ltd.", ["OBRIEN AND SONS LIMITED"]),
    ("VLADIMIR PUTIN", ["PUTIN, Vladimir Vladimirovich"]),
    ("a", ["ACME"]),
    ("LLC THE OF AND", ["ACME LLC"]),
    ("", ["ACME"]),
    ("Rosneft", ["Open Joint-Stock Company Rosneft Oil Company"]),
    ("Islamic Republic of Iran Shipping Lines", ["IRAN SHIPPING LINES", "IRISL"]),
    ("Zhongchen", ["ZHONGCHEN ELECTRONICS CO LTD", "CHEN ZHONG"]),
    ("MB BANK", ["MB BANK", "BANK MELLI IRAN"]),
    ("Gazprom Neft", ["GAZPROMNEFT", "GAZPROM NEFT PJSC"]),
    ("SEPEHR ENERGY JAHAN NAMA PARS", ["SEPEHR ENERGY JAHAN NAMA PARS COMPANY"]),
    ("xyzzy nonsense name", ["ACME TRADING FZE"]),
    ("Müller GmbH", ["MULLER GMBH"]),
    ("北京公司", ["BEIJING COMPANY"]),
    ("123456", ["123456 ALBERTA LTD"]),
]

JS = r"""
const fs=require('fs');
const src=fs.readFileSync(process.argv[2],'utf8')
  .replace(/^import[^\n]*$/gm,'').replace(/\bexport\s+(const|function|async function|class)\b/g,'$1');
const mod={};
new Function('module','exports','crypto','Response','URL',src+'; module.exports={scoreRec,tokens,norm,jw};')(mod,{},{},function(){},URL);
const {scoreRec}=mod.exports;
const pairs=JSON.parse(fs.readFileSync(process.argv[3],'utf8'));
// rec shape used by scoreRec: [id, name, type, country, authorities, programs, aliases]
console.log(JSON.stringify(pairs.map(([q,names])=>
  +scoreRec(q,[null,names[0],null,null,[],[],names.slice(1)]).toFixed(6))));
"""

def test_parity():
    node = shutil.which("node")
    if not node:
        print("SKIP: node not on PATH, cannot check JS/Python parity")
        return
    with tempfile.TemporaryDirectory() as d:
        js, pj = os.path.join(d, "r.js"), os.path.join(d, "p.json")
        open(js, "w").write(JS)
        json.dump(PAIRS, open(pj, "w"))
        out = subprocess.run([node, js, os.path.join(ROOT, "functions/api/v1/_lib.js"), pj],
                             capture_output=True, text=True)
        assert out.returncode == 0, "node failed: " + out.stderr[-800:]
        js_scores = json.loads(out.stdout)
    bad = []
    for (q, names), got in zip(PAIRS, js_scores):
        mine = round(matching.score(q, names), 6)
        if abs(mine - got) > 1e-6:
            bad.append((q, names[0], got, mine))
    for b in bad:
        print("MISMATCH query=%r vs %r  js=%.6f python=%.6f" % b)
    assert not bad, f"{len(bad)} of {len(PAIRS)} pairs scored differently"
    print(f"parity ok: {len(PAIRS)} pairs identical in JS and Python")

if __name__ == "__main__":
    test_parity()
