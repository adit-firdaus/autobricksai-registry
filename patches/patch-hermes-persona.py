#!/usr/bin/env python3
"""
Build-time patch for the `/persona <slug>` slash command.

Adds a per-source ("per-chat") persona swap that's independent of upstream's
`/personality` command (which is global + reads from config.yaml). `/persona`:

  * pins (platform, chat_id) -> persona slug in /opt/data/source_persona_overrides.json
  * the persona body is loaded from /opt/data/agents/<slug>.md (the file the
    autobricks `/account/agents` install flow writes) — so any agent installed
    from the autobricks UI is immediately usable as a persona without any
    config.yaml mutation.
  * each turn, the gateway prepends the persona body to ``combined_ephemeral``
    in ``run_sync`` — the per-turn ephemeral system prompt path — so the
    model adopts that identity *every turn*. Earlier versions of this patch
    prepended to ``stable_parts`` in ``agent/system_prompt.py``, but
    ``build_system_prompt`` is cached per-session (only rebuilt on context
    compression), so the prepend ran once at session start (when no pin was
    set) and never re-read the override file. The ephemeral path naturally
    rebuilds every turn and is the load-bearing injection point now.

Six injection sites across three files. Each site uses an idempotent marker
pair so rebuilds are safe. (The old ``system_prompt.py`` prepend is kept for
backward-compat with already-deployed bots but is now functionally dead;
removal is harmless.)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# ──────────────────────────────────────────────────────────────────────────────
# Target file resolution (Python 3.11 path with 3.12/3.13 fallback)
# ──────────────────────────────────────────────────────────────────────────────

_VENV_LIB_CANDIDATES = [
    Path("/opt/hermes/.venv/lib/python3.11/site-packages"),
    Path("/opt/hermes/.venv/lib/python3.12/site-packages"),
    Path("/opt/hermes/.venv/lib/python3.13/site-packages"),
]


def _site_packages() -> Path:
    for c in _VENV_LIB_CANDIDATES:
        if c.is_dir():
            return c
    # Fallback: glob for whichever python3.X dir exists.
    for hit in Path("/opt/hermes/.venv/lib").glob("python3.*/site-packages"):
        if hit.is_dir():
            return hit
    raise SystemExit("patch-hermes-persona: could not locate hermes venv site-packages")


# ──────────────────────────────────────────────────────────────────────────────
# Idempotent patch helpers
# ──────────────────────────────────────────────────────────────────────────────


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def _drop_pyc(py_path: Path) -> None:
    cache = py_path.parent / "__pycache__"
    if cache.is_dir():
        for pyc in cache.glob(f"{py_path.stem}.cpython-*.pyc"):
            try:
                pyc.unlink()
            except OSError:
                pass


def _insert_once(text: str, marker: str, anchor: str, block: str, *, before: bool = False) -> tuple[str, str]:
    """Insert ``block`` near ``anchor`` if ``marker`` not present.

    Returns (new_text, status) where status is "applied" or "already-applied"
    or "anchor-not-found".
    """
    if marker in text:
        return text, "already-applied"
    idx = text.find(anchor)
    if idx < 0:
        return text, "anchor-not-found"
    if before:
        return text[:idx] + block + text[idx:], "applied"
    end = idx + len(anchor)
    # Insert right after the line containing the anchor.
    line_end = text.find("\n", end)
    if line_end < 0:
        line_end = len(text)
    return text[: line_end + 1] + block + text[line_end + 1 :], "applied"


# ──────────────────────────────────────────────────────────────────────────────
# Patch 1: hermes_cli/commands.py — register the /persona CommandDef
# ──────────────────────────────────────────────────────────────────────────────

CMD_MARKER_START = "# ── AUTOBRICKS:PERSONA-COMMANDDEF:START ──"
CMD_MARKER_END = "# ── AUTOBRICKS:PERSONA-COMMANDDEF:END ──"

CMD_ANCHOR = (
    'CommandDef("personality", "Set a predefined personality", "Configuration",'
)
CMD_BLOCK = f"""
    {CMD_MARKER_START}
    CommandDef("persona", "Set the per-chat specialist persona (autobricks)",
               "Configuration", args_hint="[slug|default]"),
    {CMD_MARKER_END}
