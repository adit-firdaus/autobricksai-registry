#!/usr/bin/env python3
"""
Build-time patch for Hermes' models.dev integration.

Appends a hook to /opt/hermes/.venv/lib/python3.11/site-packages/agent/models_dev.py
that:
  1. Maps our `abai` provider name to the locally-served `autobricksai` provider ID.
  2. Wraps fetch_models_dev() so it merges /opt/data/.hermes/autobricksai_provider.json
     into the returned registry on every call.

The hook keeps re-applying after Hermes' hourly background refresh, so the network
fetch overwriting _models_dev_cache doesn't strip our provider entry.

Idempotent: checks for the sentinel marker before appending. Safe on rebuilds.
"""
from __future__ import annotations

import sys
from pathlib import Path

MARKER_START = "# ── AUTOBRICKS:MODELS_DEV_INJECT:START ──"
MARKER_END   = "# ── AUTOBRICKS:MODELS_DEV_INJECT:END ──"

PATCH_BLOCK = f'''

{MARKER_START}
# Registers the locally-served `autobricksai` provider into models.dev lookups
# so Hermes' image-routing + capability checks work for our branded slugs.
# Data is read from /opt/data/.hermes/autobricksai_provider.json (rewritten by
# the AutoBricks platform on every bot provision + catalog change).

PROVIDER_TO_MODELS_DEV["abai"] = "autobricksai"

_AUTOBRICKSAI_PROVIDER_PATH = "/opt/data/.hermes/autobricksai_provider.json"
_orig_fetch_models_dev = fetch_models_dev


def fetch_models_dev(force_refresh: bool = False):  # type: ignore[no-redef]
    data = _orig_fetch_models_dev(force_refresh)
    try:
        import json as _json
        import os as _os
        if _os.path.exists(_AUTOBRICKSAI_PROVIDER_PATH):
            with open(_AUTOBRICKSAI_PROVIDER_PATH, "r", encoding="utf-8") as _f:
                entry = _json.load(_f)
            if isinstance(entry, dict) and isinstance(data, dict):
                data["autobricksai"] = entry
                _models_dev_cache["autobricksai"] = entry
    except Exception as exc:
        logger.debug("autobricksai inject failed: %s", exc)
    return data
{MARKER_END}
'''


def find_all_models_dev() -> list[Path]:
    """Locate every copy of agent/models_dev.py inside the Hermes install.

    There can be three: the venv site-packages copy, an editable repo-tree
    copy at /opt/hermes/agent/, and a build artifact at /opt/hermes/build/.
    When /opt/hermes is on sys.path ahead of the venv (which happens when
    hermes is launched with CWD=/opt/hermes), the repo-tree copy shadows
    the venv copy at import time — so patching only the venv silently fails.
    We patch every copy we find.
    """
    found: list[Path] = []
    seen: set[Path] = set()
    explicit = [
        Path("/opt/hermes/.venv/lib/python3.11/site-packages/agent/models_dev.py"),
        Path("/opt/hermes/agent/models_dev.py"),
        Path("/opt/hermes/build/lib/agent/models_dev.py"),
    ]
    for c in explicit:
        if c.is_file() and c.resolve() not in seen:
            seen.add(c.resolve())
            found.append(c)
    # Also handle other minor python versions transparently.
    for glob_root in ("/opt/hermes/.venv/lib", "/opt/hermes"):
        root = Path(glob_root)
        if not root.is_dir():
            continue
        for hit in root.rglob("agent/models_dev.py"):
            if hit.is_file() and hit.resolve() not in seen:
                seen.add(hit.resolve())
                found.append(hit)
    if not found:
        raise SystemExit("patch-hermes-models-dev: could not locate any agent/models_dev.py")
    return found


def patch_one(target: Path) -> str:
    text = target.read_text(encoding="utf-8")
    if MARKER_START in text:
        return f"already applied: {target}"
    new_text = text.rstrip() + PATCH_BLOCK
    target.write_text(new_text, encoding="utf-8")
    # Drop any stale .pyc for this file so the next import recompiles from source.
    cache = target.parent / "__pycache__"
    if cache.is_dir():
        for pyc in cache.glob(f"{target.stem}.cpython-*.pyc"):
            try:
                pyc.unlink()
            except OSError:
                pass
    return f"applied: {target}"


def main() -> int:
    targets = find_all_models_dev()
    for t in targets:
        print(f"patch-hermes-models-dev: {patch_one(t)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
