#!/usr/bin/env python3
"""
skill-workflow skill — Hermes handler.

Lists and runs user-defined Skill Workflows on the AutoBricks platform. Auth
piggybacks on the per-bot ``AUTOBRICKS_API_KEY`` mounted at
``/opt/data/.env`` during bot provisioning — no separate connect-tile flow.
The internal API (``/api/internal/workflows/*``) accepts ``abai_sk_live_*``
bearer tokens and resolves them to the bot's owner, whose workflows are
listed/run.

Output: one JSON object on stdout, {"action", "ok", "data" | "error"}.

Env overrides (for local testing):
  AUTOBRICKS_API_KEY       — bearer key (default: read from /opt/data/.env)
  AUTOBRICKSAI_API_BASE    — gateway URL (default: read from .env or fallback)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any
from urllib.parse import urlparse

import httpx

ENV_PATH = "/opt/data/.env"
# The internal workflows API lives on the MAIN domain. The `api.autobricksai.com`
# subdomain is a gateway restricted to /v1/* (OpenAI-compat surface only — see
# main.py:101). Sending /api/internal/workflows/* to the api subdomain returns
# 404 by design.
DEFAULT_API_BASE = "https://autobricksai.com"
HTTP_TIMEOUT = 30.0
RUN_POLL_INTERVAL = 1.5
RUN_POLL_TIMEOUT_DEFAULT = 300.0


# ============================================================
# Config IO
# ============================================================
class Quit(RuntimeError):
    def __init__(self, code: str, details: str = "", status: int | None = None):
        self.code = code
        self.details = details
        self.status = status
        super().__init__(code)


def _read_env_file(path: str) -> dict[str, str]:
    """Parse a minimal KEY=VALUE .env file. ``docker exec`` doesn't inherit
    the env that supervisord sourced at startup, so we re-read it ourselves.
    Identical to what skill-translate.py used to do."""
    out: dict[str, str] = {}
    try:
        with open(path) as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                val = val.strip().strip("'").strip('"')
                out[key.strip()] = val
    except FileNotFoundError:
        pass
    return out


def load_config() -> dict:
    """Build {api_base, token, default_vm_id} from process env + /opt/data/.env.

    - Token is the bot's per-container ``AUTOBRICKS_API_KEY``.
    - ``default_vm_id`` is the bot's own user_vms.id, written into the .env at
      provisioning so the bot can target its own VM by default when running
      workflows. If unset, `run` requires the caller to pass --vm.
    """
    env_file = _read_env_file(ENV_PATH)
    token = (os.environ.get("AUTOBRICKS_API_KEY")
             or env_file.get("AUTOBRICKS_API_KEY")
             or "").strip()
    if not token:
        raise Quit(
            "not_configured",
            f"No AUTOBRICKS_API_KEY in environment or {ENV_PATH}. "
            f"This is normally set automatically at bot provisioning; "
            f"if it's missing, redeploy the bot from the AutoBricks dashboard.",
        )
    api_base = (os.environ.get("AUTOBRICKSAI_API_BASE")
                or env_file.get("AUTOBRICKSAI_API_BASE")
                or DEFAULT_API_BASE).rstrip("/")
    default_vm_id = (os.environ.get("AUTOBRICKSAI_VM_ID")
                     or env_file.get("AUTOBRICKSAI_VM_ID")
                     or "").strip() or None
    return {"api_base": api_base, "token": token, "default_vm_id": default_vm_id}


# ============================================================
# REST wire layer
# ============================================================
def _validate_api_base(url: str) -> str:
    if not url:
        raise Quit("invalid_api_base", "api_base is required")
    u = url.strip().rstrip("/")
    parsed = urlparse(u)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise Quit("invalid_api_base", f"api_base must be http(s)://host[:port], got {url!r}")
    return u


def _headers(cfg: dict) -> dict[str, str]:
    return {
        "Accept": "application/json",
        "Authorization": f"Bearer {cfg['token']}",
    }


def request(
    cfg: dict,
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: Any = None,
    timeout: float = HTTP_TIMEOUT,
) -> httpx.Response:
    base = _validate_api_base(cfg["api_base"])
    url = f"{base}{path}"
    hdrs = _headers(cfg)
    if json_body is not None:
        hdrs["Content-Type"] = "application/json"
    try:
        with httpx.Client(timeout=timeout, follow_redirects=False) as c:
            r = c.request(method, url, params=params, headers=hdrs, json=json_body)
    except httpx.ConnectError as exc:
        raise Quit("autobricks_unreachable", str(exc)) from exc
    except httpx.TimeoutException as exc:
        raise Quit("timeout", str(exc)) from exc
    except httpx.HTTPError as exc:
        raise Quit("http_error", str(exc)) from exc

    if r.status_code in (401, 403):
        raise Quit("invalid_token", _short(r), status=r.status_code)
    if r.status_code == 404:
        raise Quit("not_found", _short(r), status=r.status_code)
    if r.status_code in (400, 422):
        body = _safe_json(r)
        detail = body.get("detail") if isinstance(body, dict) else None
        if isinstance(detail, dict) and detail.get("error") == "missing_skills":
            raise Quit("missing_skills", json.dumps(detail)[:500], status=r.status_code)
        raise Quit("invalid_request", _short(r), status=r.status_code)
    if r.status_code >= 400:
        raise Quit("http_error", _short(r), status=r.status_code)
    return r


def _short(r: httpx.Response) -> str:
    return (r.text or "").strip()[:500] or f"HTTP {r.status_code}"


def _safe_json(r: httpx.Response) -> Any:
    try:
        return r.json()
    except Exception:
        return {}


def _json(r: httpx.Response) -> Any:
    try:
        return r.json()
    except Exception as exc:
        raise Quit("http_error", f"non-JSON response: {exc}: {r.text[:200]}") from exc


# ============================================================
# Actions
# ============================================================
def op_status() -> dict:
    try:
        cfg = load_config()
    except Quit as exc:
        return {"configured": False, "error": exc.code, "details": exc.details}
    try:
        request(cfg, "GET", "/api/internal/workflows/by-name", timeout=10.0)
        return {"configured": True, "api_base": cfg["api_base"], "last_check_ok": True}
    except Quit as exc:
        return {
            "configured": True, "api_base": cfg["api_base"],
            "last_check_ok": False, "error": exc.code, "details": exc.details,
        }


def op_list(cfg: dict) -> dict:
    r = request(cfg, "GET", "/api/internal/workflows/by-name", timeout=15.0)
    body = _json(r)
    items = body.get("workflows") if isinstance(body, dict) else None
    if not isinstance(items, list):
        items = []
    return {"count": len(items), "workflows": items}


def op_run(cfg: dict, *, name: str, inputs_raw: str | None,
           vm_id: str | None) -> dict:
    """Fire-and-forget run. Returns {run_id, status_url} immediately. The
    completion notification is delivered to the bot's home channel by the
    platform when the run finishes — there's no synchronous wait here so
    the chat stays responsive while the workflow executes."""
    inputs: dict[str, Any] = {}
    if inputs_raw:
        try:
            parsed = json.loads(inputs_raw)
        except json.JSONDecodeError as exc:
            raise Quit("bad_arg", f"--inputs is not valid JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise Quit("bad_arg", "--inputs must be a JSON object")
        inputs = parsed

    body: dict[str, Any] = {"input_payload": inputs}
    # vm_id resolution order: explicit --vm > bot's own VM (from /opt/data/.env) >
    # let the server fall back to workflow.default_vm_id. The bot's own VM is the
    # right default when chat is the caller — running someone else's workflow on
    # the same bot the user is talking to.
    chosen_vm = vm_id or cfg.get("default_vm_id")
    if chosen_vm:
        body["vm_id"] = chosen_vm
    r = request(cfg, "POST", f"/api/internal/workflows/by-name/{name}/run",
                json_body=body, timeout=20.0)
    return _json(r)


def op_get_run(cfg: dict, *, run_id: str, wait: bool, timeout: float) -> dict:
    if wait:
        return _wait_for_run(cfg, run_id=run_id, timeout=timeout)
    r = request(cfg, "GET", f"/api/internal/workflows/runs/{run_id}/wait",
                params={"timeout": 1}, timeout=15.0)
    return _json(r)


def op_cancel_run(cfg: dict, run_id: str) -> dict:
    # Cancel goes through the user-side API; the internal router doesn't expose
    # a separate cancel endpoint because the long-poll wait endpoint already
    # returns terminal status on cancel.
    r = request(cfg, "POST", f"/api/workflows/runs/{run_id}/cancel", timeout=10.0)
    return _json(r)


def _wait_for_run(cfg: dict, *, run_id: str, timeout: float) -> dict:
    """Long-poll the wait endpoint, looping until terminal or timeout."""
    deadline = time.monotonic() + max(1.0, timeout)
    last: dict | None = None
    while True:
        remaining = max(1, int(deadline - time.monotonic()))
        chunk = min(60, remaining)
        r = request(
            cfg, "GET", f"/api/internal/workflows/runs/{run_id}/wait",
            params={"timeout": chunk}, timeout=chunk + 10,
        )
        last = _json(r)
        status = last.get("status") if isinstance(last, dict) else None
        if status in ("succeeded", "failed", "cancelled"):
            return last
        if time.monotonic() >= deadline:
            if isinstance(last, dict):
                last = dict(last)
                last["_wait_timeout"] = True
            return last or {"_wait_timeout": True, "run_id": run_id}
        # Brief breather between chunks so we don't hot-loop on a fast server.
        sys.stderr.write(f"[run {run_id}] {status or 'pending'}\n")
        sys.stderr.flush()
        time.sleep(RUN_POLL_INTERVAL)


# ============================================================
# CLI dispatch
# ============================================================
_ACTIONS = {
    "status",
    "list", "run", "get_run", "cancel_run",
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="skill-workflow")
    ap.add_argument("--action", required=True, choices=sorted(_ACTIONS))

    # Run
    ap.add_argument("--name", help="(run) workflow slug")
    ap.add_argument("--inputs", help="(run) JSON object of workflow inputs")
    ap.add_argument("--vm", dest="vm_id", help="(run) override target VM id")

    # Polling (only `get_run` accepts these — see check below)
    ap.add_argument("--run-id", dest="run_id", help="(get_run / cancel_run) run id")
    ap.add_argument("--wait", action="store_true", help="(get_run only) block until terminal")
    ap.add_argument("--timeout", type=float, default=RUN_POLL_TIMEOUT_DEFAULT)

    args = ap.parse_args(argv)

    try:
        if args.action == "status":
            return _emit("status", ok=True, data=op_status())

        cfg = load_config()

        if args.action == "list":
            return _emit("list", ok=True, data=op_list(cfg))

        if args.action == "run":
            if not args.name:
                raise Quit("bad_arg", "run requires --name")
            # `run` is fire-and-forget by design — synchronous wait blocks the
            # bot's chat loop. If the agent tries the old shape, reject loudly
            # so the LLM sees the new contract and corrects on the next call.
            if args.wait:
                raise Quit(
                    "wait_not_supported_on_run",
                    "run is fire-and-forget. Call --action run (returns run_id "
                    "immediately) then --action get_run --run-id <id> --wait if "
                    "you need to block. Completion is delivered to the user's "
                    "home channel automatically — usually no wait is needed.",
                )
            return _emit("run", ok=True, data=op_run(
                cfg, name=args.name, inputs_raw=args.inputs,
                vm_id=args.vm_id,
            ))

        if args.action == "get_run":
            if not args.run_id:
                raise Quit("bad_arg", "get_run requires --run-id")
            return _emit("get_run", ok=True, data=op_get_run(
                cfg, run_id=args.run_id, wait=args.wait, timeout=args.timeout,
            ))

        if args.action == "cancel_run":
            if not args.run_id:
                raise Quit("bad_arg", "cancel_run requires --run-id")
            return _emit("cancel_run", ok=True, data=op_cancel_run(cfg, args.run_id))

        raise Quit("no_op", f"Unhandled action: {args.action}")

    except Quit as exc:
        return _emit(args.action, ok=False, error=exc.code, details=exc.details)
    except Exception as exc:  # pragma: no cover — last-resort
        return _emit(args.action, ok=False, error="exception", details=str(exc))


def _emit(action: str, *, ok: bool, data: dict | None = None,
          error: str | None = None, details: str | None = None) -> int:
    payload: dict[str, Any] = {"action": action, "ok": ok}
    if data is not None:
        payload["data"] = data
    if error:
        payload["error"] = error
    if details:
        payload["details"] = details
    print(json.dumps(payload, default=str, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