"""


def patch_commands(site: Path) -> None:
    path = site / "hermes_cli" / "commands.py"
    if not path.is_file():
        print(f"  skip (no file): {path}")
        return
    text = _read(path)
    if CMD_MARKER_START in text:
        print(f"  already applied: {path.name} (CommandDef)")
        return
    # Find the END of the personality CommandDef tuple — it spans two lines:
    #   CommandDef("personality", "Set a predefined personality", "Configuration",
    #              args_hint="[name]"),
    m = re.search(
        r'CommandDef\("personality",\s*"[^"]+",\s*"Configuration",\s*\n'
        r'\s*args_hint="\[name\]"\),',
        text,
    )
    if not m:
        print(f"  ANCHOR NOT FOUND for personality CommandDef in {path.name}")
        return
    insert_at = m.end()
    new_text = text[: insert_at] + "\n" + CMD_BLOCK.rstrip("\n") + text[insert_at:]
    _write(path, new_text)
    _drop_pyc(path)
    print(f"  applied: {path.name} (CommandDef)")


# ──────────────────────────────────────────────────────────────────────────────
# Patch 2: gateway/run.py — contextvar definition + .set() call
# ──────────────────────────────────────────────────────────────────────────────

CTX_MARKER_START = "# ── AUTOBRICKS:PERSONA-CONTEXT:START ──"
CTX_MARKER_END = "# ── AUTOBRICKS:PERSONA-CONTEXT:END ──"

CTX_DEF_BLOCK = f"""
{CTX_MARKER_START}
import contextvars as _abai_cv  # noqa: E402
abai_current_source_key: _abai_cv.ContextVar = _abai_cv.ContextVar(
    "abai_current_source_key", default=None
)
{CTX_MARKER_END}
"""

# Anchor for the contextvar definition: append after the early stdlib import
# block. `import sqlite3` is the last stdlib import on the current upstream
# (around line ~38) and is unlikely to disappear / move dramatically.
CTX_DEF_ANCHOR_RE = re.compile(r"^import\s+sqlite3\s*$", re.MULTILINE)

# Anchor for the .set() call: we insert at the TOP of _handle_message — the
# per-incoming-message entry point — so the contextvar is set for the entire
# turn (including system_prompt build).
CTX_SET_ANCHOR = "async def _handle_message(self, event: MessageEvent)"
CTX_SET_MARKER_START = "# ── AUTOBRICKS:PERSONA-CONTEXT-SET:START ──"
CTX_SET_MARKER_END = "# ── AUTOBRICKS:PERSONA-CONTEXT-SET:END ──"


def patch_gateway_context(text: str) -> tuple[str, list[str]]:
    """Apply the two contextvar patches to gateway/run.py text."""
    log: list[str] = []

    # 2a. The ContextVar module-level definition.
    if CTX_MARKER_START in text:
        log.append("  already applied: gateway/run.py (context-def)")
    else:
        m = CTX_DEF_ANCHOR_RE.search(text)
        if not m:
            log.append("  ANCHOR NOT FOUND: gateway/run.py (context-def)")
        else:
            # Insert at end of line containing the anchor
            line_end = text.find("\n", m.end())
            if line_end < 0:
                line_end = len(text)
            text = text[: line_end + 1] + CTX_DEF_BLOCK + text[line_end + 1 :]
            log.append("  applied: gateway/run.py (context-def)")

    # 2b. The .set() call at the top of _handle_message body. The anchor is
    # the function signature; we insert the block right at the start of the
    # function body (just after any docstring).
    if CTX_SET_MARKER_START in text:
        log.append("  already applied: gateway/run.py (context-set)")
    else:
        idx = text.find(CTX_SET_ANCHOR)
        if idx < 0:
            log.append("  ANCHOR NOT FOUND: gateway/run.py (context-set)")
        else:
            # Find the end of the signature line (the `:` at end).
            sig_line_end = text.find("\n", idx)
            if sig_line_end < 0:
                log.append("  ANCHOR NOT FOUND: gateway/run.py (context-set, sig end)")
                return text, log
            # Skip a docstring if present. Look at the next non-blank line.
            cursor = sig_line_end + 1
            # Detect the body indent (4 spaces deeper than the `async def` line).
            sig_line_start = text.rfind("\n", 0, idx) + 1
            sig_indent = re.match(r"[ \t]*", text[sig_line_start:idx]).group(0)
            body_indent = sig_indent + "    "
            # Skip blank lines + docstring lines if present.
            while cursor < len(text):
                next_eol = text.find("\n", cursor)
                if next_eol < 0:
                    next_eol = len(text)
                line = text[cursor:next_eol]
                stripped = line.strip()
                if stripped.startswith('"""') or stripped.startswith("'''"):
                    # docstring start; find its end on same or later line
                    quote = stripped[:3]
                    rest = stripped[3:]
                    if rest.endswith(quote) and len(rest) >= 3:
                        # single-line docstring
                        cursor = next_eol + 1
                        break
                    # multi-line: scan forward to the closing quote
                    scan = next_eol + 1
                    while scan < len(text):
                        end_line = text.find("\n", scan)
                        if end_line < 0:
                            end_line = len(text)
                        if quote in text[scan:end_line]:
                            cursor = end_line + 1
                            break
                        scan = end_line + 1
                    else:
                        cursor = len(text)
                    break
                elif stripped == "":
                    cursor = next_eol + 1
                    continue
                else:
                    # First real statement; insert above it
                    break
            insert_at = cursor
            inserted = (
                f"{body_indent}{CTX_SET_MARKER_START}\n"
                f"{body_indent}try:\n"
                f"{body_indent}    if event.source.platform and event.source.chat_id:\n"
                f"{body_indent}        abai_current_source_key.set(\n"
                f"{body_indent}            f\"{{event.source.platform.value}}:{{event.source.chat_id}}\"\n"
                f"{body_indent}        )\n"
                f"{body_indent}except Exception:\n"
                f"{body_indent}    pass\n"
                f"{body_indent}{CTX_SET_MARKER_END}\n"
            )
            text = text[:insert_at] + inserted + text[insert_at:]
            log.append("  applied: gateway/run.py (context-set)")

    return text, log


