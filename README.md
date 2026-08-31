# django-lookup

Product lookup & dedup for the Volkanos platform — "do we already have this?" across PIM and supplier
feeds (atlas), by identifier, text or image, with explainable `match` / `review` / `no_match` verdicts.

## Install

```shell
pip install entirius-django-lookup
```

Then read [`docs/install.md`](docs/install.md): Postgres extensions, the settings table, choosing an
embedding backend, bootstrap order, sizing. [`docs/settings_example.py`](docs/settings_example.py) is
the block to copy — it is under test.

## Docs

| Read | When |
|---|---|
| [`docs/install.md`](docs/install.md) | putting it on a host |
| [`docs/api.md`](docs/api.md) | calling `/search/` and `/check/` |
| [`docs/operations.md`](docs/operations.md) | day 2 — commands, Celery, degradation, tuning, calibration |
| [`docs/concept.md`](docs/concept.md) | why a weight, flag or threshold is what it is |
| [`docs/gotchas.md`](docs/gotchas.md) | before editing |
| [`AGENTS.md`](AGENTS.md) | the map — layout, conventions, where things live |

## Development

```shell
make install   # uv sync --all-extras
make test      # pytest against Postgres (DATABASE_URL or LOOKUP_TEST_DB_*; default: zeno on 5532)
make check     # ruff + canonical .gitleaks.toml
```

## License

MPL-2.0 — see `LICENSE`.
