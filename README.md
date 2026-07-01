# autobricksai-registry

Container-image source for the **`autobot-hermes`** family — the Hermes +
hermes-workspace runtime that powers every bot on the AutoBricks AI
platform. Pairs with the main monorepo
[`Autobricks-AI/Autobricks-AI`](https://github.com/Autobricks-AI/Autobricks-AI)
(the FastAPI control plane, provision flow, Traefik routing, billing, etc.).

This repo exists so the image layer can ship independently from the API:
- Image rebuilds and `(autobricks) registry pull`s happen in tight cycles
  during incidents, without dragging the whole monorepo's CI.
- GitHub Actions on this repo produces a **published, publicly pullable
  artifact on GHCR** that the monorepo's `docker_host.py` already supports
  pulling from `ghcr.io/...` instead of the legacy private registry on
  `82.22.63.206:5000`.
- The one-command student installer in `install/install.sh` lets users run
  Hermes locally in Docker, wired to AutoBricks as the model provider,
  by downloading and running a single shell script.

---

## For students: run Hermes locally

> Requires Docker (Desktop, or Engine on Linux) and a free AutoBricks API key
> from `autobricksai.com → account → API Keys`.

The published source lives in the GitHub org that owns the image registry.
Today that's **`Autobricks-AI/autobricksai-registry`**. If you're running
from a fork / cohort mirror, replace the URL with your mirror path.

```bash
# Paste your key into the installer (or set AUTOBRICKS_API_KEY=abai_sk_live_... first):
curl -fsSL https://raw.githubusercontent.com/Autobricks-AI/autobricksai-registry/main/install/install.sh | bash

# From a fork / class-cohort mirror:
curl -fsSL https://raw.githubusercontent.com/<your-org>/autobricksai-registry/main/install/install.sh | bash
```

What it does (see `install/install.sh` for the canonical docblock):
1. Verifies Docker + Compose are installed and the daemon is running.
2. Asks for / accepts your `AUTOBRICKS_API_KEY` (or reuses one in an existing
   `data/.env`).
3. Writes `~/autobricks-hermes/data/.env` (chmod 600) with the secrets and
   proxy vars the image expects.
4. Writes `~/autobricks-hermes/data/config.yaml` with the AutoBricks
   provider block (only on first run — never clobbers user edits).
5. Writes `~/autobricks-hermes/docker-compose.yml` referencing the published
   image at `ghcr.io/Autobricks-AI/autobricksai-registry/autobot-hermes:latest`.
6. `docker compose pull && up -d`.
7. Waits up to ~90 s for the WebUI on `http://localhost:8787` and prints
   your one-time password.

Re-running is safe: secrets and user config are preserved; only the
`docker-compose.yml` is regenerated so image-update flags propagate.

### Pin to a specific published tag

```bash
HERMES_IMAGE=ghcr.io/Autobricks-AI/autobricksai-registry/autobot-hermes:main \
  curl -fsSL https://raw.githubusercontent.com/Autobricks-AI/autobricksai-registry/main/install/install.sh | bash

# Or a tagged release:
HERMES_IMAGE=ghcr.io/Autobricks-AI/autobricksai-registry/autobot-hermes:v1.0.0 \
  curl -fsSL ... | bash
```

### Update / stop

```bash
cd ~/autobricks-hermes
docker compose pull && docker compose up -d    # update to the latest image
docker compose logs -f                          # tail logs
docker compose down                             # stop (keeps your data in ./data)
```

---

## Layout

```
.
├── build/
│   ├── Dockerfile                  ← full autobot-hermes image (two-stage build)
│   ├── Dockerfile.spinup-patch     ← thin "entrypoint rebaser" layer
│   └── hermes-workspace-base/
│       Dockerfile                  ← upstream hermes-workspace Dockerfile
│                                      vendored verbatim so this repo can
│                                      rebuild the base from upstream source
│                                      (no host pre-bake step)
├── entrypoint/
│   ├── entrypoint.sh               ← container startup (sourced by supervisord)
│   ├── supervisord.conf            ← gateway / dashboard / workspace / explorer / ttyd / abai-mcp
│   ├── wait-for-hermes-gateway.sh  ← gates the workspace server on /v1/models
│   ├── hermes-report-style.css     ← brand stylesheet baked into the pdf skill
│   ├── skills-search.py            ← marketplace search bridge (workspace → hermes skills list)
│   ├── skills-install.py           ← marketplace install/uninstall bridge (workspace → hermes CLI)
│   └── abai-mcp-server.py          ← MCP exec-bridge server (run_command / read_file / write_file)
├── patches/
│   ├── gws-skills-overrides.py     ← Google Workspace SKILL.md pre-auth patch
│   ├── gws-google-api-patch.py     ← adds --attach to gmail send/reply wrapper
│   ├── patch-hermes-pdf-skill.py   ← AutoBricks PDF report override
│   ├── patch-hermes-models-dev.py  ← abai provider alias + autobricksai inject
│   ├── patch-hermes-persona.py     ← /persona <slug> slash command
│   ├── patch-workspace-providers.cjs   ← inject "abai" into hermes-workspace provider lists
│   ├── patch-agent-meta-seeder.cjs     ← seed agent system prompts from profile config.yaml
│   ├── patch-skills-card-link.cjs      ← marketplace "View source" link to upstream skill page
│   └── patch-skills-install.cjs        ← marketplace install/uninstall to use local hermes CLI
├── skills/
│   └── skill-workflow/             ← pre-installed skill bundle (SKILL.md + handler.py)
├── install/
│   └── install.sh                  ← one-command local-Docker installer (pulls the published image)
├── .github/workflows/
│   ├── hermes-workspace-base.yml   ← builds + publishes the upstream
│   │                                  hermes-workspace base to GHCR
│   └── build-push.yml              ← autobricks overlay build, push, and
│                                      visibility-set (consumes the base)
└── README.md                       ← this file
```

---

## How the workflow ties in

`.github/workflows/build-push.yml` runs:

| Trigger | Result |
|---|---|
| Push to `main` | Builds both images, pushes `:main`, `:latest`, `:<date>` |
| Push tag `v*` | Builds both images, pushes `:vX.Y.Z`, `:vX.Y`, `:vX`, `:latest` |
| Manual dispatch | Rebuilds on demand; optional `tag_override` input |

Both images publish to a single GHCR package, owned by the **same GitHub
user/org that owns this repo** (default `IMAGE_OWNER` in the workflow is
`${{ github.repository_owner }}`). To force the prod namespace
(`Autobricks-AI`) regardless of where this repo is hosted, set a repo
variable `IMAGE_OWNER=Autobricks-AI` at
*Settings → Secrets and variables → Actions → Variables*.

```
ghcr.io/<owner>/autobricksai-registry/autobot-hermes:latest
ghcr.io/<owner>/autobricksai-registry/autobot-hermes:main
ghcr.io/<owner>/autobricksai-registry/autobot-hermes:vX.Y.Z

ghcr.io/<owner>/autobricksai-registry/autobot-hermes-spinup:main
ghcr.io/<owner>/autobricksai-registry/autobot-hermes-spinup:latest
```

The autobricks platform's `docker_host.py` resolves each per-bot image
name to a registry URL. Once the workflow's first build goes green, swap
the legacy `82.22.63.206:5000/autobricks/autobot-hermes:latest` references
over to `ghcr.io/<owner>/autobricksai-registry/autobot-hermes:<tag>`.

### Automatic public-visibility enforcement

GHCR creates new packages as **private** by default and the `docker/login-action`
push alone won't flip that. The workflow runs an extra step after every push
that calls `PATCH /{orgs|user}/packages/container/autobricksai-registry/visibility`
with `visibility=public` so the package is anonymously pullable for students.

The PATCH is tried with two credentials, in order:
1. `secrets.GITHUB_TOKEN` — works when the package ends up **repo-scoped**.
2. `secrets.GHCR_ADMIN_TOKEN` — a PAT held by a maintainer with the
   `write:packages` scope on the org, stored as an **org or repo secret**.

If neither succeeds (e.g. the org-scoped package needs an org admin PAT but
none has been provisioned), the step logs a `:warning::` annotation and a
link to the manual settings page but does **not** fail the workflow — the
images are pushed, so authenticated callers can still pull. Always follow
up by manually flipping visibility at
`github.com/<org-or-user>/packages/container/autobricksai-registry/settings`
(use `…/orgs/…` for org-owned packages, `…/users/…` for personal ones).

A subsequent `Verify image is publicly pullable` step HEADs the registry
manifest for `:latest` after each main-branch build; a non-200 there means
the visibility flip did not land (GHCR sometimes takes a minute to
propagate).

---

## Build pipeline (two workflows)

The autobricks overlay (`build-push.yml`) is a **multi-stage** Docker build
whose Stage-2 base is upstream `outsourc-e/hermes-workspace`. To keep CI
hosted runners buildable without depending on each autobricks docker host
having a hand-baked `hermes-workspace-local:vX.Y.Z`, the base is itself
rebuilt and published by a sibling workflow:

```
┌────────────────────────────────┐    ┌─────────────────────────────────┐
│ hermes-workspace-base.yml      │    │ build-push.yml                  │
│                                │    │                                 │
│ 1. Download upstream tarball   │    │ 1. docker build build/Dockerfile│
│    @ pinned tag (v2.3.0)       │    │      FROM $WORKSPACE_BASE       │
│ 2. Verify vendored Dockerfile  │ ─► │ 2. patchers + entrypoint bits   │
│    matches upstream byte-for-  │    │ 3. push to GHCR                 │
│    byte (fail-loud on drift)   │    │ 4. flip GHCR package public     │
│ 3. docker build (node22, pnpm) │    │ 5. verify public pull           │
│ 4. push ghcr.io/<owner>/       │    │                                 │
│    autobricksai-registry/      │    │                                 │
│    hermes-workspace-base:v2.3.0│    │                                 │
│ 5. flip GHCR package public    │    │                                 │
└────────────────────────────────┘    └─────────────────────────────────┘
```

You **must** publish `hermes-workspace-base` before `build-push.yml` can
build the overlay (the overlay's Stage-2 FROM references it by tag).

### Bumping the upstream base version

1. Check the upstream release notes at
   <https://github.com/outsourc-e/hermes-workspace/releases>.
2. Re-vendor the new Dockerfile into
   `build/hermes-workspace-base/Dockerfile` (download raw from upstream at
   the new tag, then prepend the comment header that's already in the file).
3. Bump `HERMES_WORKSPACE_TAG` in
   `.github/workflows/hermes-workspace-base.yml` and the `v2.3.0` default in
   the workflow `inputs`.
4. Bump `WORKSPACE_BASE_IMAGE` default in `build/Dockerfile` and the
   `WORKSPACE_BASE` value in `.github/workflows/build-push.yml` to the new
   tag (e.g. `:v2.4.0`).
5. Push to `main` and run `hermes-workspace-base.yml` once via
   *Run workflow* (accept the default tag, which is now the new release).
6. The next push to `main` (or a manual run) of `build-push.yml` will pick
   up the new base.

The vendored Dockerfile is checked against upstream at the same tag in a
verification step; the base workflow **fails the build** if the file in
this repo has drifted from upstream, so silent divergence is impossible.

### Required permissions

GitHub Actions on this repo must have **Read and write** permissions so
the default `GITHUB_TOKEN` can publish to GHCR. Repository settings → *Actions
→ General → Workflow permissions*.

No secrets are **required** for the build. The optional `GHCR_ADMIN_TOKEN`
secret is only needed if you want full automation of the public-visibility
flip on org-scoped packages.

---

## Build & test locally

The two-stage `build/Dockerfile` requires `hermes-agent:v2026.6.5` and
`hermes-workspace-local:v2.3.0` in the local Docker daemon. Those are
internal mirrors populated by the autobricks build host; rebuilding from
scratch outside that environment is not currently supported, and is
why the workflow runs on a pristine `ubuntu-24.04` runner.

If you only need the spinup-patch rebase (e.g. you changed just the
entrypoint), you can build that locally:

```bash
# Pull whichever tag the main image was published under
# Replace <owner> with Autobricks-AI for prod, or your fork's GitHub owner.
docker pull ghcr.io/<owner>/autobricksai-registry/autobot-hermes:main

# Bake the spinup patch over it
docker build \
  --build-arg HERMES_IMAGE=ghcr.io/<owner>/autobricksai-registry/autobot-hermes:main \
  -f build/Dockerfile.spinup-patch \
  -t autobricksai/autobot-hermes-spinup:local \
  .
```

---

## Version pinning policy (do NOT relax this)

Two upstream tags are baked into the image. They are explicitly pinned to:

- `nousresearch/hermes-agent:v2026.6.5` — gateway + Python CLI
- `hermes-workspace-local:v2.3.0` — built React workspace bundle

We do **not** use `:latest` or `:main` for either. The history on
2026-06-08 (4 bots crash-looped after an upstream silent re-tag of
`hermes-agent:latest`) makes the rationale non-negotiable. To upgrade,
bump the digest explicitly in `build/Dockerfile` after reviewing the
upstream release notes, and open a PR.

---

## Prose of intent

This is a container-image-only repository. There is no runtime code
here. The hermes-agent Python source lives upstream; the
hermes-workspace React bundle lives upstream; only the AutoBricks
overlay bits that **customise** those upstream images live here. If you
are looking for the API / control plane / dashboard, that lives in
`Autobricks-AI/Autobricks-AI` (the monorepo).