# ──────────────────────────────────────────────────────────────────────────────
# Patch 3: gateway/run.py — _handle_persona_command method + dispatch branch
# ──────────────────────────────────────────────────────────────────────────────

CMDHANDLER_MARKER_START = "# ── AUTOBRICKS:PERSONA-COMMAND:START ──"
CMDHANDLER_MARKER_END = "# ── AUTOBRICKS:PERSONA-COMMAND:END ──"

# The method block — inserted right BEFORE the existing _handle_personality_command.
PERSONA_METHOD_BLOCK = f'''
    {CMDHANDLER_MARKER_START}
    async def _handle_persona_command(self, event):
        """Handle /persona — show, set, or clear the per-source specialist persona.

        Storage: /opt/data/source_persona_overrides.json, keyed by
        "<platform>:<chat_id>". Persona body comes from /opt/data/agents/<slug>.md
        (the file the autobricks /account/agents install flow writes).
        """
        import json
        import os
        import tempfile
        store_path = "/opt/data/source_persona_overrides.json"
        agents_dir = "/opt/data/agents"

        arg = (event.get_command_args() or "").strip().lower()
        try:
            src_key = (
                f"{{event.source.platform.value}}:{{event.source.chat_id}}"
            )
        except Exception:
            return "Could not identify this chat source — /persona unavailable here."

        store = {{}}
        if os.path.exists(store_path):
            try:
                with open(store_path) as _f:
                    store = json.load(_f) or {{}}
            except Exception:
                store = {{}}

        def _write_store(s):
            d = os.path.dirname(store_path) or "."
            fd, tmp = tempfile.mkstemp(dir=d, prefix=".persona.", suffix=".json.tmp")
            try:
                with os.fdopen(fd, "w") as _fw:
                    json.dump(s, _fw, indent=2)
                os.replace(tmp, store_path)
                try:
                    os.chmod(store_path, 0o600)
                except Exception:
                    pass
            except Exception:
                try:
                    os.unlink(tmp)
                except Exception:
                    pass
                raise

        if not arg:
            cur = store.get(src_key, "default")
            try:
                _av = sorted(
                    p[:-3] for p in os.listdir(agents_dir) if p.endswith(".md")
                )
            except FileNotFoundError:
                _av = []
            if _av:
                _av_str = ", ".join(_av)
            else:
                _av_str = "none installed — install some at /account/agents"
            return (
                f"Active persona in this chat: {{cur}}.\\n"
                f"Available: {{_av_str}}.\\n"
                "Use /persona <slug> to switch, /persona default to clear."
            )

        if arg in {{"default", "clear", "none", "off"}}:
            if src_key in store:
                store.pop(src_key, None)
                try:
                    _write_store(store)
                except Exception as exc:
                    return f"Failed to clear persona: {{exc}}"
            return "Persona cleared — bot will respond as default."

        md_path = os.path.join(agents_dir, f"{{arg}}.md")
        if not os.path.exists(md_path):
            try:
                available = sorted(
                    p[:-3] for p in os.listdir(agents_dir) if p.endswith(".md")
                )
            except FileNotFoundError:
                available = []
            if available:
                avail_str = ", ".join(available)
            else:
                avail_str = "none installed — install some at /account/agents"
            return f"Unknown persona '{{arg}}'. Available: {{avail_str}}"

        store[src_key] = arg
        try:
            _write_store(store)
        except Exception as exc:
            return f"Failed to save persona: {{exc}}"
        return f"Now responding as {{arg}} in this chat."
    {CMDHANDLER_MARKER_END}
'''


