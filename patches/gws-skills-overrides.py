"""In-place patcher for SKILL.md files bundled in our autobot images.

Three operations, all idempotent:

    --pre-auth <path>
        For the NousResearch productivity/google-workspace SKILL.md (hermes
        image only). Rewrites the upstream `## First-Time Setup` section with
        an AutoBricks pre-auth note (credentials are auto-populated; the agent
        only runs `setup.py --check` and `--revoke`), and appends a
        `## Common Workflows` section with verified-working `gws` CLI examples
        for the most-used operations the upstream `## Usage` section doesn't
        cover well. This is the consolidated patch hermes uses now that we no
        longer bundle the upstream `gws-*` SKILL.md files.

    --auth <path>
        For `gws-shared/SKILL.md` in the upstream googleworkspace/cli bundle
        (openclaw image only - openclaw still pre-bundles those skills as a
        staging dir for `install_gws_skills()`). Replaces the upstream
        `## Authentication` section with an AutoBricks-AI note that tells the
        bot NOT to run `gws auth login`.

    --workflows <skill-id> <path>
        For `gws-drive`, `gws-sheets`, `gws-gmail` SKILL.md in the openclaw
        bundle. Inserts a `## Common Workflows` section right before the
        upstream `## Discovering Commands` heading with verified `gws`
        examples (share, overwrite, modify, etc.).

All operations are no-ops if their markers are already present, so the
Dockerfile RUN can re-run on incremental rebuilds without double-injecting.
"""
from __future__ import annotations

import pathlib
import re
import sys


# ----------------------------------------------------------------------------
# Pre-auth section (replaces upstream `## First-Time Setup`)
# ----------------------------------------------------------------------------
_PRE_AUTH_MARKER = "<!-- AUTOBRICKS:PRE-AUTH:v4 -->"

