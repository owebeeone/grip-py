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

## Build Distributions

```bash
python -m pip install build
python -m build
```
