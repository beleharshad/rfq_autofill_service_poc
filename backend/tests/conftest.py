"""
conftest.py — session-wide test fixtures and module stubs.

EasyOCR imports PyTorch at module level.  On some Windows machines (and in CI)
PyTorch fails to load its native DLLs, causing a fatal Windows exception that
kills the entire pytest process before a single test runs.

We stub out the problematic packages in sys.modules BEFORE any test module is
imported.  The real easyocr code path is only needed by AutoDetectService;
every test that exercises auto-detection monkeypatches the service directly,
so the stub is sufficient.
"""
from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock


def _stub_module(name: str) -> MagicMock:
    """Return (and register) a MagicMock for *name* if not already present."""
    if name not in sys.modules:
        mock = MagicMock(name=name)
        sys.modules[name] = mock
        return mock
    return sys.modules[name]  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Stub torch + easyocr and their sub-packages so importing auto_detect_service
# (and therefore app.main) does not trigger a DLL-load fatal exception.
# ---------------------------------------------------------------------------
_TORCH_SUBMODULES = [
    "torch",
    "torch.nn",
    "torch.nn.functional",
    "torch.cuda",
    "torch.utils",
    "torch.utils.data",
    "torch.backends",
    "torch.backends.cuda",
    "torch.backends.cudnn",
]

for _mod in _TORCH_SUBMODULES:
    _stub_module(_mod)

_EASYOCR_SUBMODULES = [
    "easyocr",
    "easyocr.easyocr",
    "easyocr.recognition",
    "easyocr.detection",
    "easyocr.utils",
]

for _mod in _EASYOCR_SUBMODULES:
    _stub_module(_mod)

# Expose easyocr.Reader as a MagicMock class so ``import easyocr; easyocr.Reader(...)``
# works without crashing.
sys.modules["easyocr"].Reader = MagicMock(name="easyocr.Reader")
