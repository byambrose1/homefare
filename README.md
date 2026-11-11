# HomeFare engine

The private engine behind HomeFare. Every morning it collects flight prices home from UK airports, works out what each route normally costs, flags fares that are genuinely cheap, and checks them live on Google Flights. A person reviews every deal before any member sees it.

## How it works

| Step | File | What it does |
|---|---|---|
| 1 | `engine/collect.py` | Gets the cheapest return fare for every UK airport × home city × travel month (next 12 months) from Travelpayouts. Free. |
| 2 | `engine/find_deals.py` | Usual price = the median of the last 90 days for that route and month. Flags fares at least 25% below it and among the cheapest 10% ever recorded. Routes with under 20 days of history are "learning". |
| 3 | `engine/live_check.py` | Re-checks the best candidates live on Google Flights (via SerpApi). While routes are learning, it uses Google's own typical price range instead. Verified deals go into `data/deal_log.csv` as `awaiting_review`. |

Settings (airports, cities, deal rules, daily search limit) are all in `config/routes.json`.

## Where the results are

| File | What's in it |
|---|---|
| `data/prices/YYYY-MM.csv` | Every price recorded, by the month it was checked |
| `data/baselines.csv` | Usual price per route and travel month |
| `data/candidates.json` | Today's candidate deals, plus routes still learning |
| `data/live_checks_latest.json` | Today's live Google Flights checks |
| `data/deal_log.csv` | Every verified deal: the start of the public deal log |
| `data/reports/` | A short report from each step (coverage, errors, searches used) |

## Secrets (the two keys)

The keys are stored in GitHub, encrypted. They are never written in any file here.

1. On GitHub, open this repository → **Settings** → **Secrets and variables** → **Actions**.
2. Click **New repository secret**.
3. Name `TRAVELPAYOUTS_TOKEN`, paste the Travelpayouts token, click **Add secret**.
4. Click **New repository secret** again. Name `SERPAPI_KEY`, paste the SerpApi key, **Add secret**.

## Running it

It runs automatically every day at 05:00 UTC. To run it now: **Actions** tab → **Daily prices** → **Run workflow**.

## Budget

SerpApi's free plan is 250 searches a month, so `max_checks_per_day` in `config/routes.json` is set to 6. Raise it after upgrading the plan.

## The website

`site/index.html` is the live site on Netlify. `site/homefare.html` is the same page without the outer HTML wrapper.