_PRE_AUTH_BLOCK = (
    "## First-Time Setup\n\n"
    f"{_PRE_AUTH_MARKER}\n\n"
    "**Already authenticated.** AutoBricks AI managed bots receive Google\n"
    "Workspace credentials automatically when the user clicks Connect Google\n"
    "Workspace in the dashboard. The platform writes\n"
    "`/opt/data/google_token.json` + `/opt/data/google_client_secret.json`\n"
    "(`HERMES_HOME` is `/opt/data`) plus `~/.config/gws/credentials.json` for\n"
    "the `gws` CLI execution backend.\n\n"
    "Skip the upstream multi-step OAuth walkthrough. The agent ONLY uses:\n\n"
    "### Hard rules for `gws` syntax (read FIRST, before any gws call)\n\n"
    "Every `gws` call has exactly this shape:\n\n"
    "```\n"
    "gws <service> <resource> <method> [--params '{...}'] [--json '{...}']\n"
    "```\n\n"
    "- `--params '{...}'` carries **path + query** fields (resource IDs and\n"
    "  query options) as a JSON object.\n"
    "- `--json '{...}'` carries the **request body** for write operations as\n"
    "  a JSON object.\n"
    "- These are the ONLY two flags that take resource fields. **Top-level\n"
    "  flags like `--q`, `--max`, `--corpus`, `--orderBy`, `--mime-type` do\n"
    "  not exist** — they are training-time hallucinations from native\n"
    "  Google API conventions. Every one belongs INSIDE the `--params` JSON.\n\n"
    "**Forbidden top-level flags → correct form:**\n\n"
    "| WRONG | RIGHT |\n"
    "|-------|-------|\n"
    "| `--q \"name contains foo\"` | `--params '{\"q\":\"name contains \\\"foo\\\"\"}'` |\n"
    "| `--max 10` / `--maxResults 10` | `--params '{\"maxResults\":10}'` |\n"
    "| `--pageSize 10` | `--params '{\"pageSize\":10}'` |\n"
    "| `--corpus user` | `--params '{\"corpus\":\"user\"}'` |\n"
    "| `--orderBy modifiedTime` | `--params '{\"orderBy\":\"modifiedTime desc\"}'` |\n"
    "| `--fields id,name` | `--params '{\"fields\":\"id,name\"}'` |\n"
    "| `--mime-type ...` | inside the q string: `--params '{\"q\":\"mimeType=\\\"application/...\\\"\"}'` |\n\n"
    "**Common subcommand confusions** — when a `list` call returns\n"
    "`unrecognized subcommand`, the resource collection name is missing:\n\n"
    "| WRONG | RIGHT |\n"
    "|-------|-------|\n"
    "| `gws calendar list` | `gws calendar calendarList list` |\n"
    "| `gws files list` | `gws drive files list` (must include `<service>`) |\n"
    "| `gws gmail list` | `gws gmail users messages list` |\n\n"
    "If unsure for any method, **run `gws schema <service>.<resource>.<method>`\n"
    "FIRST** (e.g. `gws schema drive.files.list`). It prints the exact param +\n"
    "body fields with their `location: path|query|body` so you know which\n"
    "flag each goes in. Cheaper than guessing through three failed attempts.\n\n"
    "### Use these absolute-path shortcuts (copy literally)\n\n"
    "```bash\n"
    "GSETUP=\"/opt/hermes/.venv/bin/python /opt/hermes/skills/productivity/google-workspace/scripts/setup.py\"\n"
    "GAPI=\"/opt/hermes/.venv/bin/python /opt/hermes/skills/productivity/google-workspace/scripts/google_api.py\"\n"
    "```\n\n"
    "**Use these shortcuts verbatim.** Do NOT substitute `python` for the\n"
    "venv path (the system has no `python` binary; the venv's binary is the\n"
    "only one with the google-api-python-client deps installed). Do NOT\n"
    "substitute `${HERMES_HOME}` or `${HOME}` for anything; the absolute\n"
    "paths above always work regardless of working directory or workspace\n"
    "context.\n\n"
    "### Verify auth at the start of any Google task\n\n"
    "```bash\n"
    "$GSETUP --check\n"
    "# prints AUTHENTICATED on success\n"
    "```\n\n"
    "If anything other than `AUTHENTICATED`, tell the user *\"Google Workspace\n"
    "credentials need refreshing - please click Reconnect in the AutoBricks\n"
    "dashboard.\"* Then stop. Do not loop, do not attempt manual OAuth, do not\n"
    "run `--client-secret`, `--auth-url`, or `--auth-code`.\n\n"
    "### Run any Google operation\n\n"
    "Use `$GAPI <service> <command> ...` (the shorthand above). For example:\n"
    "`$GAPI gmail search \"is:unread\" --max 10`,\n"
    "`$GAPI calendar list`,\n"
    "`$GAPI drive search \"finance\" --max 10`,\n"
    "`$GAPI sheets get SHEET_ID \"Sheet1!A1:D10\"`. The full command surface\n"
    "is documented in the `## Usage` section below.\n\n"
    "### Scope limits — read this BEFORE running Gmail or Drive operations\n\n"
    "The OAuth grant AutoBricks issues is deliberately narrow:\n\n"
    "- `gmail.send` — **send only**. Cannot read the user's inbox, cannot\n"
    "  search threads, cannot save drafts to the user's Drafts folder.\n"
    "- `calendar` — full read/write on the user's calendars and events.\n"
    "- `drive.file` — read/write **only on files the bot created itself**, or\n"
    "  files the user explicitly opened via a Google Picker dialog. Cannot\n"
    "  browse, search, or open files from the user's wider Drive.\n"
    "- `contacts`, `spreadsheets`, `documents` — full access on the\n"
    "  corresponding APIs.\n\n"
    "Translation for the agent: if the user asks something that needs a scope\n"
    "you don't have, say so up front rather than burning calls hunting for it.\n\n"
    "**Gmail inbox reading / search** (e.g. `gws gmail users messages list`,\n"
    "`gws gmail search`, `$GAPI gmail get`): the API will return\n"
    "`insufficient authentication scopes` (HTTP 403). Don't retry. Tell the\n"
    "user: *\"I can send emails on your behalf but I can't read your inbox -\n"
    "AutoBricks deliberately limits this for privacy. Open Gmail directly to\n"
    "find the message you want, then tell me what to reply.\"*\n\n"
    "**Drive browse / search across user's existing files** (e.g.\n"
    "`gws drive files list`, `$GAPI drive search`): will return\n"
    "`insufficient authentication scopes` for files the bot did not create.\n"
    "Tell the user: *\"I can only see Drive files I've created for you - I\n"
    "can't browse your wider Drive. Paste the file's link or share it with me\n"
    "directly and I can work with it.\"*\n\n"
    "**Legacy bots** provisioned before the scope narrowing keep their\n"
    "original broader grant (`gmail.modify` + full `drive`) and won't hit\n"
    "these errors. If the same operation works for some users and 403s for\n"
    "others, that's why - never assume a scope error means a bug.\n\n"
    "### Revoke (only when the user explicitly asks)\n\n"
    "```bash\n"
    "$GSETUP --revoke\n"
    "```\n\n"
    "(Revoking from inside the bot is rarely the right call - prefer the\n"
    "dashboard's Disconnect button so platform-side state stays in sync.)\n\n"
    "### Do NOT run\n\n"
    "- `setup.py --client-secret <path>` - bot has no client_secret to upload.\n"
    "- `setup.py --auth-url` - the user does not need an auth URL.\n"
    "- `setup.py --auth-code <code>` - same, the platform exchanges codes\n"
    "  server-side.\n\n"
)


