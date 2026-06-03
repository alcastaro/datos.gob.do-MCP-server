# Contributing

Thanks for your interest. This is a small solo project; contributions are welcome but scope is intentionally narrow.

## Bug reports

Open an issue with:
- Version (`pip show dominican-open-data-mcp`)
- Python version
- Minimal reproduction steps
- What you expected vs. what happened

## Pull requests

1. **Fork + branch** from `main`.
2. **Install dev deps:** `uv sync --group dev --extra dev`
3. **Quality gates must pass before opening the PR:**
   ```bash
   uv run ruff check src/ tests/
   uv run ruff format src/ tests/
   uv run mypy src/datosgobdo_mcp/ --no-error-summary
   uv run pytest
   ```
4. **Tests:** add hermetic tests (no live network) for any new behaviour. Live tests go in `tests/test_live_api.py` under `@pytest.mark.live`.
5. **Scope:** this server targets `datos.gob.do` specifically. Changes that add tools for other portals belong in `opendata-latam-mcp` instead.
6. **Security issues:** please report privately via [GitHub Security Advisories](https://github.com/alcastaro/datos.gob.do-MCP-server/security/advisories/new) rather than opening a public issue. See [SECURITY.md](SECURITY.md).

## Code style

- No inline comments that describe *what* code does (names do that). Comment *why* only.
- No unnecessary abstractions; three similar lines beat a premature helper.
- All SQL input (column names, literals) must go through `_quote_ident` / `_quote_literal` in `analytics.py`. No f-string interpolation of user values.
- All CKAN filter values must go through `_escape_solr` / `_fq_term` in `ckan.py`.
