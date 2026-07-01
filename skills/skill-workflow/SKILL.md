---
name: skill-workflow
description: |
  Run a user's saved Skill Workflow by slug. Skill Workflows are visual
  multi-step DAGs the user has built at /account/skill-workflows that chain
  other Hermes skills together (e.g. propertyguru-scrape → xlsx-fill →
  google-workspace email). This skill lets the agent invoke those saved
  workflows directly.

  Standard flow: call `list` first to discover what workflows the user has,
  then `run --name <slug> --inputs '{...}' --wait` to execute one and get
  the result. Authentication is automatic — the skill uses the bot's
  per-container AUTOBRICKS_API_KEY (mounted at /opt/data/.env at bot
  provisioning); no configure step is needed.

  Use whenever the user asks to *run, trigger, execute, or invoke* a
  workflow / automation they've saved on AutoBricks, or asks "what
  workflows do I have?".
metadata:
  version: 0.1.0
  hermes:
    category: "automation"
    emoji: "🧩"
    command: '/opt/hermes/.venv/bin/python "/opt/data/skills/automation/skill-workflow/handler.py"'
    cliHelp: '/opt/hermes/.venv/bin/python "/opt/data/skills/automation/skill-workflow/handler.py" --help'
---

# skill-workflow

Trigger user-defined Skill Workflows on AutoBricks. Each workflow is a saved
DAG that chains other Hermes skills together; this skill is the agent-side
entrypoint that runs them on demand.

## When to use

- User asks to **run / trigger / execute** something by name ("run the
  propertyguru report", "kick off the daily digest", "do the email
  workflow").
- User asks **what workflows / automations they have** ("what can I run?",
  "list my workflows").
- User describes a multi-step task that *might* match a saved workflow —
  call `list` first to check before assembling skills manually.

## How to invoke

```bash
/opt/hermes/.venv/bin/python "/opt/data/skills/automation/skill-workflow/handler.py" \
  --action list
```

```bash
/opt/hermes/.venv/bin/python "/opt/data/skills/automation/skill-workflow/handler.py" \
  --action run --name propertyguru-to-email \
  --inputs '{"to": "john@example.com", "district": "D9"}'
```

## Actions

| Action | Required | Optional | What it does |
|---|---|---|---|
| `list` | — | — | List the user's workflows: `{workflows: [{slug, name, description, inputs_schema, default_vm_id}]}`. Always call this before `run` so the agent knows the available slugs and what payload shape each expects. |
| `run` | `--name SLUG` | `--inputs JSON --vm VM_ID` | Trigger a workflow. **Fire-and-forget — always returns `{run_id, status_url}` in ~1 s.** Completion is delivered to the user's home channel automatically when the run finishes; the agent does NOT block waiting. `--inputs` is a JSON object matching the workflow's `inputs_schema`. `--vm` is optional (falls back to the workflow's default VM, then to this bot's VM). There is intentionally no `--wait` flag — passing one returns a `wait_not_supported_on_run` error so the bot's chat loop stays free. |
| `get_run` | `--run-id RID` | `--wait --timeout SECONDS` | Snapshot of a run's status + per-node states + final output. Use `--wait` to long-poll (acceptable only when the user EXPLICITLY asks to wait — it will block the chat). |
| `cancel_run` | `--run-id RID` | — | Cooperative cancel — the orchestrator stops between nodes. Already-started skill executions inside a node finish on their own. |

### Diagnostics

| Action | Required | What it does |
|---|---|---|
| `status` | — | Reports whether the bot's AUTOBRICKS_API_KEY is present and the gateway is reachable: `{configured, api_base, last_check_ok}`. Useful for diagnosing platform connectivity. |

---

## Output contract

Every invocation prints one JSON object to stdout:

```json
{"action": "run", "ok": true, "data": { ... }}
```

On failure:

```json
{"action": "run", "ok": false, "error": "missing_skills", "details": "{...}"}
```

Exit code is `0` on success, `1` on failure (so the agent's shell sees both
signals).

## Errors

| `error` | Meaning | How to respond |
|---|---|---|
| `not_configured` | `AUTOBRICKS_API_KEY` missing from the bot's env. | Normally pre-mounted at provisioning. If missing, tell the user to redeploy the bot from the AutoBricks dashboard. |
| `invalid_token` | API key revoked/rotated. | Tell the user the bot's API key has been invalidated; redeploy the bot to mint a fresh one. |
| `not_found` | No workflow with that slug for this user. | Re-run `list` and check the slug. |
| `missing_skills` | Target VM is missing one or more skills the workflow needs. `details` JSON has `missing_skills: [...]`. | Tell the user which skills to install on which VM (point to `/account/skills`), or ask if they want to try a different VM via `--vm`. |
| `invalid_request` | Backend rejected the payload (bad inputs shape, bad VM, etc.). | Read `details`, fix, retry. |
| `autobricks_unreachable` / `timeout` / `http_error` | Network / platform issues. | Retry once with backoff; if persistent, surface as a platform problem. |
| `wait_not_supported_on_run` | The agent passed `--wait` to `--action run`. | Drop `--wait`, re-call `run`, then (only if the user explicitly asked to block) call `get_run --run-id <id> --wait`. The bot's chat loop locks while waiting — usually you don't want this since completion is auto-notified. |

## Heuristics

- **Always `list` first** in a conversation, then cache the result mentally
  for the rest of the turn. The list is cheap (one DB read) and avoids
  guessing slugs.
- **`run` is fire-and-forget by design — the `--wait` flag does not exist.**
  After `run`, tell the user the workflow has started, mention the
  `run_id`, and reassure them they'll get a notification when it's done
  (the platform sends it via their home channel automatically). They can
  also check `/account/skill-workflows/runs` in the dashboard.
- **For status checks**, call `get_run --run-id <id>` (NO `--wait`). It
  returns a snapshot: `{status, node_states, final_output}`. Report the
  status in plain English. Only use `get_run --wait` if the user
  *explicitly* asks "wait until it's done" / "let me know when it's
  finished" — that blocks the chat loop until the run terminates, so use
  sparingly.
- If a workflow has no `default_vm_id` and the user has multiple Hermes
  VMs, the bot must pass `--vm`. If unsure, list VMs from the user
  (you don't have direct access to that list from this skill) or use the
  VM the user most recently mentioned.
- Slugs are user-scoped and stable. If the user renames a workflow, the
  slug usually stays the same.
