"""FastAPI backend package introduced through verified vertical slices."""

from __future__ import annotations

import os


_SQLITE_DLL_HANDLE = None
if (
    os.name == "nt"
    and hasattr(os, "add_dll_directory")
    and os.environ.get("P3_SQLITE_DLL_DIR")
):
    _SQLITE_DLL_HANDLE = os.add_dll_directory(os.environ["P3_SQLITE_DLL_DIR"])
