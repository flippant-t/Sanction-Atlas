# SanctionScope

Every party on the US, EU, UK, UN, Australian and Canadian sanctions lists, on one world
map, with the links between them and which authorities agree. Updates itself every night.
No server, no database, no API keys.

Source data: the [Consolidated Screening List](https://www.trade.gov/consolidated-screening-list)
published by trade.gov, which merges OFAC (SDN and non-SDN), BIS (Entity List, Denied Persons,
Unverified, Military End User) and State Department (nonproliferation, terrorist) lists.

## Sources

| Authority | List | Format |
|---|---|---|
| US | Consolidated Screening List (OFAC SDN and non-SDN, BIS Entity/Denied/Unverified/MEU, State ISN and AECA) | CSV |
| EU | Consolidated Financial Sanctions List | CSV |
| UK | UK Sanctions List (FCDO), which replaced the OFSI Consolidated List in January 2026 | CSV |
| UN | Security Council Consolidated List | XML |
| Australia | DFAT Consolidated List | XLSX |
| Canada | SEMA / autonomous sanctions list | XML |

Parsers live in `pipeline/sources.py`. Each one is isolated: if a download fails or a format
changes, that authority is skipped for the night, the site shows it as "failed to load", and
everything else still builds. Parties that appear on several lists are merged by normalised
name (legal suffixes stripped, individual name order ignored, birth years must not conflict).
That is approximate by design; every merged party shows its separate official records.

Programs from non-US lists are prefixed with the authority (`EU:RUS`, `UK:Cyber`, `UN:DPRK`).

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

### Cloudflare Pages (recommended once you have a domain; required for the query API)

The nightly GitHub Action keeps doing the heavy work (downloads, geocoding, diff) and commits
`site/data/`. Cloudflare Pages then builds the static pages and API from that data and serves
everything, including the query endpoints in `functions/`.

1. Cloudflare dashboard → Workers & Pages → Create → Pages → Connect to Git → pick the repo.
2. Build settings: Framework preset **None**. Build command:
   `python3 pipeline/pages.py "$SITE_URL" && python3 pipeline/api.py "$SITE_URL"`
   Build output directory: `site`. Root directory: leave blank.
3. Environment variables (Production): `SITE_URL` = your full URL with trailing slash, e.g.
   `https://sanctionscope.com/`. Also set the same `SITE_URL` as a GitHub repository variable.
4. Save and deploy. Every nightly data commit triggers a redeploy automatically.
5. Custom domain: Pages project → Custom domains → add it. Cloudflare handles DNS and TLS.
6. Once Cloudflare is live, turn off GitHub Pages (repo Settings → Pages → Source: None) so
   there's one copy of the site, and delete the `deploy` job from the workflow.

Query endpoints (`/api/v1/search`, `/api/v1/screen`, `/api/v1/party/<id>`) are Cloudflare Pages
Functions in `functions/api/v1/`. They read the static index the build wrote. The free plan's
CPU limit is enough for search and small screening batches; the in-browser screener at
`/screen.html` has no limit because it runs on the visitor's machine.

Local test of the functions: `npx wrangler pages dev site` after a build.

### GitHub Pages only (no query API)

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

## API

Documented at `/api/` on the site. Static JSON under `/api/v1/` (meta, parties in parts,
index, changes, programs, countries, RSS feed, OpenAPI) is regenerated every build; the
query endpoints need Cloudflare Pages. All endpoints are free, keyless and CORS-enabled.

## Paid API tier (Stripe)

Keys are optional; everything works without one at free limits. To sell the Pro tier:

1. Stripe → Products → add "SanctionScope API Pro", recurring monthly price. Then Payment Links →
   create one for it. Under "After payment" choose "Don't show confirmation page" and set the
   redirect to `https://sanctionscope.com/api/activate?session_id={CHECKOUT_SESSION_ID}`
   (Stripe fills the placeholder). Copy the link URL.
2. Stripe → Developers → Webhooks → Add endpoint `https://sanctionscope.com/api/stripe-webhook`,
   events `customer.subscription.deleted` and `customer.subscription.updated`. Copy the signing secret.
3. Cloudflare → Workers & Pages → KV → Create namespace `sanctionscope-keys`.
4. Pages project → Settings → Bindings → Add → KV namespace, variable name `KEYS`, pick the namespace.
5. Pages project → Settings → Variables and secrets (Production):
   `STRIPE_SECRET` = your Stripe secret key (encrypt), `STRIPE_WEBHOOK_SECRET` = the signing secret (encrypt),
   `STRIPE_PAYMENT_LINK` = the payment link URL (plain; used at build time for the Subscribe button).
6. Retry deployment. The API docs page shows a Subscribe button; a paying customer lands on
   /api/activate and gets a key; the key is checked on every query request (`x-api-key` header
   or `?key=`); the webhook switches it off if the subscription lapses.

Limits per tier are in `functions/api/v1/_lib.js` (`LIMITS`). Pricing is whatever you set in Stripe.

## Static pages and search engines

`pipeline/pages.py` runs after every build and writes plain HTML pages for every program
(`/programs/<code>/`), every country (`/countries/<iso>.html`) and every party with two or
more "Linked To" relationships (`/parties/<slug>.html`), plus `sitemap.xml` and `robots.txt`.
The workflow sets the base URL from the repo name; once you have a custom domain, add a
repository variable `SITE_URL` (Settings → Secrets and variables → Actions → Variables) with
the full URL, e.g. `https://sanctionscope.com/`, and submit `sitemap.xml` in Google Search Console.

## Map features

Country bubbles at world view, city bubbles closer in, individual parties past zoom 4. Country
panel with intensity index, authority and program breakdown. "Why listed" category filter.
Authority filter and two-authority compare mode. Timeline slider over listing dates (published
for the EU, UK, UN, AU and CA lists; OFAC does not include them in the CSL feed). "Show only
additions" layer over the change feed. Five guided stories under Explore, defined in the
`STORIES` array in `site/index.html`; add your own the same way.

## Shareable URLs

State lives in the hash: `#p=<party id>`, `#prog=RUSSIA-EO14024,IRAN`, `#src=OFAC SDN`,
`#kind=Vessel`, `#lines=link`, `#c=ru` (country panel). The "Copy link" button in a party's panel copies the current one.

## Layout

```
pipeline/build.py     the whole data pipeline
pipeline/pages.py     static program, country and party pages, sitemap
pipeline/api.py       static API files and docs
functions/            Cloudflare Pages Functions: search, screen, party lookup, key check, Stripe activation and webhook
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
