"""
Debug Viewer Module - DEBUG ONLY

This module provides minimal 3D visualization capability strictly for
testing and validation of generated 3D solids.

WARNING: This module is for debugging only. Do not use in production.
"""

from typing import Optional
from OCC.Core.TopoDS import TopoDS_Solid
from OCC.Display.SimpleGui import init_display


class DebugViewer:
    """Minimal 3D viewer for debugging TopoDS_Solid geometry - DEBUG ONLY."""
    
    def __init__(self):
        """Initialize the debug viewer - DEBUG ONLY."""
        self.display, self.start_display, self.add_menu, self.function = init_display()
        self._is_displaying = False
        self._current_solid: Optional[TopoDS_Solid] = None
    
    def display_solid(self, solid: TopoDS_Solid) -> None:
        """Display a TopoDS_Solid for debugging - DEBUG ONLY.
        
        Args:
            solid: TopoDS_Solid to display
        """
        if solid.IsNull():
            return  # Graceful failure for null solid
        
        self._current_solid = solid
        
        # Clear previous display
        self.display.EraseAll()
        
        # Display the solid
        self.display.DisplayShape(solid, update=True)
        
        # Auto-fit view
        self.display.FitAll()
        
        # Set isometric orientation
        try:
            self.display.View_Iso()
        except Exception:
            pass  # Ignore if not supported
        
        self._is_displaying = True
    
    def display_and_wait(self) -> None:
        """Display and wait for user interaction - DEBUG ONLY."""
        if self._current_solid is None or self._current_solid.IsNull():
            return  # Graceful failure
        
        self.start_display()
    
    def clear(self) -> None:
        """Clear the display - DEBUG ONLY."""
        self.display.EraseAll()
        self._is_displaying = False
        self._current_solid = None
    
    def is_displaying(self) -> bool:
        """Check if viewer is currently displaying geometry - DEBUG ONLY.
        
        Returns:
            True if displaying, False otherwise
        """
        return self._is_displaying


def view_solid(solid: TopoDS_Solid, title: Optional[str] = None) -> None:
    """Convenience function to quickly view a solid - DEBUG ONLY.
    
    Blocking function that displays solid and waits for user to close viewer.
    
    Args:
        solid: TopoDS_Solid to display
        title: Optional window title (currently unused, for API compatibility)
    """
    viewer = DebugViewer()
    viewer.display_solid(solid)
    viewer.display_and_wait()


def view_solid_non_blocking(solid: TopoDS_Solid, title: Optional[str] = None) -> DebugViewer:
    """Display a solid without blocking execution - DEBUG ONLY.
    
    Args:
        solid: TopoDS_Solid to display
        title: Optional window title (currently unused, for API compatibility)
        
    Returns:
        DebugViewer instance
    """
    viewer = DebugViewer()
    viewer.display_solid(solid)
    return viewer