# ----------------------------------------------------------------------------
# Common Workflows section (appended at end-of-file)
# ----------------------------------------------------------------------------
_WORKFLOWS_MARKER = "<!-- AUTOBRICKS:COMMON-WORKFLOWS -->"

_WORKFLOWS_BLOCK = (
    "## Common Workflows\n\n"
    f"{_WORKFLOWS_MARKER}\n\n"
    "Below are verified-working `gws` CLI commands for operations the\n"
    "upstream `## Usage` section doesn't cover well, or that the model tends\n"
    "to get wrong on the first try. Resource IDs and query options go in\n"
    "`--params`; request body fields go in `--json`. Run\n"
    "`gws schema <resource>.<method>` to see which is which for a method not\n"
    "listed here.\n\n"
    "### Important\n\n"
    "- **Do NOT fall back to `curl` against googleapis.com** if a `gws`\n"
    "  command rejects a flag - read the schema and retry. Raw curl needs an\n"
    "  access token we don't expose, and `gcloud` is not installed.\n"
    "- **`--json` is for POST request bodies, not output formatting.** Use\n"
    "  `--format json|table|yaml|csv` if you need to change output (default\n"
    "  is JSON).\n"
    "- **Read the error string before assuming auth failure.** `unauthorized`\n"
    "  / `credentials` / `token expired` -> auth issue (tell user to\n"
    "  Reconnect). `unexpected argument` / `missing required parameter` /\n"
    "  `validation` -> usage issue, fix and retry.\n\n"
    "### Sheets: create a new spreadsheet\n\n"
    "**The CLI does NOT accept `--title`** as a top-level flag. The title\n"
    "goes inside the `--json` request body under `properties.title`:\n\n"
    "```bash\n"
    "gws sheets spreadsheets create \\\n"
    "  --json '{\"properties\":{\"title\":\"Q4 Budget\"},\"sheets\":[{\"properties\":{\"title\":\"Stock\"}}]}'\n"
    "```\n"
    "Returns the full spreadsheet object including `spreadsheetId` and\n"
    "`spreadsheetUrl`. To share it with a user immediately after creation,\n"
    "chain a `gws drive permissions create` call against the new\n"
    "`spreadsheetId`. **DO NOT** try `gws sheets create --title \"...\"` -\n"
    "that returns `unexpected argument '--title'`, which means usage error,\n"
    "NOT a permission/auth problem. Fix the syntax and retry.\n\n"
    "### Gmail: send an email (DEFAULT TO --html, optional attachment)\n\n"
    "**Always use `--html` mode for emails with more than one paragraph.**\n"
    "Bash double-quote `\\n` is NOT a newline (it's literal backslash-n), so\n"
    "plain-text bodies usually arrive as a single unreadable run-on line.\n"
    "HTML mode side-steps the whole quoting trap - use `<p>` for\n"
    "paragraphs, `<br>` for soft line breaks, `<a href=\"...\">link</a>` for\n"
    "links. Gmail renders this beautifully.\n\n"
    "```bash\n"
    "$GAPI gmail send --to user@example.com \\\n"
    "  --subject \"Reddit summary report\" \\\n"
    "  --html \\\n"
    "  --body '<p>Hi Kar Wei,</p>\n"
    "<p>Here is your Reddit summary report as requested.</p>\n"
    "<p><strong>Google Sheet:</strong> <a href=\"https://docs.google.com/spreadsheets/d/ID/edit\">Open spreadsheet</a></p>\n"
    "<p><strong>Attached PDF:</strong> <code>hermes_reddit_summary.pdf</code></p>\n"
    "<p>Let me know if you need anything else.</p>\n"
    "<p>Best,<br>Munki</p>' \\\n"
    "  --attach /opt/data/hermes_reddit_summary.pdf\n"
    "```\n"
    "`--attach` can be repeated for multiple files. Plain-text mode is\n"
    "fine for single-line confirmations (e.g. `--body 'Done.'`) - skip the\n"
    "`--html` flag in those cases. The raw\n"
    "`gws gmail users messages send` form (taking `--json '{...raw...}'`)\n"
    "exists but requires you to base64url-encode RFC2822 yourself -\n"
    "almost always wrong choice. Use `$GAPI gmail send` instead.\n\n"
    "### Drive: share a file/sheet/folder with a user\n\n"
    "```bash\n"
    "gws drive permissions create \\\n"
    "  --params '{\"fileId\":\"<id>\",\"sendNotificationEmail\":false}' \\\n"
    "  --json '{\"role\":\"writer\",\"type\":\"user\",\"emailAddress\":\"<email>\"}'\n"
    "```\n"
    "`role`: `reader` | `commenter` | `writer` | `owner`. "
    "`type`: `user` | `group` | `domain` | `anyone`. "
    "Set `sendNotificationEmail:true` (and optionally `emailMessage`) to notify the recipient.\n\n"
    "### Drive: move a file to a folder (replace parents)\n\n"
    "```bash\n"
    "gws drive files update \\\n"
    "  --params '{\"fileId\":\"<id>\",\"addParents\":\"<folder-id>\",\"removeParents\":\"<old-parent-id>\"}'\n"
    "```\n"
    "Get the current parent first via "
    "`gws drive files get --params '{\"fileId\":\"<id>\",\"fields\":\"parents\"}'`.\n\n"
    "### Drive: copy a file (and place in a folder)\n\n"
    "```bash\n"
    "gws drive files copy \\\n"
    "  --params '{\"fileId\":\"<id>\"}' \\\n"
    "  --json '{\"name\":\"My Copy\",\"parents\":[\"<folder-id>\"]}'\n"
    "```\n\n"
    "### Drive: search by name / mime / modified time\n\n"
    "```bash\n"
    "gws drive files list \\\n"
    "  --params '{\"q\":\"name contains \\\"Q4\\\" and mimeType = \\\"application/vnd.google-apps.spreadsheet\\\"\",\"pageSize\":10,\"orderBy\":\"modifiedTime desc\"}'\n"
    "```\n"
    "Common mime types: `application/vnd.google-apps.spreadsheet`, "
    "`.document`, `.presentation`, `.folder`.\n\n"
    "### Sheets: overwrite a range\n\n"
    "Values go in `--json`'s body, range/options in `--params`. "
    "`valueInputOption` is **required**: `RAW` (literal) or `USER_ENTERED` (parses formulas/dates).\n\n"
    "```bash\n"
    "gws sheets spreadsheets values update \\\n"
    "  --params '{\"spreadsheetId\":\"<id>\",\"range\":\"Sheet1!A1:B2\",\"valueInputOption\":\"USER_ENTERED\"}' \\\n"
    "  --json '{\"values\":[[\"A1\",\"B1\"],[\"A2\",\"B2\"]]}'\n"
    "```\n\n"
    "### Sheets: read a range as JSON\n\n"
    "```bash\n"
    "gws sheets spreadsheets values get \\\n"
    "  --params '{\"spreadsheetId\":\"<id>\",\"range\":\"Sheet1!A1:D10\"}'\n"
    "```\n\n"
    "### Sheets: add a new tab/sheet to a spreadsheet\n\n"
    "```bash\n"
    "gws sheets spreadsheets batchUpdate \\\n"
    "  --params '{\"spreadsheetId\":\"<id>\"}' \\\n"
    "  --json '{\"requests\":[{\"addSheet\":{\"properties\":{\"title\":\"NewTab\"}}}]}'\n"
    "```\n"
    "Use `batchUpdate` for any structural change: rename tab, delete tab, format cells, freeze rows, etc.\n\n"
    "### Gmail: mark a message as read (remove UNREAD label)\n\n"
    "```bash\n"
    "gws gmail users messages modify \\\n"
    "  --params '{\"userId\":\"me\",\"id\":\"<message-id>\"}' \\\n"
    "  --json '{\"removeLabelIds\":[\"UNREAD\"]}'\n"
    "```\n"
    "Same shape works for archive (`removeLabelIds:[\"INBOX\"]`), star "
    "(`addLabelIds:[\"STARRED\"]`), trash, etc.\n\n"
    "### Gmail: list threads matching a query\n\n"
    "```bash\n"
    "gws gmail users threads list \\\n"
    "  --params '{\"userId\":\"me\",\"q\":\"from:boss@x.com is:unread\",\"maxResults\":10}'\n"
    "```\n"
    "Threads keep replies grouped; prefer this over `messages list` for conversation views.\n\n"
    "### Gmail: create a draft\n\n"
    "```bash\n"
    "gws gmail users drafts create \\\n"
    "  --params '{\"userId\":\"me\"}' \\\n"
    "  --json '{\"message\":{\"raw\":\"<base64url-encoded RFC2822>\"}}'\n"
    "```\n"
    "For sending, prefer the higher-level `$GAPI gmail send` from the Usage\n"
    "section above - it builds the RFC2822 envelope for you.\n\n"
    "### Gmail: multi-line email bodies (CRITICAL)\n\n"
    "Bash double-quoted strings do **NOT** interpret `\\n` as a newline -\n"
    "they pass `\\` + `n` literally. So `--body \"Hi,\\n\\nThanks\"` sends an\n"
    "email whose body is the literal text `Hi,\\n\\nThanks`. Recipients see\n"
    "the backslash-n on screen instead of paragraph breaks.\n\n"
    "**Always use ANSI-C quoting (`$'...'`) for multi-line bodies** - that\n"
    "syntax DOES expand `\\n`, `\\t`, etc. to actual control characters:\n\n"
    "```bash\n"
    "# CORRECT - $'...' expands escapes\n"
    "$GAPI gmail send --to user@example.com \\\n"
    "  --subject \"Report\" \\\n"
    "  --body $'Hi Kar Wei,\\n\\nHere is your report:\\n\\nhttps://example.com/sheet\\n\\nThanks,\\nMomo'\n\n"
    "# ALSO CORRECT - real newlines inside double quotes work fine\n"
    "$GAPI gmail send --to user@example.com --subject \"Report\" --body \"Hi Kar Wei,\n"
    "\n"
    "Here is your report.\n"
    "\n"
    "Thanks,\n"
    "Momo\"\n\n"
    "# WRONG - the \\n stays literal; recipient sees `Hi Kar Wei,\\n\\nHere is...`\n"
    "$GAPI gmail send --to user@example.com --subject \"Report\" \\\n"
    "  --body \"Hi Kar Wei,\\n\\nHere is your report.\"\n"
    "```\n\n"
    "For HTML bodies (`--html`), use `<br>` or `<p>` tags instead of newlines -\n"
    "those render correctly regardless of how the body string was quoted.\n"
)