CMDHANDLER_METHOD_ANCHOR = "async def _handle_personality_command"

# Dispatch branch — inserted right BEFORE the personality dispatch line.
CMDHANDLER_DISPATCH_MARKER_START = "# ── AUTOBRICKS:PERSONA-DISPATCH:START ──"
CMDHANDLER_DISPATCH_MARKER_END = "# ── AUTOBRICKS:PERSONA-DISPATCH:END ──"

CMDHANDLER_DISPATCH_ANCHOR_RE = re.compile(
    r'^([ \t]*)if canonical == "personality":\s*\n[ \t]*return await self\._handle_personality_command\(event\)\s*\n',
    re.MULTILINE,
)


def patch_gateway_persona_command(text: str) -> tuple[str, list[str]]:
    log: list[str] = []

    # 3a. The method body.
    if CMDHANDLER_MARKER_START in text:
        log.append("  already applied: gateway/run.py (handler method)")
    else:
        idx = text.find(CMDHANDLER_METHOD_ANCHOR)
        if idx < 0:
            log.append("  ANCHOR NOT FOUND: gateway/run.py (handler method)")
        else:
            line_start = text.rfind("\n", 0, idx) + 1
            text = text[:line_start] + PERSONA_METHOD_BLOCK.lstrip("\n") + "\n" + text[line_start:]
            log.append("  applied: gateway/run.py (handler method)")

    # 3b. The dispatch branch. The dispatcher uses `if canonical == "X":` —
    # we insert a sibling block for "persona" right before the personality one.
    if CMDHANDLER_DISPATCH_MARKER_START in text:
        log.append("  already applied: gateway/run.py (handler dispatch)")
    else:
        m = CMDHANDLER_DISPATCH_ANCHOR_RE.search(text)
        if not m:
            log.append("  ANCHOR NOT FOUND: gateway/run.py (handler dispatch)")
        else:
            indent = m.group(1)
            block = (
                f"{indent}{CMDHANDLER_DISPATCH_MARKER_START}\n"
                f'{indent}if canonical == "persona":\n'
                f"{indent}    return await self._handle_persona_command(event)\n"
                f"\n"
                f"{indent}{CMDHANDLER_DISPATCH_MARKER_END}\n"
            )
            text = text[:m.start()] + block + text[m.start():]
            log.append("  applied: gateway/run.py (handler dispatch)")

    return text, log


# ──────────────────────────────────────────────────────────────────────────────
# Patch 4: gateway/run.py — per-turn ephemeral persona injection in run_sync
# ──────────────────────────────────────────────────────────────────────────────
#
# This is THE load-bearing patch. ``run_sync`` constructs ``combined_ephemeral``
# fresh every turn (context_prompt + channel_prompt + _ephemeral_system_prompt),
# and that string is added to the agent's system message at run time. By
# prepending the pinned persona's MD body here we:
#   * pick up the JSON override file on every message (no caching trap),
#   * keep scoping per-source (we key off ``source.platform``/``source.chat_id``),
#   * leave the cached stable system prompt untouched (prefix cache stays warm).
#
# Anchor: the unique line ``combined_ephemeral = context_prompt or ""`` —
# we insert immediately after.

EPHEMERAL_MARKER_START = "# ── AUTOBRICKS:PERSONA-EPHEMERAL:START ──"
EPHEMERAL_MARKER_END = "# ── AUTOBRICKS:PERSONA-EPHEMERAL:END ──"

