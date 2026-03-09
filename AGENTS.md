# AGENTS

## Scope
This file defines repository-specific coding instructions for `grip-py`.

These same Python rules should also be applied to Python Glial modules in this repository, including:

- `glial-local-py`
- `glial-net-py`
- `glial-router-py`

## Design Rules
- Prefer `@dataclass` for classes that primarily hold state.
- When a class is not a good dataclass candidate (for example, protocol classes or classes requiring highly custom initialization/slots behavior), keep it as a normal class and document that choice in code review notes or PR description.
- Keep type annotations complete and precise so IDE inference remains strong.

## Development Process (TDD)
Use a strict red/green/refactor workflow for all behavior changes.

1. Red: Add or update tests first to express the expected behavior, then run the smallest relevant test target and confirm it fails.
2. Green: Implement the minimal code change required to make the failing test pass.
3. Refactor: Improve structure/readability while preserving behavior.
4. Verify targeted scope: Re-run the focused test subset.
5. Verify full regression: Run the full test suite before finalizing.

## Test Commands
- Focused tests: `uv run --with pytest --with pytest-cov pytest <test-path> -q`
- Full suite: `uv run --with pytest --with pytest-cov pytest -q`