# ----------------------------------------------------------------------------
# Patcher
# ----------------------------------------------------------------------------
def patch_pre_auth(path: str) -> None:
    """Three-part patch on the NousResearch productivity/google-workspace
    SKILL.md, all idempotent:

    1. Strip the `related_skills: [himalaya]` line from the YAML frontmatter.
       Upstream points the bot at the himalaya skill as a Gmail-only
       alternative, but in AutoBricks we don't bundle himalaya AND we've
       already auto-authenticated google-workspace, so any "let me try
       himalaya instead" detour is a wasted turn ending in
       `command not found: himalaya`.

    2. Replace `## First-Time Setup` with the pre-auth block (credentials are
       auto-populated; agent only uses --check / --revoke).

    3. Append `## Common Workflows` at end of file (verified gws CLI examples
       for share/move/copy/overwrite/batch-update/modify/threads/drafts).
    """
    p = pathlib.Path(path)
    text = p.read_text()
    changed = False

    # 1) Strip related_skills: [himalaya] (or any related_skills entry mentioning
    # himalaya). The line lives in the YAML frontmatter at the top of the file.
    new_text, n = re.subn(
        r"^\s*related_skills:.*\bhimalaya\b.*\n",
        "",
        text,
        count=1,
        flags=re.MULTILINE,
    )
    if n:
        text = new_text
        changed = True
        print(f"[pre-auth] stripped related_skills: [himalaya] from {path}")

    # 1b) Rewrite the upstream GAPI/GSETUP shorthand definitions in the
    # `## Usage` section to use absolute paths. Upstream uses
    # `python ${HERMES_HOME:-$HOME/.hermes}/skills/...` which the model
    # mis-substitutes (it confuses workspace_context.path for HERMES_HOME)
    # and produces nonsense paths like /home/workspace/workspace/.hermes/.../
    # AND `python` doesn't resolve to anything (no `python` binary in PATH;
    # only `/opt/hermes/.venv/bin/python` has the google-api-python-client
    # deps installed). Replace those shorthand lines wholesale.
    abs_python = "/opt/hermes/.venv/bin/python"
    abs_skill = "/opt/hermes/skills/productivity/google-workspace/scripts"
    n_total = 0
    for var, script in [("GAPI", "google_api.py"), ("GSETUP", "setup.py")]:
        pat = (
            rf'{var}="python\s+\$\{{HERMES_HOME:-\$HOME/\.hermes\}}'
            rf"/skills/productivity/google-workspace/scripts/{script}\""
        )
        repl = f'{var}="{abs_python} {abs_skill}/{script}"'
        text, n = re.subn(pat, repl, text, count=1)
        n_total += n
    if n_total:
        changed = True
        print(f"[pre-auth] rewrote {n_total} upstream shorthand path(s) to absolute in {path}")

    # 2) Replace `## First-Time Setup` with the pre-auth block
    if _PRE_AUTH_MARKER not in text:
        new_text, n = re.subn(
            r"## First-Time Setup\n.*?(?=\n## )",
            _PRE_AUTH_BLOCK,
            text,
            count=1,
            flags=re.DOTALL,
        )
        if n != 1:
            raise SystemExit(
                f"[pre-auth] could not locate ## First-Time Setup in {path} "
                f"(matches={n}). Upstream SKILL.md layout may have changed."
            )
        text = new_text
        changed = True
        print(f"[pre-auth] patched ## First-Time Setup in {path}")
    else:
        print(f"[pre-auth] {path} already has pre-auth section, skipping")

    # 3) Append `## Common Workflows` at end of file
    if _WORKFLOWS_MARKER not in text:
        if not text.endswith("\n"):
            text += "\n"
        text += "\n" + _WORKFLOWS_BLOCK
        changed = True
        print(f"[pre-auth] appended ## Common Workflows to {path}")
    else:
        print(f"[pre-auth] {path} already has common-workflows section, skipping")

    if changed:
        p.write_text(text)


