"""Shared pytest setup.

`custom_components/longse_nvr/client.py` is importable without Home
Assistant (it only needs `aiohttp` + `.const`), so client tests run with a
bare `pytest`. Config-flow tests need `pytest-homeassistant-custom-component`
(see `tests/test_config_flow.py` — skipped automatically when HA is absent).

The `longse_nvr` alias below lets tests import the client WITHOUT executing
`custom_components/longse_nvr/__init__.py` (which imports homeassistant).
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    # Needed for `import custom_components.longse_nvr.*` (a namespace package).
    sys.path.insert(0, str(_REPO_ROOT))

_pkg = types.ModuleType("longse_nvr")
_pkg.__path__ = [str(_REPO_ROOT / "custom_components" / "longse_nvr")]
sys.modules.setdefault("longse_nvr", _pkg)

try:
    import homeassistant  # noqa: F401
except ImportError:
    _HAS_HA = False
else:
    _HAS_HA = True
    pytest_plugins = "pytest_homeassistant_custom_component"


if _HAS_HA:

    @pytest.fixture(autouse=True)
    def auto_enable_custom_integrations(enable_custom_integrations):
        yield
