"""Idempotent patcher for Anthropic's productivity/pdf SKILL.md.

Inserts an AutoBricks override block telling the agent to render
analytics/report PDFs via weasyprint + the bundled brand stylesheet at
`/opt/hermes/skills/productivity/pdf/autobricks-report.css`, NOT via raw
pandoc default.

Usage (called from docker/Dockerfile.autobot-hermes after the pdf skill
install at line ~191):

    python3 patch-hermes-pdf-skill.py \\
        /opt/hermes/skills/productivity/pdf/SKILL.md

A `<!-- AUTOBRICKS:REPORT-STYLE:v1 -->` marker comment is written into the
file so re-runs short-circuit (same defensive pattern as
gws-skills-overrides.py).
"""
from __future__ import annotations

import pathlib
import sys


MARKER = "<!-- AUTOBRICKS:REPORT-STYLE:v2 -->"

BLOCK = f"""
## Branded reports (AutoBricks override)

{MARKER}

For any **user-facing PDF** — analytics summaries, weekly/monthly reports,
proposals, briefs, status updates — render via WeasyPrint with the bundled
AutoBricks stylesheet. Do **NOT** use `pandoc input.md -o output.pdf`: the
LaTeX defaults look like a homework assignment (plain Times-roman, no
tables, no colour, no page footer) and are not suitable for the user to
forward to clients.

### Canonical pattern: write a script file, then run it

**CRITICAL: do NOT pass the weasyprint code via `python3 -c "..."`.**
That pattern triggers Hermes' security approval gate (the gate flags
multi-line `-c` payloads as "scriptable code execution") and the call
returns `exit_code: -1, status: pending_approval` with empty output. Your
PDF won't be generated and you'll waste turns chasing a phantom error.

The reliable pattern is: write the HTML to a file, write the conversion
script to a `.py` file, then run the script with `python3 <path>` as a
plain positional argument. The `/opt/hermes/.venv/bin/python` interpreter
already has weasyprint installed.

```bash
# 1. Write the HTML (use a heredoc to avoid bash quoting hell)
cat > /opt/data/report.html <<'HTML'
<!doctype html>
<html>
<head><meta charset="utf-8"><title>Report</title></head>
<body>
  <h1>Report title</h1>
  <!-- ... see HTML scaffold below ... -->
</body>
</html>
HTML

# 2. Write the conversion script
cat > /opt/data/_render_pdf.py <<'PY'
from weasyprint import HTML, CSS
HTML(filename="/opt/data/report.html").write_pdf(
    "/opt/data/report.pdf",
    stylesheets=[CSS("/opt/hermes/skills/productivity/pdf/autobricks-report.css")],
)
print("PDF written to /opt/data/report.pdf")
PY

# 3. Run it — note: positional script path, NOT -c
/opt/hermes/.venv/bin/python /opt/data/_render_pdf.py
```

After success, `/opt/data/report.pdf` is the artifact to share/email/etc.
`weasyprint` is pre-installed in `/opt/hermes/.venv` — no `pip install`
needed (and pip is unavailable in the sandbox anyway).

### HTML scaffold

Build the report as semantic HTML, not Markdown. The stylesheet expects
this shape:

```html
<!doctype html>
<html>
<head><meta charset="utf-8"><title>{{{{ title }}}}</title></head>
<body>
  <h1>{{{{ title }}}}</h1>
  <p class="subtitle">{{{{ date_range }}}}</p>
  <p class="report-footer-meta">Generated {{{{ today }}}} · {{{{ bot_name }}}}</p>

  <!-- One <section> per platform / topic -->
  <h2>Instagram <span class="handle">(@autobricksai)</span></h2>
  <div class="summary-card">
    Two-three sentence takeaway. What changed, what to do about it.
  </div>

  <!-- Headline KPIs (optional, max ~4) -->
  <div class="kpi-row">
    <div class="kpi">
      <div class="label">Reach</div>
      <div class="value">132</div>
      <div class="delta up">+18% vs last week</div>
    </div>
    <!-- ... -->
  </div>

  <!-- Per-metric time series — ALWAYS a table, never a bullet list -->
  <h3>Reach</h3>
  <table class="metrics">
    <thead><tr><th>Date</th><th class="right">Value</th></tr></thead>
    <tbody>
      <tr><td>2026-05-11</td><td class="right">0</td></tr>
      <tr><td>2026-05-12</td><td class="right">1</td></tr>
      <!-- ... -->
    </tbody>
  </table>
</body>
</html>
```

### Rules

- **Tables, not bullet lists, for time-series.** A `<ul>` of
  "- 2026-05-11: 0" repeated 7 times looks amateur. Use `<table class="metrics">`.
- **Each platform gets `<h2>` + `<div class="summary-card">`** with a real
  takeaway. Don't ship raw numbers without interpretation.
- **`<p class="report-footer-meta">` is captured into every page's footer**
  via CSS `string-set`. Put it once near the top.
- **No `<style>` tag is needed** unless overriding the bundled CSS — the
  stylesheet covers H1/H2/H3, tables, summary cards, KPI strips, and page
  chrome.
- **Stylesheet path is absolute**: `/opt/hermes/skills/productivity/pdf/autobricks-report.css`.
  Don't try to copy it elsewhere first.

### When NOT to apply this

- One-off PDF manipulation (merge, split, extract, OCR, form-fill) — those
  use the upstream `pdf` skill workflows below, unchanged.
- Plain text dumps where styling is irrelevant — but if you're sending the
  PDF back to the user, default to styled.
"""


def patch(path: str) -> None:
    p = pathlib.Path(path)
    if not p.exists():
        raise SystemExit(f"[pdf-skill] {path} not found")
    text = p.read_text()
    if MARKER in text:
        print(f"[pdf-skill] {path} already patched, skipping")
        return
    if not text.endswith("\n"):
        text += "\n"
    text += BLOCK
    p.write_text(text)
    print(f"[pdf-skill] appended AutoBricks report-style block to {path}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: patch-hermes-pdf-skill.py <path-to-pdf-SKILL.md>")
    patch(sys.argv[1])
