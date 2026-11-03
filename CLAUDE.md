# Notes for agents

- **Clone sparsely.** Check out `vendors/core`, `tests`, and only the vendor
  you're working on, plus only that vendor's submodule (shallow). Never use
  `--recurse-submodules` or check out every vendor. The exact commands and the
  vendor → paths/submodule table are under "Working on one vendor (sparse
  checkout)" in README.md.
- The framework contract (Vendor base class, `@dataset`, per-vendor storage
  in `data/<vendor>/`) and "Adding a vendor" are also in README.md. Follow
  them; don't add per-vendor storage paths or CLIs.
- Don't edit code under `external/`. Those are other repos: fix them upstream,
  then bump the submodule pointer here.
- Tests are offline (`python -m pytest`). Mark vendor-specific tests with
  `@pytest.mark.vendor("<name>")` so they skip on partial checkouts.
- Never hardcode personal identity (emails, names, API keys). Read them from
  env vars, e.g. `SEC_USER_AGENT`, `KALSHI_API_KEY_ID`.