EPHEMERAL_ANCHOR_RE = re.compile(
    r'^([ \t]*)combined_ephemeral\s*=\s*context_prompt\s+or\s+""\s*\n',
    re.MULTILINE,
)


def patch_gateway_ephemeral(text: str) -> tuple[str, list[str]]:
    log: list[str] = []
    if EPHEMERAL_MARKER_START in text:
        log.append("  already applied: gateway/run.py (ephemeral)")
        return text, log
    m = EPHEMERAL_ANCHOR_RE.search(text)
    if not m:
        log.append("  ANCHOR NOT FOUND: gateway/run.py (ephemeral)")
        return text, log
    indent = m.group(1)
    insert_at = m.end()
    block = (
        f"{indent}{EPHEMERAL_MARKER_START}\n"
        f"{indent}try:\n"
        f"{indent}    import json as _abai_json\n"
        f"{indent}    import os as _abai_os\n"
        f"{indent}    if source.platform and source.chat_id:\n"
        f'{indent}        _abai_key = f"{{source.platform.value}}:{{source.chat_id}}"\n'
        f'{indent}        _abai_store_path = "/opt/data/source_persona_overrides.json"\n'
        f"{indent}        _abai_pin = None\n"
        f"{indent}        if _abai_os.path.exists(_abai_store_path):\n"
        f"{indent}            try:\n"
        f"{indent}                with open(_abai_store_path) as _abai_f:\n"
        f"{indent}                    _abai_pin = (_abai_json.load(_abai_f) or {{}}).get(_abai_key)\n"
        f"{indent}            except Exception:\n"
        f"{indent}                _abai_pin = None\n"
        f"{indent}        if _abai_pin:\n"
        f'{indent}            _abai_md = f"/opt/data/agents/{{_abai_pin}}.md"\n'
        f"{indent}            if _abai_os.path.exists(_abai_md):\n"
        f"{indent}                with open(_abai_md) as _abai_f:\n"
        f"{indent}                    _abai_body = _abai_f.read()\n"
        f'{indent}                if _abai_body.startswith("---"):\n'
        f'{indent}                    _abai_end = _abai_body.find("---", 3)\n'
        f"{indent}                    if _abai_end != -1:\n"
        f"{indent}                        _abai_body = _abai_body[_abai_end + 3:].lstrip()\n"
        f"{indent}                combined_ephemeral = (\n"
        f'{indent}                    (combined_ephemeral + "\\n\\n" + _abai_body).strip()\n'
        f"{indent}                    if combined_ephemeral else _abai_body\n"
        f"{indent}                )\n"
        f"{indent}except Exception:\n"
        f"{indent}    pass\n"
        f"{indent}{EPHEMERAL_MARKER_END}\n"
    )
    text = text[:insert_at] + block + text[insert_at:]
    log.append("  applied: gateway/run.py (ephemeral)")
    return text, log


# ──────────────────────────────────────────────────────────────────────────────
# Patch 5: agent/system_prompt.py — prepend persona body when pin is set
# ──────────────────────────────────────────────────────────────────────────────
#
# NOTE: kept for backward-compat with already-deployed bots that have this
# marker. Functionally DEAD because build_system_prompt is cached per-session,
# so the JSON override file is read once (at session start, before any pin
# exists) and never again. Patch 4 (ephemeral) is the real path.

SYSPROMPT_MARKER_START = "# ── AUTOBRICKS:PERSONA-SYSPROMPT:START ──"
SYSPROMPT_MARKER_END = "# ── AUTOBRICKS:PERSONA-SYSPROMPT:END ──"

# Anchor: the existing "from agent.file_safety import _resolve_active_profile_name"
# inside the try block that drives the Active Hermes profile branch.
SYSPROMPT_ANCHOR = "from agent.file_safety import _resolve_active_profile_name"


