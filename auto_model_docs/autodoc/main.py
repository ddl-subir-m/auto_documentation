"""Shim so ``python -m autodoc.main`` resolves to the top-level CLI entry point.

When the wheel is installed, ``main.py`` lands in site-packages as a
standalone module alongside the ``autodoc`` package.  This file makes
``autodoc.main`` an alias for it so Portal's job command works without
requiring the caller to know the package layout.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure the package root (parent of autodoc/) is on sys.path so the
# top-level main module and its siblings (artifact_layout, dataset_store, …)
# are importable when invoked as ``python -m autodoc.main``.
_pkg_root = str(Path(__file__).resolve().parent.parent)
if _pkg_root not in sys.path:
    sys.path.insert(0, _pkg_root)

from main import main  # noqa: E402 — path manipulation must come first

if __name__ == "__main__":
    main(standalone_mode=True)
