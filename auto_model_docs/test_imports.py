#!/usr/bin/env python3
"""Quick diagnostic script to verify imports, paths, and Domino connectivity.

Run from any Domino context (workspace terminal, job, or locally):

    python /mnt/code/auto_model_docs/test_imports.py
"""

from __future__ import annotations

import ctypes
import importlib.util
import os
import sys
from pathlib import Path

PASS = "PASS"
FAIL = "FAIL"


def _banner(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")


def _result(label: str, ok: bool, detail: str = "") -> None:
    status = PASS if ok else FAIL
    suffix = f"  ({detail})" if detail else ""
    print(f"  [{status}] {label}{suffix}")


def check_paths() -> None:
    _banner("Paths")
    print(f"  __file__   = {__file__}")
    print(f"  cwd        = {os.getcwd()}")
    print(f"  sys.path[:5] =")
    for p in sys.path[:5]:
        print(f"    {p}")


def preload_conda_libstdcpp() -> str | None:
    """Preload conda's libstdc++ to fix CXXABI version mismatch in Domino Apps."""
    _banner("libstdc++ preload (CXXABI fix)")
    for candidate in (
        os.path.join(os.environ.get("CONDA_PREFIX", "/opt/conda"), "lib", "libstdc++.so.6"),
        "/opt/conda/lib/libstdc++.so.6",
    ):
        if os.path.isfile(candidate):
            try:
                ctypes.CDLL(candidate, mode=ctypes.RTLD_GLOBAL)
                _result("preload", True, candidate)
                return candidate
            except OSError as exc:
                _result("preload", False, f"{candidate}: {exc}")
        else:
            print(f"  (not found: {candidate})")
    _result("preload", False, "no conda libstdc++ found")
    return None


def check_sibling_import(name: str) -> bool:
    """Import a sibling module using the same importlib trick as web_app.py."""
    path = Path(__file__).resolve().parent / f"{name}.py"
    if not path.exists():
        _result(name, False, f"file not found: {path}")
        return False
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _result(name, True, f"loaded from {path}")
        return True
    except Exception as exc:
        _result(name, False, f"{type(exc).__name__}: {exc}")
        return False


def check_imports() -> dict[str, object]:
    _banner("Sibling module imports (importlib)")
    modules: dict[str, object] = {}
    for name in ("domino_client", "domino_job_store", "spec_store"):
        ok = check_sibling_import(name)
        if ok:
            path = Path(__file__).resolve().parent / f"{name}.py"
            spec = importlib.util.spec_from_file_location(name, path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            modules[name] = mod
    return modules


def check_domino_client(modules: dict[str, object]) -> None:
    _banner("Domino client smoke tests")
    dc = modules.get("domino_client")
    if dc is None:
        print("  (skipped — domino_client import failed)")
        return

    # list_branches
    try:
        branches = dc.list_branches()
        _result("list_branches()", True, f"{len(branches)} branch(es)")
        for b in branches[:5]:
            print(f"    - {b.get('name', b)}")
        if len(branches) > 5:
            print(f"    ... and {len(branches) - 5} more")
    except Exception as exc:
        _result("list_branches()", False, f"{type(exc).__name__}: {exc}")

    # list_hardware_tiers
    try:
        tiers = dc.list_hardware_tiers()
        _result("list_hardware_tiers()", True, f"{len(tiers)} tier(s)")
        for t in tiers[:5]:
            print(f"    - {t.get('name', t)}")
    except Exception as exc:
        _result("list_hardware_tiers()", False, f"{type(exc).__name__}: {exc}")


def check_env_vars() -> None:
    _banner("DOMINO_* environment variables")
    found = {k: v for k, v in sorted(os.environ.items()) if k.startswith("DOMINO_")}
    if not found:
        print("  (none set)")
    for k, v in found.items():
        # Mask API keys
        if "KEY" in k or "SECRET" in k or "TOKEN" in k:
            v = v[:4] + "..." + v[-4:] if len(v) > 8 else "***"
        print(f"  {k} = {v}")


def main() -> None:
    print("Auto Model Docs — Import Diagnostics")
    check_paths()
    preload_conda_libstdcpp()
    modules = check_imports()
    check_domino_client(modules)
    check_env_vars()

    _banner("Summary")
    total = 3  # domino_client, domino_job_store, spec_store
    loaded = len(modules)
    print(f"  Modules loaded: {loaded}/{total}")
    if loaded == total:
        print("  All imports OK")
    else:
        print("  Some imports FAILED — check output above")


if __name__ == "__main__":
    main()
