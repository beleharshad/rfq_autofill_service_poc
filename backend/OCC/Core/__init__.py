"""Import shim that maps `OCC.Core.*` modules to `OCP.*` modules.

`cadquery-ocp` exposes OpenCascade bindings under the `OCP` namespace rather
than `OCC.Core`.  The backend codebase still imports `OCC.Core.*`, so this shim
bridges the two layouts without touching the rest of the code.
"""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.util
import sys
from types import ModuleType

_PREFIX = __name__
_TARGET_PREFIX = "OCP"


class _OcpAliasLoader(importlib.abc.Loader):
    """Loader that aliases `OCC.Core.*` modules to `OCP.*` modules."""

    def __init__(self, fullname: str) -> None:
        self.fullname = fullname
        self.target_name = f"{_TARGET_PREFIX}.{fullname[len(_PREFIX) + 1:]}"

    def create_module(self, spec):  # type: ignore[override]
        module = importlib.import_module(self.target_name)
        sys.modules[self.fullname] = module
        return module

    def exec_module(self, module: ModuleType) -> None:  # type: ignore[override]
        sys.modules[self.fullname] = module


class _OcpAliasFinder(importlib.abc.MetaPathFinder):
    """Meta path finder that resolves `OCC.Core.*` from `OCP.*`."""

    def find_spec(self, fullname: str, path=None, target=None):
        if not fullname.startswith(f"{_PREFIX}."):
            return None

        target_name = f"{_TARGET_PREFIX}.{fullname[len(_PREFIX) + 1:]}"
        target_spec = importlib.util.find_spec(target_name)
        if target_spec is None:
            return None

        alias_spec = importlib.util.spec_from_loader(
            fullname,
            _OcpAliasLoader(fullname),
            origin=target_spec.origin,
            is_package=target_spec.submodule_search_locations is not None,
        )
        if alias_spec and target_spec.submodule_search_locations is not None:
            alias_spec.submodule_search_locations = list(target_spec.submodule_search_locations)
        return alias_spec


if not any(isinstance(finder, _OcpAliasFinder) for finder in sys.meta_path):
    sys.meta_path.insert(0, _OcpAliasFinder())


def __getattr__(name: str):
    return importlib.import_module(f"{_PREFIX}.{name}")
