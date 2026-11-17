"""Opt in to crisp native Windows DPI rendering before Tk creates a root.

Only changes process display scaling; never changes Minecraft or model state.
"""
from __future__ import annotations
import sys


def enable_windows_dpi_awareness() -> bool:
    if sys.platform != 'win32':
        return False
    try:
        import ctypes
        user32 = ctypes.windll.user32
        # Windows 10+: make Tk and its canvas render at the monitor pixel density.
        try:
            if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
                return True
        except (AttributeError, OSError, ValueError):
            pass
        try:
            # Fallback: still crisp on the main monitor, unlike DPI virtualization.
            if ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0:
                return True
        except (AttributeError, OSError, ValueError):
            pass
        return bool(user32.SetProcessDPIAware())
    except (AttributeError, OSError, ValueError):
        # App display must never depend on optional platform DPI APIs.
        return False