# ----------------------------------------------------------------------------
# Authentication override (openclaw / gws-shared/SKILL.md)
# ----------------------------------------------------------------------------
_AUTH_NEW_BLOCK = (
    "## Authentication\n\n"
    "**In AutoBricks AI managed bots, authentication is handled by the\n"
    "platform.** A `credentials.json` (authorized_user format) is\n"
    "auto-populated at `~/.config/gws/credentials.json` after the user\n"
    "clicks \"Connect Google Workspace\" in the AutoBricks dashboard.\n\n"
    "- Do **NOT** run `gws auth login`.\n"
    "- Do **NOT** create `client_secret.json` or attempt manual OAuth.\n"
    "- If a `gws` command returns an authentication error (token expired,\n"
    "  credentials missing), tell the user: *\"Google Workspace credentials\n"
    "  need refreshing - please click Reconnect in the AutoBricks\n"
    "  dashboard.\"* Then stop.\n\n"
)


def patch_auth(path: str) -> None:
    p = pathlib.Path(path)
    text = p.read_text()
    if "AutoBricks AI managed bots" in text:
        print(f"[auth] {path} already patched, skipping")
        return
    new_text, n = re.subn(
        r"## Authentication\n.*?(?=\n## )",
        _AUTH_NEW_BLOCK,
        text,
        count=1,
        flags=re.DOTALL,
    )
    if n != 1:
        raise SystemExit(
            f"[auth] could not locate ## Authentication in {path} (matches={n})."
        )
    p.write_text(new_text)
    print(f"[auth] patched {path}")


