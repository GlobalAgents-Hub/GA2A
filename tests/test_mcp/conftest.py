"""Conftest for MCP tests - ensures correct import resolution."""

import sys
from pathlib import Path

# Ensure src/ directory takes precedence over root a2a.py for package imports.
# Remove the root a2a module from cache if it was imported from the root a2a.py.
_src_path = str(Path(__file__).resolve().parent.parent.parent / "src")
_root_path = str(Path(__file__).resolve().parent.parent.parent)

if _src_path not in sys.path:
    sys.path.insert(0, _src_path)

# Remove root directory from path if present (a2a.py shadows the package)
if _root_path in sys.path:
    sys.path.remove(_root_path)

# Clear cached a2a module if it points to the wrong file
if "a2a" in sys.modules:
    mod = sys.modules["a2a"]
    if hasattr(mod, "__file__") and mod.__file__ and "src" not in mod.__file__:
        del sys.modules["a2a"]
