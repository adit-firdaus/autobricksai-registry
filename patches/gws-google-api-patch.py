#!/usr/bin/env python3
"""
Idempotent patch for the upstream `google_api.py` ($GAPI) wrapper that
ships with @googleworkspace/cli. Adds --attach (file attachment) support
to `gmail send` and `gmail reply`, since neither command natively supports
attachments and Skill-Workflows agents need them for "email this PDF" tasks.

Usage:
    python gws-google-api-patch.py /path/to/google_api.py

Idempotent via a marker comment — re-running on an already-patched file is
a no-op. Run during image build immediately after npm-installing
@googleworkspace/cli (before the tree is locked down).

The patch:
  1. Adds `from email.mime.multipart import MIMEMultipart` and
     `from email.mime.base import MIMEBase` + `from email import encoders`
     + `import mimetypes` near the top imports if missing.
  2. Replaces gmail_send() body construction to switch from MIMEText to a
     multipart/mixed envelope when args.attach is non-empty. Each attach
     path becomes a MIMEBase part with content-type guessed from the
     filename, base64-encoded, with Content-Disposition: attachment.
  3. Same for gmail_reply().
  4. argparse: adds `--attach` (repeatable, default=[]) to BOTH the send
     and reply sub-parsers.

Idempotent guard: marker `# AUTOBRICKS:ATTACH-V1` at top of file.
"""
from __future__ import annotations

import pathlib
import re
import sys

MARKER = "# AUTOBRICKS:ATTACH-V1"

# ---------- The patched helper that builds a MIME message with attachments ----------
HELPER_BLOCK = '''

# AUTOBRICKS:ATTACH-V1  attach-aware MIME builder used by gmail_send / gmail_reply
def _autobricks_build_message(body: str, *, to: str = "", subject: str = "",
                              cc: str = "", from_header: str = "",
                              html: bool = False, attach: list[str] | None = None):
    """Return an email.message.Message ready for base64url encoding.

    If `attach` is non-empty, returns a multipart/mixed message with the body
    as the first part and each file as a subsequent part. Otherwise returns a
    plain MIMEText.
    """
    import mimetypes as _mt
    from email.mime.base import MIMEBase as _MIMEBase
    from email.mime.multipart import MIMEMultipart as _MIMEMultipart
    from email.mime.text import MIMEText as _MIMEText
    from email import encoders as _encoders
    import os as _os

    if not attach:
        msg = _MIMEText(body, "html" if html else "plain")
    else:
        msg = _MIMEMultipart("mixed")
        msg.attach(_MIMEText(body, "html" if html else "plain"))
        for path in attach:
            if not _os.path.exists(path):
                raise FileNotFoundError(f"attachment not found: {path}")
            ctype, encoding = _mt.guess_type(path)
            if ctype is None or encoding is not None:
                ctype = "application/octet-stream"
            maintype, subtype = ctype.split("/", 1)
            with open(path, "rb") as f:
                part = _MIMEBase(maintype, subtype)
                part.set_payload(f.read())
            _encoders.encode_base64(part)
            part.add_header("Content-Disposition", "attachment",
                            filename=_os.path.basename(path))
            msg.attach(part)
    if to:           msg["to"] = to
    if subject:      msg["subject"] = subject
    if cc:           msg["cc"] = cc
    if from_header:  msg["from"] = from_header
    return msg

'''


def _rewrite_message_block(text: str) -> tuple[str, int]:
    """Replace every `message = MIMEText(args.body, ...)` + the immediately
    following `message["to/subject/cc/from"] = ...` assignments with a single
    `message = _autobricks_build_message(...)` call.

    Line-by-line scan instead of one giant regex so it survives indent /
    whitespace / line-ending drift in the upstream package. The block ends
    at the first non-`message[...]`-assignment line that isn't an
    `if args.cc/from_header:` guard.
    """
    lines = text.split("\n")
    out: list[str] = []
    n_rewritten = 0
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.lstrip()
        # Send-style: takes a 2nd positional arg (html/plain). The reply
        # block uses `MIMEText(args.body)` with no comma — handled separately.
        if stripped.startswith("message = MIMEText(args.body,"):
            indent = " " * (len(line) - len(stripped))
            # Skip ahead past all consecutive lines that touch `message` —
            # the assignment block, plus any `if args.cc:` / `if args.from_header:`
            # guards and their single-line bodies.
            j = i + 1
            while j < len(lines):
                nxt = lines[j].lstrip()
                if (nxt.startswith("message[") or
                    nxt.startswith("if args.cc") or
                    nxt.startswith("if args.from_header")):
                    j += 1
                    continue
                # Guard bodies are indented one level deeper; they start with
                # `message[`. Already captured above via .lstrip(). So we stop
                # at the first line that doesn't match.
                break
            # Emit the helper-call replacement at the original indent.
            out.append(f"{indent}message = _autobricks_build_message(")
            out.append(f"{indent}    args.body,")
            out.append(f"{indent}    to=args.to,")
            out.append(f"{indent}    subject=args.subject,")
            out.append(f"{indent}    cc=args.cc,")
            out.append(f"{indent}    from_header=args.from_header,")
            out.append(f"{indent}    html=getattr(args, 'html', False),")
            out.append(f"{indent}    attach=getattr(args, 'attach', None) or [],")
            out.append(f"{indent})")
            n_rewritten += 1
            i = j
        else:
            out.append(line)
            i += 1
    return "\n".join(out), n_rewritten


