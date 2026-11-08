# vendors

One interface for pulling data from market and filings data vendors, with each
vendor storing what it collects in its own folder.

| Vendor    | Source                                                                 | Datasets |
|-----------|------------------------------------------------------------------------|----------|
| `deribit` | [`external/derbit`](https://github.com/wetoyo/derbit) (submodule)       | `instruments`, `book_summary`, `order_books`, `trades`, `historical_volatility`, `index_price` + `stream()` |
| `kalshi`  | [`external/prediction_market_scraper`](https://github.com/wetoyo/prediction_market_scraper) (submodule, `Clients/Kalshi`) | `series`, `markets`, `orderbooks`, `trades` + `stream()` (needs API key) |
| `polymarket` | [`external/prediction_market_scraper`](https://github.com/wetoyo/prediction_market_scraper) (submodule, `Clients/Polymarket`) | `markets`, `events`, `orderbooks`, `price_history` + `stream()` |
| `yahoo`   | `vendors/yahoo/scraper.py`, ported from PocketQuant2's `YFinance.py`    | `prices`, `options` |
| `sec`     | `vendors/sec/edgar.py`, ported from Funkles-SEC's `scrape.py`           | `filings`, `shares_outstanding`, `company_filings` |
| **Weather** | | |
| `open_meteo` | Open-Meteo forecast, ERA5 archive and geocoding (no key)             | `forecast`, `historical`, `geocode` |
| `nws`     | NOAA National Weather Service, api.weather.gov (no key)                | `forecast`, `alerts`, `observations`, `stations` |
| **Satellite** | | |
| `copernicus` | Copernicus Data Space STAC: Sentinel-1 GRD SAR, Sentinel-2 L2A    | `scenes`, `aois`, `download` |
| `planetary_computer` | Microsoft Planetary Computer STAC, signed asset URLs (no key) | `scenes`, `collections`, `download` |
| `earth_search` | Element84 Earth Search STAC on AWS (no key)                      | `scenes`, `latest`, `download` |
| `nasa_firms` | NASA FIRMS VIIRS/MODIS fire and flare hotspots                     | `hotspots`, `site_scan`, `country`, `data_availability` |
| **Energy, shipping, flights** | | |
| `eia`     | US Energy Information Administration API v2                            | `series`, `weekly_petroleum`, `routes` |
| `aisstream` | aisstream.io live AIS vessel feed                                    | `positions`, `vessels` + `stream()` |
| `opensky` | OpenSky Network flight state vectors                                   | `states`, `flights`, `arrivals`, `departures` |
| **News** | | |
| `rss`     | 26 preset finance, energy, central-bank, macro and crypto feeds       | `items`, `feeds` |
| **Social** | | |
| `reddit`  | Reddit listings, search and comments (OAuth recommended)               | `posts`, `search`, `comments`, `ticker_mentions` |
| `bluesky` | Bluesky AT Protocol search and feeds, Jetstream firehose               | `search`, `author_feed` + `stream()` |
| `hackernews` | Algolia HN search and the official Firebase API (no key)           | `search`, `top`, `comments` |
| `stocktwits` | StockTwits public API (no key)                                     | `messages`, `sentiment`, `trending` |
| **Macro and markets** | | |
| `fred`    | St. Louis Fed FRED                                                     | `observations`, `series_info`, `search`, `release_dates` |
| `cftc`    | CFTC Commitments of Traders (legacy, disaggregated, TFF)               | `cot`, `markets` |
| `coingecko` | CoinGecko API v3 (no key needed)                                     | `markets`, `market_chart`, `ohlc`, `trending`, `global_metrics` |

## Setup

```bash
git clone --recurse-submodules https://github.com/wetoyo/vendors.git
cd vendors
pip install -e ".[all]"          # or pick extras: .[deribit], .[kalshi], .[polymarket], .[yahoo]
export SEC_USER_AGENT="Your Name you@example.com"   # required for the sec vendor
```

API keys, all read from env vars. Vendors not listed need nothing.

| Vendor | Env vars | Required? |
|--------|----------|-----------|
| `eia` | `EIA_API_KEY` | yes (free) |
| `fred` | `FRED_API_KEY` | yes (free) |
| `nasa_firms` | `FIRMS_MAP_KEY` | yes (free) |
| `aisstream` | `AISSTREAM_API_KEY` | yes (free) |
| `copernicus` | `CDSE_USERNAME`, `CDSE_PASSWORD` | only for `download` |
| `opensky` | `OPENSKY_CLIENT_ID`, `OPENSKY_CLIENT_SECRET` | only for `flights`/`arrivals`/`departures` |
| `reddit` | `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USER_AGENT` | in practice yes (anonymous JSON gets 403) |
| `bluesky` | `BSKY_HANDLE`, `BSKY_APP_PASSWORD` | no; anonymous search returns one page per term |
| `planetary_computer` | `PC_SDK_SUBSCRIPTION_KEY` | no; raises rate limits |
| `coingecko` | `COINGECKO_API_KEY`, `COINGECKO_PRO=1` | no |
| `cftc` | `SOCRATA_APP_TOKEN` | no |
| `nws`, `open_meteo`, `stocktwits`, `rss` | `NWS_USER_AGENT`, `OPEN_METEO_USER_AGENT`, `STOCKTWITS_USER_AGENT`, `RSS_USER_AGENT` | no; override the User-Agent |

Already cloned without submodules? `git submodule update --init --recursive`.
Pull upstream changes to the submodules with `git submodule update --remote`.

## Working on one vendor (sparse checkout)

**Agents and scripts: don't clone everything.** Check out the framework, the
vendor(s) you're touching, and only the submodule that vendor needs. Leave out
`--recurse-submodules`.

```bash
# 1. Framework only: root files + vendors/core + tests, no submodules, blobs fetched lazily
git clone --filter=blob:none --sparse https://github.com/wetoyo/vendors.git
cd vendors
git sparse-checkout set vendors/core tests

# 2. Add the vendor(s) you need (see the table below)
git sparse-checkout add vendors/kalshi

# 3. Fetch only that vendor's submodule, shallow
git submodule update --init --depth 1 external/prediction_market_scraper

# 4. (optional) Trim the submodule to the client you need
git -C external/prediction_market_scraper sparse-checkout set Clients/Kalshi

# 5. Install with just that vendor's extras
pip install -e ".[kalshi]" pytest
python -m pytest     # tests for vendors you didn't check out are skipped, not failed
```

| Vendor       | `sparse-checkout add`  | Submodule (`submodule update --init --depth 1 ...`) | Optional submodule trim | Extra          |
|--------------|------------------------|-------------------------------------------|-------------------------|----------------|
| `deribit`    | `vendors/deribit`      | `external/derbit`                         | (already small)         | `.[deribit]`   |
| `kalshi`     | `vendors/kalshi`       | `external/prediction_market_scraper`      | `Clients/Kalshi`        | `.[kalshi]`    |
| `polymarket` | `vendors/polymarket`   | `external/prediction_market_scraper`      | `Clients/Polymarket`    | `.[polymarket]`|
| `sec`        | `vendors/sec`          | none                                      | n/a                     | none           |
| `yahoo`      | `vendors/yahoo`        | none                                      | n/a                     | `.[yahoo]`     |
| `aisstream`  | `vendors/aisstream`    | none                                      | n/a                     | `.[aisstream]` |
| `bluesky`    | `vendors/bluesky`      | none                                      | n/a                     | `.[bluesky]` (only for `stream()`) |
| every other new vendor | `vendors/<name>` | none                               | n/a                     | none           |

The satellite vendors, `nasa_firms` and `aisstream` share the preset sites in
`vendors/core/aoi.py`, which comes with `vendors/core`.

Kalshi and Polymarket share a submodule. To work on both, trim it with
`sparse-checkout set Clients/Kalshi Clients/Polymarket`. When you add a new
vendor, `git sparse-checkout add vendors/<name>` before creating its folder,
or git won't track the new files.

## Usage

Every vendor works the same way:

```python
from vendors import get_vendor

kalshi = get_vendor("kalshi")
kalshi.datasets()                                   # what can I pull, with which params?
df = kalshi.fetch("markets", series_ticker="KXFED") # pull only
df = kalshi.collect("markets", series_ticker="KXFED")  # pull + store in data/kalshi/
df = kalshi.load("markets", status="active")        # read back what's stored
kalshi.storage.export("markets", fmt="parquet")     # -> data/kalshi/exports/markets.parquet

# Streams: wrap in aclosing() so breaking out of the loop closes the websocket cleanly
from contextlib import aclosing
async with aclosing(get_vendor("deribit").stream(["BTC-PERPETUAL"], ["ticker.100ms"])) as feed:
    async for msg in feed:
        ...
```

Same thing from the shell:

```bash
vendors list
vendors datasets deribit
vendors fetch   deribit index_price index_name=eth_usd
vendors collect deribit book_summary currency=ETH
vendors collect yahoo   prices tickers=AAPL,MSFT start=2026-01-01
vendors collect polymarket markets query=bitcoin
vendors collect polymarket price_history token_ids=<clob token id> interval=1w
vendors collect sec     filings form_type="SCHEDULE 13D" days=3
vendors collect open_meteo forecast location=houston days=7
vendors collect copernicus scenes aoi=cushing preset=oil_tanks_sar
vendors collect earth_search latest preset=oil_tanks_optical
vendors collect nasa_firms site_scan days=2
vendors collect eia     series preset=crude_stocks_cushing start=2024-01-01
vendors collect aisstream vessels aoi=fujairah seconds=300
vendors collect rss     items group=energy
vendors collect reddit  posts group=stocks sort=new limit=200
vendors collect cftc    cot preset=wti start=2025-01-01
vendors collect fred    observations preset=rates
vendors load    sec     filings --rows 20
vendors export  yahoo   prices --format csv
```

(`python -m vendors ...` works without installing.)

## The framework

### Contract

A vendor is a `Vendor` subclass (`vendors/core/base.py`) whose `@dataset`
methods each take keyword params and return a DataFrame, one row per record.

```python
class Vendor:
    name: str                                  # also the storage folder name
    def datasets(cls) -> dict[str, Dataset]    # discovered from @dataset methods
    def fetch(self, dataset, **params) -> DataFrame    # pull, normalize, don't store
    def collect(self, dataset, **params) -> DataFrame  # fetch + write to storage
    def load(self, dataset, **filters) -> DataFrame    # read stored data
    async def stream(self, ...) -> AsyncIterator[dict] # optional live feed
```

Conventions that `fetch()` applies to every vendor:

* column names become `snake_case` (`Adj Close` → `adj_close`, `contractSymbol` → `contract_symbol`)
* nested values (order-book levels, lists of CIKs) are stored as JSON text
* every stored row gets an `_ingested_at` UTC timestamp

### Storage: one folder per vendor

```
data/                     # or $VENDORS_DATA_DIR
  deribit/
    deribit.db            # SQLite, one table per dataset
    exports/              # parquet/csv dumps from storage.export()
  kalshi/kalshi.db
  yahoo/yahoo.db
  sec/
    sec.db
    files/filings/        # raw filing documents (XML)
```

A vendor only ever writes inside its own folder (`self.storage`, a
`VendorStorage`). Each dataset declares how repeat fetches behave:

* `@dataset(key=[...])`: **upsert**. Re-collecting the same key overwrites the
  row, so collects can be re-run safely (e.g. `markets` keyed by `ticker`).
* `@dataset()` with no key: **append**. Every collect adds rows; use this for
  snapshot time series (e.g. Kalshi `orderbooks`).

Tables are created on first write and gain columns automatically when the
upstream API adds fields, so there's no schema to maintain.

### Adding a vendor

1. Create `vendors/<name>/__init__.py`:

   ```python
   import pandas as pd
   from vendors.core import Vendor, dataset

   class AcmeVendor(Vendor):
       name = "acme"
       description = "Acme market data"

       @dataset(key=["symbol", "date"])
       def bars(self, symbol: str = "SPY", days: int = 30):
           """Daily bars."""
           return pd.DataFrame(acme_client.bars(symbol, days))

       @dataset(key=["path"])
       def reports(self, year: int = 2026):
           """Raw reports saved to data/acme/files/."""
           ...
           p = self.storage.write_file(f"reports/{name}.pdf", content)
   ```

2. Register it in `vendors/core/registry.py`: `"acme": "vendors.acme:AcmeVendor"`.
3. If its client lives in its own repo, add it as a submodule under
   `external/` and map its import path in `vendors/core/submodules.py`.
4. Add an offline test in `tests/test_adapters.py` (monkeypatch the API calls)
   and mark it `@pytest.mark.vendor("<name>")`.

## Layout

```
external/            git submodules (upstream code, untouched)
vendors/core/        base class, storage, registry, submodule importer
vendors/<vendor>/    one adapter package per vendor
tests/               offline tests: python -m pytest
CLAUDE.md            rules for agents working in this repo
data/                collected data (git-ignored)
```

## Notes

* **SEC identity:** SEC rejects requests without a User-Agent naming who you are.
  There is no built-in default (the original Funkles-SEC code hardcoded a
  personal name/email), so set `SEC_USER_AGENT` yourself; the vendor raises
  `MissingUserAgent` if it's unset.
* **Polymarket:** order-book and price datasets take CLOB *token* ids. Each
  market's `clob_token_ids` column lists them, one per outcome (`[Yes, No]`).
  `price_history` calls CLOB `/prices-history` directly because the submodule
  client has no history endpoint yet.
* **SEC:** the filing search calls EDGAR full-text search directly instead of
  through `datamule` (same endpoint). Requests are throttled below SEC's
  10 req/s limit. The 13D issuer CIK is read from the XML
  (`issuerCIK`), not taken as the first CIK on the hit.
* **Watching oil storage:** `vendors/core/aoi.py` presets 11 sites (Cushing,
  Houston Ship Channel, Corpus Christi, LOOP, Ras Tanura, Fujairah, Rotterdam,
  Jurong, Ningbo-Zhoushan, Qingdao, Saldanha Bay). Pass `aoi=<name>` or your own
  `bbox=min_lon,min_lat,max_lon,max_lat`. The satellite vendors take
  `preset=oil_tanks_sar` (Sentinel-1, sees through cloud, shows floating-roof
  shadows) or `preset=oil_tanks_optical` (Sentinel-2, cloud < 20%). Cross-check
  with `nasa_firms` (flaring), `aisstream` (tankers at the terminal) and `eia`
  (weekly Cushing and US crude stocks).
* **Satellite downloads:** `earth_search` serves Sentinel-2 over https for free;
  its Landsat and Sentinel-1 assets are requester-pays S3 and need AWS
  credentials. `planetary_computer` hrefs are SAS-signed and expire after about a
  day, so re-run `scenes` for fresh ones.
* **Reddit:** anonymous `*.json` is blocked (403). Create a "script" app at
  reddit.com/prefs/apps and set `REDDIT_CLIENT_ID` / `REDDIT_CLIENT_SECRET`.
* **Bluesky:** `search` splits a query on ` OR ` and runs one search per term,
  since `searchPosts` treats every word as required; `limit` is per term.
* **Not yet run against the live API (no key here):** `eia`, `fred`,
  `nasa_firms`, `aisstream`, the `copernicus` download and the OpenSky/Reddit
  auth paths. They're built from the docs and tested offline. In particular, check
  the EIA `refinery_utilization`, `crude_production` and `henry_hub_spot` routes.
* **Kalshi streaming** needs `KALSHI_API_KEY_ID` and `KALSHI_PRIVATE_KEY_PATH`.
