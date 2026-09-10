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

## Live vessel positions (AIS)

`collector/ais_collect.py` runs every 15 minutes in GitHub Actions (`.github/workflows/ais.yml`),
listens to the aisstream.io feed for two minutes, keeps positions for vessels on the sanctioned
roster (`site/data/vessels.json`, every vessel with a published IMO or MMSI), and writes them to
the same Cloudflare KV namespace the API keys use. The map's "Live vessels" toggle and the
`/vessels/` page read them through `/api/v1/vessels`. OFAC and the UK publish IMO numbers but AIS
is keyed by MMSI, so the collector learns the IMO-to-MMSI mapping from ship static-data messages
over its first days; coverage grows with time.

Setup:
1. aisstream.io → sign in with GitHub → API keys → create one.
2. Cloudflare → My Profile → API Tokens → Create token → "Edit Cloudflare Workers" template is
   more than enough; or custom with permission Account · Workers KV Storage · Edit. Copy the token.
3. Account ID: Cloudflare dashboard, right column of any domain overview. KV namespace ID:
   Storage & databases → KV → `sanctionscope-keys` → the ID shown next to it.
4. GitHub repo → Settings → Secrets and variables → Actions → Secrets: `AISSTREAM_KEY`,
   `CF_API_TOKEN`, `CF_ACCOUNT_ID`, `CF_KV_NAMESPACE_ID`.
5. Actions → "AIS positions for sanctioned vessels" → Run workflow once; then it runs itself.

aisstream.io is free for non-commercial use. The vessel layer is therefore on the free site only
and is not part of the paid API tier. Sanctioned vessels frequently disable or spoof AIS; the site
says so wherever positions are shown.

## Protecting the free-tier quotas

Pages Functions (everything under `functions/`) count against the Workers free plan: **100,000
requests a day**, and **10 ms of CPU per request**. Static files are unmetered, so the map and all
the JSON under `site/` are free no matter the traffic; only the query endpoints cost quota.

Two things follow from that, and both are already done in the code:

* **The search index is precomputed.** `pipeline/api.py` writes a prefix-sharded inverted index to
  `site/api/v1/search/`. A query parses a few KB instead of rebuilding a token index over 37k
  parties on every cold start, which measured 420 ms of CPU against a 10 ms budget.
* **No `_middleware.js` under `/api/v1/`.** Pages middleware intercepts static assets on the same
  path, which would turn every index shard fetch into a billed Function call. CORS for the static
  files comes from `site/_headers`; the dynamic routes set their own.

What still needs doing in the dashboard, once:

1. Cloudflare → your domain → Security → WAF → **Rate limiting rules** → Create rule.
   Name `api`, expression `(http.request.uri.path contains "/api/v1/")`,
   characteristic *IP*, **60 requests per 1 minute**, action **Block** for 1 minute.
   The free plan includes exactly one of these rules, and this is the one worth spending it on.
2. Security → Bots → leave **Bot Fight Mode** on, and check Security → Events occasionally to make
   sure Googlebot is not being challenged.

If the API ever earns a paying customer, move to **Workers Paid ($5/month)**: it removes the daily
request cap and raises CPU from 10 ms to 30 s, which is what large screening batches actually need.
One subscriber covers it four times over.

## Legal pages

`terms.html`, `privacy.html` and `about.html` are generated nightly by `pipeline/pages.py` (edit the text there). They are linked from the footer of every page. Governing law is set to Florida; change it in pages.py if that's wrong for you. The contact address used throughout is hello@sanctionscope.com; set up Cloudflare Email Routing so it forwards to your inbox.

## Static pages and search engines

`pipeline/pages.py` runs after every build and writes plain HTML pages for every program
(`/programs/<code>/`), every country (`/countries/<iso>.html`) and every party with two or
more "Linked To" relationships (`/parties/<slug>.html`), plus `sitemap.xml` and `robots.txt`.
The workflow sets the base URL from the repo name; once you have a custom domain, add a
repository variable `SITE_URL` (Settings → Secrets and variables → Actions → Variables) with
the full URL, e.g. `https://sanctionscope.com/`, and submit `sitemap.xml` in Google Search Console.

## Map

The map is MapLibre GL (GPU-rendered) on an OpenFreeMap basemap (free, no key, commercial use
allowed; OpenStreetMap data) recoloured to the site palette at load time. CARTO's basemaps now
require an API key, so don't switch back to them without one. Points are clustered by the engine; lines are great circles; the
choropleth is our own country layer. The map loads a compact 7 MB dataset (`data/map-*.json`)
and fetches full records on click from the API shards, so the 30 MB full dataset is never sent
to the browser. If the basemap tiles can't be reached the map falls back to plain country polygons.

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
functions/            Cloudflare Pages Functions: search, screen, party lookup, vessels, key check, Stripe activation and webhook
collector/            AIS collector (GitHub Actions cron)
pipeline/og.py        optional: renders og.png for link previews
site/index.html       the site
site/vendor/          d3, topojson, MapLibre GL, world map (vendored)
site/data/            generated nightly; state.json is the diff memory, don't delete it
```

## Notes

* Placement: a party lands on its city when the listed address names one the gazetteer knows,
  otherwise it is spread inside the country. Unknown-address parties fall back to nationality.
* Only US lists for now. EU, UK, UN and Canada lists could be merged in the same way.
* Not legal advice. Confirm against the official record (linked from every party) before acting.
