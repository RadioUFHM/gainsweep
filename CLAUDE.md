# CLAUDE.md — Conventions for Claude Code

This file is read automatically by Claude Code at the start of every session. Keep it short and current.

## Source of truth

**`docs/SPEC.md` is the design spec.** Read it before starting any new feature. If a task contradicts the spec, ask before proceeding — the spec may need updating, but it shouldn't be silently bypassed.

## Workflow

1. **Branch per task.** Never commit to `main` directly. Use `feat/`, `fix/`, `chore/`, `docs/`, `test/` prefixes.  
   - Example: `feat/coingecko-price-provider`  
2. **Conventional commits.** Format: `type(scope): subject`. Examples:  
   - `feat(price): add CoinGecko provider with caching`  
   - `fix(alert): re-arm on new daily high`  
   - `test(backtest): add regret metric calculation`  
3. **PR even to yourself.** Use `gh pr create` to open a PR back to `main`. Squash-merge.  
4. **One feature at a time.** Don't mix unrelated changes in one branch.

## Code conventions

- **Python 3.12+**, type-hinted everywhere, `from __future__ import annotations` at the top of every module.  
- **Decimal for money, never float.** Import: `from decimal import Decimal`. Construct from strings: `Decimal("3421.50")`, never `Decimal(3421.50)`.  
- **UTC timestamps only.** `datetime.now(timezone.utc)`. Never naive datetimes.  
- **No `print()` in library code.** Use `logging` with structured fields.  
- **Tests live in `tests/`**, mirroring the `src/` layout. `pytest` is the runner.  
- **Run formatters before committing.** `ruff format` and `ruff check --fix`.

## Architecture rules (from SPEC.md §3, restated)

1. Components live behind protocols/interfaces. New concrete implementations are additive only.  
2. Every state change is logged. Same input \+ same state → same output. Backtests use production code paths.  
3. Fail safe. When in doubt, alert and do nothing.  
4. Trade-only API scopes. The system is structurally incapable of withdrawing funds from exchanges.

## When to ask vs. when to proceed

- **Ask first:** schema changes, new external dependencies, anything touching credentials/secrets, anything that deviates from SPEC.md.  
- **Proceed:** writing tests, adding type hints, refactoring within a module, fixing obvious bugs, formatting.

## Useful commands

```shell
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Format + lint
ruff format . && ruff check --fix .

# Type check
mypy src/

# Run a backtest (once Phase 5 is built)
python -m merchant_sweep.backtest --help
```

## What's where

```
docs/SPEC.md          The design spec. Read this first.
src/merchant_sweep/   Application code.
tests/                Tests, mirroring src/ layout.
migrations/           Alembic DB migrations.
scripts/              One-off scripts and dev tooling.
```