def patch_system_prompt(site: Path) -> None:
    path = site / "agent" / "system_prompt.py"
    if not path.is_file():
        print(f"  skip (no file): {path}")
        return
    text = _read(path)
    if SYSPROMPT_MARKER_START in text:
        print(f"  already applied: {path.relative_to(site)}")
        return
    idx = text.find(SYSPROMPT_ANCHOR)
    if idx < 0:
        print(f"  ANCHOR NOT FOUND: {path.relative_to(site)}")
        return
    # Find the start of the OUTER try block (search backwards for `    try:`
    # with the same indentation as the line containing the anchor).
    line_start = text.rfind("\n", 0, idx) + 1
    indent_match = re.match(r"[ \t]*", text[line_start:idx])
    inner_indent = indent_match.group(0) if indent_match else "        "
    # Outer indent (the indent of the `try:` statement) is two levels above
    # the anchor — drop 4 spaces.
    outer_indent = inner_indent[:-4] if len(inner_indent) >= 4 else ""
    # Find the line containing the outer `try:` — walk back to find it.
    cur = line_start
    while cur > 0:
        prev_line_end = cur - 1
        prev_line_start = text.rfind("\n", 0, prev_line_end) + 1
        prev_line = text[prev_line_start:prev_line_end]
        if prev_line.strip() == "try:" and prev_line.startswith(outer_indent + "try"):
            outer_try_line_start = prev_line_start
            break
        cur = prev_line_start
    else:
        print(f"  ANCHOR NOT FOUND: {path.relative_to(site)} (outer try)")
        return

    block = (
        f"{outer_indent}{SYSPROMPT_MARKER_START}\n"
        f"{outer_indent}try:\n"
        f"{outer_indent}    import json as _abai_json\n"
        f"{outer_indent}    import os as _abai_os\n"
        f"{outer_indent}    try:\n"
        f"{outer_indent}        from gateway.run import abai_current_source_key as _abai_cv\n"
        f"{outer_indent}    except Exception:\n"
        f"{outer_indent}        _abai_cv = None\n"
        f"{outer_indent}    _abai_src = _abai_cv.get(None) if _abai_cv is not None else None\n"
        f'{outer_indent}    _abai_store_path = "/opt/data/source_persona_overrides.json"\n'
        f"{outer_indent}    if _abai_src and _abai_os.path.exists(_abai_store_path):\n"
        f"{outer_indent}        with open(_abai_store_path) as _abai_f:\n"
        f"{outer_indent}            _abai_store = _abai_json.load(_abai_f) or {{}}\n"
        f"{outer_indent}        _abai_pin = _abai_store.get(_abai_src)\n"
        f"{outer_indent}        if _abai_pin:\n"
        f'{outer_indent}            _abai_md = f"/opt/data/agents/{{_abai_pin}}.md"\n'
        f"{outer_indent}            if _abai_os.path.exists(_abai_md):\n"
        f"{outer_indent}                with open(_abai_md) as _abai_f:\n"
        f"{outer_indent}                    _abai_body = _abai_f.read()\n"
        f'{outer_indent}                if _abai_body.startswith("---"):\n'
        f'{outer_indent}                    _abai_end = _abai_body.find("---", 3)\n'
        f"{outer_indent}                    if _abai_end != -1:\n"
        f"{outer_indent}                        _abai_body = _abai_body[_abai_end + 3:].lstrip()\n"
        f"{outer_indent}                stable_parts.insert(0, _abai_body)\n"
        f"{outer_indent}except Exception:\n"
        f"{outer_indent}    pass\n"
        f"{outer_indent}{SYSPROMPT_MARKER_END}\n"
    )
    text = text[:outer_try_line_start] + block + text[outer_try_line_start:]
    _write(path, text)
    _drop_pyc(path)
    print(f"  applied: {path.relative_to(site)}")


# ──────────────────────────────────────────────────────────────────────────────
# main
# ──────────────────────────────────────────────────────────────────────────────


def main() -> int:
    site = _site_packages()
    print(f"patch-hermes-persona: site-packages = {site}")

    # 1. commands.py
    patch_commands(site)

    # 2 + 3. gateway/run.py — three sub-patches into the same file.
    run_path = site / "gateway" / "run.py"
    if not run_path.is_file():
        print(f"  skip (no file): {run_path}")
    else:
        text = _read(run_path)
        text, log = patch_gateway_context(text)
        for line in log:
            print(line)
        text, log = patch_gateway_persona_command(text)
        for line in log:
            print(line)
        text, log = patch_gateway_ephemeral(text)
        for line in log:
            print(line)
        _write(run_path, text)
        _drop_pyc(run_path)

    # 4. agent/system_prompt.py
    patch_system_prompt(site)

    print("patch-hermes-persona: done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
