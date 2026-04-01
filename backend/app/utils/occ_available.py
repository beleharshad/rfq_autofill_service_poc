"""Utility to check if OCC (OpenCASCADE) is available."""

import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

_OCC_AVAILABLE = None
_OCC_BACKEND = None  # "OCP" | "pythonocc" | None
_OCC_ERROR = None


def check_occ_availability() -> Tuple[bool, Optional[str], Optional[str]]:
    """Check if OCC (OpenCASCADE) is available via OCP or pythonocc.
    
    Also verifies that RevolvedSolidBuilder can be imported, which is required
    for STEP generation.
    
    Returns:
        Tuple of (is_available, backend_name, error_message)
        backend_name: "OCP" | "pythonocc" | None
    """
    global _OCC_AVAILABLE, _OCC_BACKEND, _OCC_ERROR
    
    if _OCC_AVAILABLE is not None:
        return _OCC_AVAILABLE, _OCC_BACKEND, _OCC_ERROR
    
    # Try OCP first (CadQuery backend)
    try:
        import OCP
        from OCP.gp import gp_Pnt
        # Verify we can actually use it by testing RevolvedSolidBuilder import
        _verify_revolved_solid_builder_import()
        _OCC_AVAILABLE = True
        _OCC_BACKEND = "OCP"
        _OCC_ERROR = None
        logger.info("OCC (OpenCASCADE) is available via OCP (CadQuery backend)")
        return True, "OCP", None
    except ImportError as e:
        logger.debug(f"OCP not available: {e}")
    except Exception as e:
        logger.debug(f"Error checking OCP: {e}")
    
    # Try pythonocc (traditional PythonOCC)
    try:
        from OCC.Core.gp import gp_Pnt
        # Verify we can actually use it by testing RevolvedSolidBuilder import
        _verify_revolved_solid_builder_import()
        _OCC_AVAILABLE = True
        _OCC_BACKEND = "pythonocc"
        _OCC_ERROR = None
        logger.info("OCC (OpenCASCADE) is available via pythonocc")
        return True, "pythonocc", None
    except ImportError as e:
        _OCC_AVAILABLE = False
        _OCC_BACKEND = None
        _OCC_ERROR = str(e)
        logger.warning(f"OCC (OpenCASCADE) is not available: {e}")
        return False, None, str(e)
    except Exception as e:
        _OCC_AVAILABLE = False
        _OCC_BACKEND = None
        _OCC_ERROR = str(e)
        logger.warning(f"Error checking OCC availability: {e}")
        return False, None, str(e)


def _verify_revolved_solid_builder_import() -> None:
    """Verify that RevolvedSolidBuilder can be imported.
    
    This is a critical check because RevolvedSolidBuilder is required for STEP generation.
    If it can't be imported, OCC is not properly configured even if the core modules are available.
    
    Raises:
        ImportError: If RevolvedSolidBuilder cannot be imported
    """
    try:
        # Try to import RevolvedSolidBuilder
        from app.geometry.revolved_solid_builder import RevolvedSolidBuilder
        logger.debug("RevolvedSolidBuilder import successful")
    except ImportError as e:
        error_msg = f"RevolvedSolidBuilder cannot be imported: {e}. OCC may be installed but not properly configured."
        logger.warning(error_msg)
        raise ImportError(error_msg) from e
    except Exception as e:
        error_msg = f"Error importing RevolvedSolidBuilder: {e}. OCC may be installed but not properly configured."
        logger.warning(error_msg)
        raise ImportError(error_msg) from e


def occ_available() -> bool:
    """Check if OCC (OpenCASCADE) is available.
    
    Returns:
        True if OCC can be imported (via OCP or pythonocc), False otherwise
    """
    is_available, _, _ = check_occ_availability()
    return is_available


def get_occ_backend() -> Optional[str]:
    """Get the OCC backend name.
    
    Returns:
        "OCP" | "pythonocc" | None
    """
    _, backend, _ = check_occ_availability()
    return backend


def get_occ_error() -> Optional[str]:
    """Get the error message from OCC import attempt.
    
    Returns:
        Error message string, or None if OCC is available
    """
    _, _, error = check_occ_availability()
    return error