# ----------------------------------------------------------------------------
# Common Workflows snippets (openclaw / per-skill bundle)
# ----------------------------------------------------------------------------
_WORKFLOWS_PREAMBLE = (
    "Below are verified-working commands for the most common operations.\n"
    "The `## API Resources` list above is exhaustive but example-free.\n"
    "Prefer these patterns when they fit, and `gws schema <resource>.<method>`\n"
    "for anything else. Resource IDs and query options go in `--params`;\n"
    "request body fields go in `--json`.\n"
)


_WORKFLOWS_PER_SKILL_MARKER = "<!-- AUTOBRICKS:COMMON-WORKFLOWS -->"


WORKFLOW_SNIPPETS: dict[str, str] = {
    "gws-drive": (
        _WORKFLOWS_PREAMBLE + "\n"
        "### Share a file/sheet/folder with a user\n\n"
        "```bash\n"
        "gws drive permissions create \\\n"
        "  --params '{\"fileId\":\"<id>\",\"sendNotificationEmail\":false}' \\\n"
        "  --json '{\"role\":\"writer\",\"type\":\"user\",\"emailAddress\":\"<email>\"}'\n"
        "```\n"
        "`role`: `reader` | `commenter` | `writer` | `owner`. "
        "`type`: `user` | `group` | `domain` | `anyone`.\n\n"
        "### Move a file to a folder (replace parents)\n\n"
        "```bash\n"
        "gws drive files update \\\n"
        "  --params '{\"fileId\":\"<id>\",\"addParents\":\"<folder-id>\",\"removeParents\":\"<old-parent-id>\"}'\n"
        "```\n\n"
        "### Copy a file (and place in a folder)\n\n"
        "```bash\n"
        "gws drive files copy \\\n"
        "  --params '{\"fileId\":\"<id>\"}' \\\n"
        "  --json '{\"name\":\"My Copy\",\"parents\":[\"<folder-id>\"]}'\n"
        "```\n\n"
        "### Search by name / mime / modified time\n\n"
        "```bash\n"
        "gws drive files list \\\n"
        "  --params '{\"q\":\"name contains \\\"Q4\\\" and mimeType = \\\"application/vnd.google-apps.spreadsheet\\\"\",\"pageSize\":10,\"orderBy\":\"modifiedTime desc\"}'\n"
        "```\n"
    ),
    "gws-sheets": (
        _WORKFLOWS_PREAMBLE + "\n"
        "### Overwrite a range\n\n"
        "Values go in `--json`'s body, range/options in `--params`. "
        "`valueInputOption` is required: `RAW` or `USER_ENTERED`.\n\n"
        "```bash\n"
        "gws sheets spreadsheets values update \\\n"
        "  --params '{\"spreadsheetId\":\"<id>\",\"range\":\"Sheet1!A1:B2\",\"valueInputOption\":\"USER_ENTERED\"}' \\\n"
        "  --json '{\"values\":[[\"A1\",\"B1\"],[\"A2\",\"B2\"]]}'\n"
        "```\n\n"
        "### Read a range as JSON\n\n"
        "```bash\n"
        "gws sheets spreadsheets values get \\\n"
        "  --params '{\"spreadsheetId\":\"<id>\",\"range\":\"Sheet1!A1:D10\"}'\n"
        "```\n\n"
        "### Add a new tab/sheet to a spreadsheet\n\n"
        "```bash\n"
        "gws sheets spreadsheets batchUpdate \\\n"
        "  --params '{\"spreadsheetId\":\"<id>\"}' \\\n"
        "  --json '{\"requests\":[{\"addSheet\":{\"properties\":{\"title\":\"NewTab\"}}}]}'\n"
        "```\n"
    ),
    "gws-gmail": (
        _WORKFLOWS_PREAMBLE + "\n"
        "### Mark a message as read (remove UNREAD label)\n\n"
        "```bash\n"
        "gws gmail users messages modify \\\n"
        "  --params '{\"userId\":\"me\",\"id\":\"<message-id>\"}' \\\n"
        "  --json '{\"removeLabelIds\":[\"UNREAD\"]}'\n"
        "```\n\n"
        "### List threads matching a query\n\n"
        "```bash\n"
        "gws gmail users threads list \\\n"
        "  --params '{\"userId\":\"me\",\"q\":\"from:boss@x.com is:unread\",\"maxResults\":10}'\n"
        "```\n\n"
        "### Create a draft\n\n"
        "```bash\n"
        "gws gmail users drafts create \\\n"
        "  --params '{\"userId\":\"me\"}' \\\n"
        "  --json '{\"message\":{\"raw\":\"<base64url-encoded RFC2822>\"}}'\n"
        "```\n"
    ),
}


