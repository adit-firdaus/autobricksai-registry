# Hermes Local — How to Run It

Run **Hermes** on your own computer, powered by AutoBricks AI. Everything runs in
Docker; when it's up you open **http://localhost:8787** in your browser.

Pick **one** method below based on your device. You only need to do this once.

---

## Before you start (everyone)

1. **Install Docker Desktop** and make sure it's **running** (whale icon in the menu/tray):
   - Windows / Mac: https://www.docker.com/products/docker-desktop/
   - Linux: https://docs.docker.com/engine/install/
2. **Get your AutoBricks API key**: https://autobricksai.com/account/settings?tab=api
   → sign in → **API Keys** → create one → copy the `abai_sk_live_…` value.

> The download is a one-time ~5 GB image, so use a decent internet connection.
> On Apple Silicon Macs, enable **Rosetta** in Docker Desktop → Settings → General.

---

## Method A — macOS / Linux (one command)

Paste this into **Terminal**:

```bash
curl -fsSL https://adit-firdaus.github.io/autobricksai-registry/install.sh | bash
```

It asks for your API key, then downloads and starts Hermes. Run the same command
again any time to open a menu (Status / Update / Logs / Restart / Uninstall).

---

## Method B — Windows (PowerShell)

Open **PowerShell** and paste:

```powershell
irm https://adit-firdaus.github.io/autobricksai-registry/install.ps1 | iex
```

Using **Command Prompt (cmd)** instead? Paste this:

```bat
powershell -NoProfile -Command "irm https://adit-firdaus.github.io/autobricksai-registry/install.ps1 | iex"
```

Same experience as Method A: it prompts for your key, installs, and gives you the
menu on later runs.

---

## Method C — Docker Compose (any OS, no `curl` needed)

Best if the commands above don't work on your device.

1. **Download the compose file** into a new empty folder, saved as `docker-compose.yml`:
   https://adit-firdaus.github.io/autobricksai-registry/compose/docker-compose.yml
   (Right-click → Save As, or `curl -O <url>` / `wget <url>` if you have them.)

2. In that same folder, create a file named **`.env`** containing your key:

   ```
   AUTOBRICKS_API_KEY=abai_sk_live_xxxxxxxxxxxx
   ```

3. Start it (from that folder):

   ```bash
   docker compose up -d
   ```

That's it. (Login password for this method is **`hermes`** unless you add
`HERMES_PASSWORD=your-password` to the `.env`.)

---

## After it's running

- Open **http://localhost:8787**
- Log in with the password shown by the installer (Method A/B) or `hermes` (Method C)
- Start a new chat and pick a model (e.g. `autobricksai/mimo-2.5`) — you're talking to AutoBricks.

---

## Managing it

**Method A / B:** re-run the same install command to get the menu, or from your
install folder (`~/autobricks-hermes`):

```bash
docker compose logs -f              # view logs
docker compose restart              # restart
docker compose pull && docker compose up -d   # update to the latest version
docker compose down                 # stop (keeps your data)
```

**Method C:** run the same commands from the folder that has your `docker-compose.yml`.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| "Docker daemon isn't running" | Start Docker Desktop, wait for the whale icon to settle, retry. |
| Download looks stuck | It isn't — a 5 GB image takes a while. Watch the per-layer progress. |
| `exec format error` (Apple Silicon) | Enable **Rosetta** in Docker Desktop → Settings → General, then retry. |
| Page won't load at :8787 | Give it a minute after starting; check `docker compose logs -f`. |
| Chat says backend/401 error | Make sure you used a valid `abai_sk_live_…` key from the link above. |

Need a key? → https://autobricksai.com/account/settings?tab=api
