# grip-py

`grip-py` is the Python package repository for GRIP runtime work.

GitHub:
- `git@github.com:owebeeone/grip-py.git`

Package details:
- PyPI project name: `grip-py`
- Python import package: `grip_py`
- Build backend: `hatchling`

## Grip Identity

Grips use canonical scoped keys:

- Every grip has `scope` and `name`.
- Canonical key format is `<scope>:<name>`.
- Default scope is `app`.

Examples:

```python
from grip_py import GripRegistry

registry = GripRegistry()

theme = registry.add("Theme", "light")
session_theme = registry.add("Theme", "dark", scope="session")

assert theme.key == "app:Theme"
assert session_theme.key == "session:Theme"
```

## Local Development

```bash
git clone git@github.com:owebeeone/grip-py.git
cd grip-py
pip install -e .
```

## Test

Using hatch:

```bash
hatch run test:pytest
```

Or directly:

```bash
PYTHONPATH=src pytest
```

Focused context/stream tests:

```bash
PYTHONPATH=src pytest tests/core/test_context.py tests/core/test_async_stream_tap.py -q
```

## Keyed contexts

Cache named child or matching contexts on a parent facade:

```python
child = parent.get_or_create_child_context("weather:left", init=setup_column)
matching = parent.get_or_create_matching_context("coin:A", init=setup_coin_matcher)
```

- `init` runs once when a new live entry is created. Use stable named functions.
- Same key under different parents does not collide.
- `get_or_create_matching_context` creates `parent → home → presentation`.
  Register column inputs on home; read outputs from presentation.

See `grip-core/GRIP_CONTEXT.md` (in the grip-core repo) for parameter semantics.

## Async stream taps

Use `create_async_stream_multi_tap` for long-lived subscriptions (websockets,
tick feeds). Use `create_async_tap` for one-shot fetch/response flows.

```python
from grip_py.core import create_async_stream_multi_tap, AsyncStreamRetryConfig

tap = create_async_stream_multi_tap(
    provides=[PRICE, STATUS],
    destination_param_grips=[PRODUCT],
    request_key_of=lambda params: f"feed:{params.destination_params[PRODUCT]}",
    subscribe=websocket_iterable,
    map_event=lambda params, event: {PRICE: event.price, STATUS: event.status},
    retry=AsyncStreamRetryConfig(initial_delay_ms=1000, max_delay_ms=30_000),
)
```

See `grip-core/GRIP_STREAM_TAPS.md` and `grip-py-demo` coin stream UI for a full
example.

## Build Distributions

```bash
python -m pip install build
python -m build
```
