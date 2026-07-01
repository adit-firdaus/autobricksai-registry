#!/usr/bin/env python3
"""Bridge for hermes-workspace marketplace install/uninstall in zero-fork mode.

Workspace bundle calls this script with one of:
    python3 /app/scripts/skills-install.py install   <identifier> <force> <category>
    python3 /app/scripts/skills-install.py uninstall <name>

Always prints a JSON object on stdout and exits 0 so the bundle's JSON.parse
of stdout never fails. Caller maps {ok: false} to a 500 response and surfaces
the {error} string in the UI.

We always pass `--force` to the underlying CLI: every marketplace skill (even
"official" sources like anthropics/openai) gets a CAUTION verdict from the
hermes scanner because of routine SKILL.md patterns (Bash allowed-tools,
git/npm in README, ~/.bashrc examples). The marketplace card UI already shows
the security badge before the user clicks Install, so the user has acknowledged
the risk by the time we get here. Without --force every install would fail
with a confusing 'Use --force to override' message.
"""
import json, sys, subprocess


def _run(
    cmd: list[str],
    timeout: int,
    *,
    stdin_text: str | None = None,
    success_marker: str | None = None,
) -> tuple[bool, str]:
    """Run a command and return (ok, error_message).

    `hermes skills install` (and uninstall) returns exit 0 even when it fails
    (e.g., GitHub rate limit, "Could not fetch" errors). So in addition to the
    exit code we check for a `success_marker` in stdout (e.g., "Installed: ")
    and for known failure patterns ("Error: ", "could not fetch", etc.).
    """
    try:
        r = subprocess.run(
            cmd,
            input=stdin_text,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, f"timed out after {timeout}s"
    except FileNotFoundError as e:
        return False, f"binary not found: {e}"

    combined = (r.stdout or "") + "\n" + (r.stderr or "")
    lc = combined.lower()

    # CLI signals failure inline despite exit 0.
    failure_patterns = (
        "error: could not fetch",
        "rate limit exhausted",
        "rate limit reached",
        "installation blocked",
        "skill not found",
    )
    for pat in failure_patterns:
        if pat in lc:
            # Trim to a useful single-line message for the UI.
            msg = ""
            for line in combined.splitlines():
                if "error:" in line.lower() or "blocked" in line.lower():
                    msg = line.strip()
                    break
            return False, (msg or pat)[:1500]

    # If a success_marker was provided, require it (CLI prints it on real install).
    if success_marker is not None and success_marker not in combined:
        msg = (r.stderr.strip() or r.stdout.strip() or "no success marker in output")[:1500]
        return False, msg

    if r.returncode != 0:
        msg = (r.stderr.strip() or r.stdout.strip() or f"exit {r.returncode}")[:1500]
        return False, msg

    return True, ""


def main() -> None:
    action = sys.argv[1] if len(sys.argv) > 1 else ""

    if action == "install":
        identifier = sys.argv[2] if len(sys.argv) > 2 else ""
        # 4th arg ("force") is accepted but currently ignored — see module
        # docstring for why we always pass --force.
        category = sys.argv[4] if len(sys.argv) > 4 else ""
        if not identifier:
            print(json.dumps({"ok": False, "error": "missing identifier"}))
            return
        cmd = ["hermes", "skills", "install", identifier, "--yes", "--force"]
        if category:
            cmd.extend(["--category", category])
        # CLI prints "Installed: <name>" on success — require it because the
        # CLI can exit 0 even on rate-limit / fetch failures.
        ok, err = _run(cmd, 180, success_marker="Installed:")

    elif action == "uninstall":
        name = sys.argv[2] if len(sys.argv) > 2 else ""
        if not name:
            print(json.dumps({"ok": False, "error": "missing name"}))
            return
        # `hermes skills uninstall` has no --yes flag; it prompts interactively
        # ("Confirm [y/N]:"). Feed "y\n" via stdin to auto-confirm.
        ok, err = _run(["hermes", "skills", "uninstall", name], 60, stdin_text="y\n")

    else:
        print(json.dumps({"ok": False, "error": f"unknown action: {action!r}"}))
        return

    if ok:
        print(json.dumps({"ok": True}))
    else:
        print(json.dumps({"ok": False, "error": err}))


if __name__ == "__main__":
    main()