def _rewrite_reply_block(text: str) -> tuple[str, int]:
    """For gmail_reply: replace `message = MIMEText(args.body)` (note no html arg)
    with an attach-aware variant that falls back to MIMEText when no attach.
    """
    lines = text.split("\n")
    out: list[str] = []
    n_rewritten = 0
    for line in lines:
        stripped = line.lstrip()
        # Reply uses plain MIMEText(args.body) (no html arg).
        # Distinguish from gmail_send's `MIMEText(args.body, "html" ...)`.
        if (stripped == 'message = MIMEText(args.body)' or
            stripped == 'message = MIMEText(args.body)\r'):
            indent = " " * (len(line) - len(stripped))
            out.append(f"{indent}# AUTOBRICKS:ATTACH-V1 reply: support attachments")
            out.append(f"{indent}if getattr(args, 'attach', None):")
            out.append(f"{indent}    message = _autobricks_build_message(")
            out.append(f"{indent}        args.body, html=False, attach=args.attach,")
            out.append(f"{indent}    )")
            out.append(f"{indent}else:")
            out.append(f"{indent}    message = MIMEText(args.body)")
            n_rewritten += 1
        else:
            out.append(line)
    return "\n".join(out), n_rewritten


def patch(path: str) -> None:
    p = pathlib.Path(path)
    text = p.read_text()

    if MARKER in text:
        print(f"[gws-google-api-patch] {path}: already patched, skipping")
        return

    # Normalise CRLF -> LF so all our line-by-line scanners work uniformly.
    text = text.replace("\r\n", "\n")

    # --- 1. Inject the helper block right before the first `def gmail_send` line.
    m = re.search(r"^def gmail_send\b", text, flags=re.MULTILINE)
    if not m:
        raise SystemExit(f"[gws-google-api-patch] could not find `def gmail_send` in {path}")
    text = text[: m.start()] + HELPER_BLOCK + "\n" + text[m.start():]

    # --- 2. Rewrite the message-construction blocks in gmail_send (both the
    # _gws_binary() branch and the build_service fallback branch share the
    # same shape, so a single line-by-line pass catches both).
    text, n_send = _rewrite_message_block(text)
    if n_send == 0:
        raise SystemExit(
            f"[gws-google-api-patch] could not find any `message = MIMEText(args.body, ...)` "
            f"line in {path} — upstream package may have changed shape."
        )
    print(f"[gws-google-api-patch] gmail_send body blocks: {n_send} rewritten")

    # --- 3. Same idea for gmail_reply (different MIMEText shape — no html arg).
    text, n_reply = _rewrite_reply_block(text)
    print(f"[gws-google-api-patch] gmail_reply MIMEText lines: {n_reply} rewritten")

    # --- 4. Argparse: add --attach to gmail send and gmail reply sub-parsers.
    # The send parser ends with `p.set_defaults(func=gmail_send)` after the --thread-id arg.
    # Inject --attach just before set_defaults.
    send_argparse_pat = re.compile(
        r'(    p = gmail_sub\.add_parser\("send"\)\n'
        r'(?:    p\.add_argument\([^\n]+\n)+'
        r')(    p\.set_defaults\(func=gmail_send\))'
    )
    new_text, n_send_arg = send_argparse_pat.subn(
        r'\1    p.add_argument("--attach", action="append", default=[], '
        r'help="Path to file to attach (repeatable for multiple files)")\n\2',
        text,
    )
    if n_send_arg == 0:
        raise SystemExit(f"[gws-google-api-patch] could not find gmail send argparse block in {path}")
    text = new_text
    print(f"[gws-google-api-patch] gmail send argparse: added --attach")

    # Same for gmail reply.
    reply_argparse_pat = re.compile(
        r'(    p = gmail_sub\.add_parser\("reply"\)\n'
        r'(?:    p\.add_argument\([^\n]+\n)+'
        r')(    p\.set_defaults\(func=gmail_reply\))'
    )
    new_text, n_reply_arg = reply_argparse_pat.subn(
        r'\1    p.add_argument("--attach", action="append", default=[], '
        r'help="Path to file to attach (repeatable)")\n\2',
        text,
    )
    text = new_text
    print(f"[gws-google-api-patch] gmail reply argparse: {n_reply_arg} added")

    p.write_text(text)
    print(f"[gws-google-api-patch] {path}: patched (marker {MARKER} now present)")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: gws-google-api-patch.py <path-to-google_api.py>", file=sys.stderr)
        sys.exit(2)
    patch(sys.argv[1])