def patch_workflows(skill_id: str, path: str) -> None:
    if skill_id not in WORKFLOW_SNIPPETS:
        raise SystemExit(
            f"[workflows] no snippet for {skill_id!r}. Known: {sorted(WORKFLOW_SNIPPETS)}"
        )
    p = pathlib.Path(path)
    text = p.read_text()
    if _WORKFLOWS_PER_SKILL_MARKER in text:
        print(f"[workflows {skill_id}] {path} already patched, skipping")
        return
    block = (
        "## Common Workflows\n\n"
        f"{_WORKFLOWS_PER_SKILL_MARKER}\n\n"
        + WORKFLOW_SNIPPETS[skill_id]
        + "\n"
    )
    new_text, n = re.subn(
        r"(?=^## Discovering Commands\b)",
        block,
        text,
        count=1,
        flags=re.MULTILINE,
    )
    if n != 1:
        raise SystemExit(
            f"[workflows {skill_id}] could not locate ## Discovering Commands in {path} "
            f"(matches={n})."
        )
    p.write_text(new_text)
    print(f"[workflows {skill_id}] patched {path}")


_USAGE = (
    "usage: gws-skills-overrides.py --pre-auth <path>\n"
    "       gws-skills-overrides.py --auth <path>\n"
    "       gws-skills-overrides.py --workflows <skill-id> <path>"
)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(_USAGE)
    op = sys.argv[1]
    if op == "--pre-auth":
        if len(sys.argv) != 3:
            sys.exit(_USAGE)
        patch_pre_auth(sys.argv[2])
    elif op == "--auth":
        if len(sys.argv) != 3:
            sys.exit(_USAGE)
        patch_auth(sys.argv[2])
    elif op == "--workflows":
        if len(sys.argv) != 4:
            sys.exit(_USAGE)
        patch_workflows(sys.argv[2], sys.argv[3])
    else:
        sys.exit(_USAGE)
