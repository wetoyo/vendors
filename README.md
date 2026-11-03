# vendors

One interface for pulling data from market and filings data vendors, with each
vendor storing what it collects in its own folder.

| Vendor    | Source                                                                 | Datasets |
|-----------|------------------------------------------------------------------------|----------|
| `deribit` | [`external/derbit`](https://github.com/wetoyo/derbit) (submodule)       | `instruments`, `book_summary`, `order_books`, `trades`, `historical_volatility`, `index_price` + `stream()` |
| `kalshi`  | [`external/prediction_market_scraper`](https://github.com/wetoyo/prediction_market_scraper) (submodule, `Clients/Kalshi`) | `series`, `markets`, `orderbooks`, `trades` + `stream()` (needs API key) |
| `yahoo`   | `vendors/yahoo/scraper.py`, ported from PocketQuant2's `YFinance.py`    | `prices`, `options` |
| `sec`     | `vendors/sec/edgar.py`, ported from Funkles-SEC's `scrape.py`           | `filings`, `shares_outstanding`, `company_filings` |

## Setup

```bash
git clone --recurse-submodules https://github.com/wetoyo/vendors.git
cd vendors
pip install -e ".[all]"          # or pick extras: .[deribit], .[kalshi], .[yahoo]
export SEC_USER_AGENT="Your Name you@example.com"   # SEC requires real contact info
```

Already cloned without submodules? `git submodule update --init --recursive`.
Pull upstream changes to the submodules with `git submodule update --remote`.

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

async for msg in get_vendor("deribit").stream(["BTC-PERPETUAL"], ["ticker.100ms"]):
    ...
```

Same thing from the shell:

```bash
vendors list
vendors datasets deribit
vendors fetch   deribit index_price index_name=eth_usd
vendors collect deribit book_summary currency=ETH
vendors collect yahoo   prices tickers=AAPL,MSFT start=2026-01-01
vendors collect sec     filings form_type="SCHEDULE 13D" days=3
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
4. Add an offline test in `tests/test_adapters.py` (monkeypatch the API calls).

## Layout

```
external/            git submodules (upstream code, untouched)
vendors/core/        base class, storage, registry, submodule importer
vendors/<vendor>/    one adapter package per vendor
tests/               offline tests: python -m pytest
data/                collected data (git-ignored)
```

## Notes

* **SEC:** the filing search calls EDGAR full-text search directly instead of
  through `datamule` (same endpoint). Requests are throttled below SEC's
  10 req/s limit. The 13D issuer CIK is read from the XML
  (`issuerCIK`), not taken as the first CIK on the hit.
* **Kalshi streaming** needs `KALSHI_API_KEY_ID` and `KALSHI_PRIVATE_KEY_PATH`.
