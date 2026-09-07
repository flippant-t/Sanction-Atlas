# Sanctions Atlas

Every party on the US sanctions and export-control lists, on one world map, with the
links between them. Updates itself every night. No server, no database, no API keys.

Source data: the [Consolidated Screening List](https://www.trade.gov/consolidated-screening-list)
published by trade.gov, which merges OFAC (SDN and non-SDN), BIS (Entity List, Denied Persons,
Unverified, Military End User) and State Department (nonproliferation, terrorist) lists.

## How it works

```
GitHub Actions, nightly
  └─ pipeline/build.py
       downloads consolidated.csv
       geocodes every address (GeoNames gazetteer, 34k cities, offline)
       parses "Linked To:" relationships out of OFAC remarks
       diffs against the previous run  →  additions / removals
       writes site/data/{parties,changes,meta,state}.json
  └─ commits the data back to the repo
  └─ deploys site/ to GitHub Pages
```

The site is one HTML file that loads those JSON files and draws everything on a canvas.

## Go live in ten minutes

1. Create a new GitHub repo and push this folder to it (branch `main`).
2. Repo Settings → Pages → Source: **GitHub Actions**.
3. Actions tab → "Nightly update and deploy" → **Run workflow**. First run takes 2 to 4 minutes.
4. Your site is at `https://<user>.github.io/<repo>/`.

Custom domain: Settings → Pages → Custom domain, add the CNAME at your registrar. GitHub issues the TLS cert.

Change tracking starts on the second run (it needs a previous snapshot to diff against).

### Cloudflare Pages instead

Same repo. Cloudflare Pages → connect the repo → build command empty, output directory `site`.
Cloudflare redeploys automatically when the nightly job commits new data. You can delete the
`deploy` job from the workflow in that case.

## Run locally

```
pip install -r pipeline/requirements.txt
python pipeline/build.py               # downloads the live list (~20 MB)
python pipeline/build.py --input consolidated.csv   # or use a local copy
cd site && python -m http.server 8000  # open http://localhost:8000
```

## Shareable URLs

State lives in the hash: `#p=<party id>`, `#prog=RUSSIA-EO14024,IRAN`, `#src=OFAC SDN`,
`#kind=Vessel`, `#lines=link`. The "Copy link" button in a party's panel copies the current one.

## Layout

```
pipeline/build.py     the whole data pipeline
pipeline/og.py        optional: renders og.png for link previews
site/index.html       the site
site/vendor/          d3, topojson, world map (vendored, works offline)
site/data/            generated nightly; state.json is the diff memory, don't delete it
```

## Notes

* Placement: a party lands on its city when the listed address names one the gazetteer knows,
  otherwise it is spread inside the country. Unknown-address parties fall back to nationality.
* Only US lists for now. EU, UK, UN and Canada lists could be merged in the same way.
* Not legal advice. Confirm against the official record (linked from every party) before acting.
