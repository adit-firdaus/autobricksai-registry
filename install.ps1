<#
Hermes Local - installer + control panel for Windows (PowerShell).
Runs Hermes (WebUI :8787) locally in Docker Desktop, wired to AutoBricks as the model provider.
Image pulled from the autobricksai-registry package on GHCR (default owner adit-firdaus):
  ghcr.io/<IMAGE_OWNER>/autobricksai-registry/autobot-hermes

Usage (PowerShell):
  irm https://adit-firdaus.github.io/autobricksai-registry/install.ps1 | iex
  $env:AUTOBRICKS_API_KEY='abai_sk_live_...'; irm <url> | iex

Usage (cmd.exe):
  powershell -NoProfile -Command "irm https://adit-firdaus.github.io/autobricksai-registry/install.ps1 | iex"

Behaviour:
  first run (nothing installed) -> guided install wizard
  already installed             -> menu: Status / Update / Logs / Restart / Stop / Uninstall
  no interactive console (CI)    -> installs straight through (needs $env:AUTOBRICKS_API_KEY)

Overrides (env):
  AUTOBRICKS_API_KEY   your abai_sk_live_... key (else prompted)
  AUTOBRICKS_BASE_URL  model API base (default https://api.autobricksai.com/v1)
  IMAGE_OWNER          GHCR namespace owner (default adit-firdaus)
  HERMES_IMAGE         full image ref override
  APP_DIR              install dir (default %USERPROFILE%\autobricks-hermes)
  HERMES_ACTION        install|menu|status|update|logs|restart|stop|uninstall
  HERMES_WIPE=1        when uninstalling non-interactively, also delete .\data
#>
$ErrorActionPreference = 'Stop'

# ---- constants ----
function EnvOr($name, $default) {
  $v = [Environment]::GetEnvironmentVariable($name)
  if ([string]::IsNullOrEmpty($v)) { $default } else { $v }
}
$HomeBase    = if ($env:USERPROFILE) { $env:USERPROFILE } elseif ($env:HOME) { $env:HOME } else { (Get-Location).Path }
$AppDir      = EnvOr 'APP_DIR' (Join-Path $HomeBase 'autobricks-hermes')
$DataDir     = Join-Path $AppDir 'data'
$EnvFile     = Join-Path $DataDir '.env'
$ConfigFile  = Join-Path $DataDir 'config.yaml'
$ComposeFile = Join-Path $AppDir 'docker-compose.yml'
$BaseUrl     = EnvOr 'AUTOBRICKS_BASE_URL' 'https://api.autobricksai.com/v1'
$ImageOwner  = EnvOr 'IMAGE_OWNER' 'adit-firdaus'
$Image       = if ($env:HERMES_IMAGE) { $env:HERMES_IMAGE } else { "ghcr.io/$ImageOwner/autobricksai-registry/autobot-hermes:latest" }
$DefaultModel= 'autobricksai/mimo-2.5'
$Port        = 8787
$Container   = 'autobricks-hermes'
$ApiKeysUrl  = 'https://autobricksai.com/account/settings?tab=api'
$script:ComposeV2 = $null   # $true = "docker compose", $false = "docker-compose"
$script:Up = $false

# ---- output helpers ----
function Info($m) { Write-Host "> $m"  -ForegroundColor Blue }
function Ok($m)   { Write-Host "OK $m" -ForegroundColor Green }
function Warn($m) { Write-Host "! $m"  -ForegroundColor Yellow }
function Die($m)  { Write-Host "x $m"  -ForegroundColor Red; exit 1 }
function Hr()     { Write-Host '--------------------------------------------------------' }

# ---- interactivity ----
function HaveTty {
  try { return ([Environment]::UserInteractive -and -not [Console]::IsInputRedirected) } catch { return $false }
}
function Ask($p) { Read-Host -Prompt $p }
function AskSecret($p) {
  $sec = Read-Host -Prompt $p -AsSecureString
  $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
  try { [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
  finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}
function AskYN($p, $default = 'N') {
  $a = Read-Host -Prompt $p
  if ([string]::IsNullOrEmpty($a)) { $a = $default }
  return ($a -match '^[Yy]')
}

# ---- data/.env read/upsert (LF line endings for the Linux container's env_file) ----
function ReadEnv($key) {
  if (-not (Test-Path -LiteralPath $EnvFile)) { return '' }
  foreach ($line in Get-Content -LiteralPath $EnvFile) {
    if ($line -match ("^" + [regex]::Escape($key) + "=(.*)$")) { return $Matches[1] }
  }
  return ''
}
function SetEnv($key, $value) {
  New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
  $lines = @()
  if (Test-Path -LiteralPath $EnvFile) { $lines = @(Get-Content -LiteralPath $EnvFile) }
  $out = New-Object System.Collections.Generic.List[string]
  $found = $false
  foreach ($line in $lines) {
    if ($line -match ("^" + [regex]::Escape($key) + "=")) { $found = $true; $out.Add("$key=$value") }
    else { $out.Add($line) }
  }
  if (-not $found) { $out.Add("$key=$value") }
  [IO.File]::WriteAllText($EnvFile, (($out -join "`n") + "`n"))
}
function GenSecret {
  $bytes = New-Object 'byte[]' 24
  [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
  ($bytes | ForEach-Object { $_.ToString('x2') }) -join ''
}

# ---- docker preflight (memoised) ----
function NeedDocker {
  if ($null -ne $script:ComposeV2) { return }
  if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Die "Docker is not installed. Install Docker Desktop: https://docs.docker.com/desktop/install/windows-install/"
  }
  & docker info *> $null
  if ($LASTEXITCODE -ne 0) { Die "Docker is installed but the daemon isn't running. Start Docker Desktop and re-run." }
  & docker compose version *> $null
  if ($LASTEXITCODE -eq 0) { $script:ComposeV2 = $true }
  elseif (Get-Command docker-compose -ErrorAction SilentlyContinue) { $script:ComposeV2 = $false }
  else { Die "Docker Compose not found. Update Docker Desktop." }
}
function Compose {
  if ($script:ComposeV2) { & docker compose @args } else { & docker-compose @args }
}

# ---- deployment state: running | stopped | missing ----
function GetState {
  $s = (& docker ps -a --filter ("name=^/$Container$") --format '{{.State}}' 2>$null)
  if ([string]::IsNullOrWhiteSpace($s)) { return 'missing' }
  if ($s -match 'running') { return 'running' }
  return 'stopped'
}

# ---- API key: env > .env > prompt (with no-key redirect) ----
function ResolveApiKey {
  if ($env:AUTOBRICKS_API_KEY) { return $env:AUTOBRICKS_API_KEY }
  $existing = ReadEnv 'AUTOBRICKS_API_KEY'
  if ($existing) { Info 'Reusing existing AutoBricks API key.'; return $existing }
  if (-not (HaveTty)) { Die "No API key and no interactive console. Re-run with: `$env:AUTOBRICKS_API_KEY='abai_sk_live_...'; irm <url> | iex" }
  if (-not (AskYN 'Do you have an AutoBricks API key? (y/N)' 'N')) {
    Info 'Create one here:'
    Write-Host "  $ApiKeysUrl" -ForegroundColor Cyan
    Write-Host '  (sign in, open API Keys, create one, copy the abai_sk_live_... value, then paste below)'
  }
  $key = AskSecret 'Paste your AutoBricks API key'
  if ([string]::IsNullOrEmpty($key)) { Die 'Empty API key.' }
  return $key
}

# ---- config writers (LF endings) ----
function WriteEnvFile($apiKey) {
  $sk = ReadEnv 'API_SERVER_KEY'; if (-not $sk) { $sk = GenSecret }
  $pw = ReadEnv 'CLAUDE_PASSWORD'; if (-not $pw) { $pw = GenSecret }
  SetEnv 'AUTOBRICKS_API_KEY' $apiKey
  SetEnv 'OPENAI_API_KEY'     $apiKey
  SetEnv 'API_SERVER_KEY'     $sk
  SetEnv 'HERMES_API_TOKEN'   $sk
  SetEnv 'CLAUDE_PASSWORD'    $pw
  SetEnv 'HERMES_WEBUI_PASSWORD' $pw
  SetEnv 'HERMES_PASSWORD'    $pw
  SetEnv 'HERMES_HOME'        '/opt/data'
  if (-not (ReadEnv 'GITHUB_TOKEN')) { SetEnv 'GITHUB_TOKEN' '' }
  Ok "Wrote $EnvFile."
}
function WriteConfigFile {
  if (Test-Path -LiteralPath $ConfigFile) { Info "Keeping existing $ConfigFile."; return }
  $c = @"
# AutoBricks model provider for Hermes (OpenAI-compatible gateway).
model:
  default: $DefaultModel
  provider: abai
providers:
  abai:
    name: AutoBricks AI
    api: $BaseUrl
    key_env: AUTOBRICKS_API_KEY
    transport: openai_chat
    models:
      - autobricksai/mimo-2.5
      - autobricksai/claude-haiku-4.5
      - autobricksai/claude-sonnet-4.6
      - autobricksai/claude-opus-4.7
      - autobricksai/gemini-2.5-flash
      - autobricksai/gemini-2.5-pro
      - autobricksai/gpt-5.4
      - autobricksai/gpt-4.1
      - autobricksai/deepseek-v4
      - autobricksai/qwen3-235b
"@
  [IO.File]::WriteAllText($ConfigFile, ($c -replace "`r`n", "`n"))
  Ok "Wrote $ConfigFile."
}
function WriteComposeFile {
  $c = @"
services:
  hermes:
    image: $Image
    # image is built amd64-only; pin platform so arm64 hosts pull the amd64 image
    # and emulate instead of failing with "no matching manifest for linux/arm64".
    platform: linux/amd64
    container_name: $Container
    env_file: [data/.env]
    environment:
      ABAI_RUNTIME: runc
      HERMES_HOME: /opt/data
      # relaxed gateway auth is acceptable ONLY because the port below is bound to
      # loopback (single-user local install). Do not expose this port publicly.
      GATEWAY_ALLOW_ALL_USERS: "true"
    ports:
      # Bind to 127.0.0.1 so Hermes is reachable only from this machine, not the LAN/wifi.
      - "127.0.0.1:${Port}:${Port}"
    volumes:
      - ./data:/opt/data
    restart: unless-stopped
"@
  [IO.File]::WriteAllText($ComposeFile, ($c -replace "`r`n", "`n"))
  Ok "Wrote $ComposeFile."
}

# ---- boot wait + ready banner ----
function WaitUp {
  Info "Waiting for Hermes on http://localhost:$Port ..."
  for ($i = 0; $i -lt 45; $i++) {
    try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 "http://localhost:$Port/" *> $null; $script:Up = $true; return } catch {}
    Start-Sleep -Seconds 2
  }
  $script:Up = $false
}
function ShowReady {
  $pw = ReadEnv 'HERMES_WEBUI_PASSWORD'
  Write-Host ''
  if ($script:Up) { Ok 'Hermes is running.' } else { Warn "Hermes didn't answer on :$Port within ~90s - it may still be booting. Check logs." }
  Write-Host ''
  Hr
  Write-Host "  Open Hermes:   http://localhost:$Port"
  Write-Host  '  Login password (save this):'
  Write-Host "      $pw"
  Write-Host  ''
  Write-Host  '  Re-run this script any time to open the menu (status/update/logs/...).'
  Write-Host "  Your files and config live in: $DataDir"
  Hr
}

# ---- actions ----
function DoInstall {
  NeedDocker
  $st = GetState
  if ($st -ne 'missing' -and (HaveTty)) {
    Hr
    Info "An existing Hermes deployment was found ($st)."
    Write-Host '  [R] Reuse existing config, just (re)start'
    Write-Host '  [U] Update to the latest image'
    Write-Host "  [W] Wipe and reinstall (deletes $DataDir)"
    $c = Ask 'Choose [R/U/W] (default R)'; if ([string]::IsNullOrEmpty($c)) { $c = 'R' }
    switch -Regex ($c) {
      '^[Uu]' { DoUpdate; return }
      '^[Ww]' {
        if (Test-Path -LiteralPath $ComposeFile) { Push-Location $AppDir; try { Compose down 2>$null } catch {} finally { Pop-Location } }
        Remove-Item -Recurse -Force -LiteralPath $AppDir -ErrorAction SilentlyContinue
        Ok 'Wiped previous install.'
      }
      default {
        if (-not (Test-Path -LiteralPath $ComposeFile)) { Die "No compose file to reuse at $ComposeFile - choose W to reinstall." }
        Push-Location $AppDir; try { Compose up -d } finally { Pop-Location }
        WaitUp; ShowReady; return
      }
    }
  }

  New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
  Info "Install dir: $AppDir"
  $apiKey = ResolveApiKey
  WriteEnvFile $apiKey
  WriteConfigFile
  WriteComposeFile

  Push-Location $AppDir
  try {
    Info "Pulling image $Image ..."
    Compose pull
    if ($LASTEXITCODE -ne 0) { Die 'docker compose pull failed.' }
    Info 'Starting Hermes ...'
    Compose up -d
    if ($LASTEXITCODE -ne 0) { Die 'docker compose up failed.' }
  } finally { Pop-Location }
  WaitUp
  ShowReady
}

function DoStatus {
  NeedDocker
  $st = GetState
  Hr
  Write-Host  '  Hermes Local'
  Write-Host "  Image:      $Image"
  Write-Host "  Deployment: $st"
  if ($st -ne 'missing') {
    $status = (& docker ps -a --filter ("name=^/$Container$") --format '{{.Status}}' 2>$null)
    Write-Host "  Container:  $status"
    $dig = (& docker image inspect $Image --format '{{index .RepoDigests 0}}' 2>$null)
    if ($dig) { Write-Host "  Digest:     $dig" }
    try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 4 "http://localhost:$Port/" *> $null; Write-Host "  WebUI:      up -> http://localhost:$Port" }
    catch { Write-Host "  WebUI:      not responding on :$Port" }
  } else {
    Write-Host  '  (no container - choose Install)'
  }
  $code = 0
  try { $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 6 "$BaseUrl/models"; $code = [int]$r.StatusCode }
  catch { if ($_.Exception.Response) { $code = [int]$_.Exception.Response.StatusCode } }
  switch ($code) {
    0       { Write-Host "  Model API:  unreachable ($BaseUrl)" }
    {$_ -ge 200 -and $_ -lt 300} { Write-Host "  Model API:  reachable ($BaseUrl)" }
    {$_ -eq 401 -or $_ -eq 403}  { Write-Host "  Model API:  reachable, needs key ($BaseUrl)" }
    default { Write-Host "  Model API:  HTTP $code ($BaseUrl)" }
  }
  Write-Host "  Data dir:   $DataDir"
  Hr
}

function DoUpdate {
  NeedDocker
  if (-not (Test-Path -LiteralPath $ComposeFile)) { Die "No install found ($ComposeFile missing). Choose Install first." }
  Push-Location $AppDir
  try {
    $before = (& docker image inspect $Image --format '{{.Id}}' 2>$null); if (-not $before) { $before = 'none' }
    Info 'Pulling latest image ...'
    Compose pull
    if ($LASTEXITCODE -ne 0) { Die 'docker compose pull failed.' }
    $after = (& docker image inspect $Image --format '{{.Id}}' 2>$null); if (-not $after) { $after = 'none' }
    if ($before -eq $after) { Ok 'Already up to date.' } else { Info "New image: $after (was $before)" }
    Info 'Restarting with latest ...'
    Compose up -d
  } finally { Pop-Location }
  WaitUp
  if ($script:Up) { Ok 'Update complete.' } else { Warn "Updated, but :$Port hasn't answered yet - check logs." }
}

function DoLogs {
  NeedDocker
  if (-not (Test-Path -LiteralPath $ComposeFile)) { Die 'No install found. Choose Install first.' }
  Info 'Streaming logs - press Ctrl-C to stop.'
  Push-Location $AppDir
  try { Compose logs -f } finally { Pop-Location }
}

function DoRestart {
  NeedDocker
  if (-not (Test-Path -LiteralPath $ComposeFile)) { Die 'No install found. Choose Install first.' }
  Push-Location $AppDir; try { Compose restart } finally { Pop-Location }
  WaitUp
  if ($script:Up) { Ok 'Restarted.' } else { Warn "Restarted, but :$Port hasn't answered yet - check logs." }
}

function DoStop {
  NeedDocker
  if (-not (Test-Path -LiteralPath $ComposeFile)) { Die 'No install found. Choose Install first.' }
  Push-Location $AppDir; try { Compose stop } finally { Pop-Location }
  Ok "Stopped (container kept; data in $DataDir). Choose Restart to bring it back."
}

function DoUninstall {
  NeedDocker
  if (Test-Path -LiteralPath $ComposeFile) { Push-Location $AppDir; try { Compose down 2>$null } catch {} finally { Pop-Location } }
  else { & docker rm -f $Container 2>$null | Out-Null }
  Ok 'Container removed.'
  if (HaveTty) {
    if (AskYN "Also delete your data and config at $DataDir? (y/N)" 'N') {
      Remove-Item -Recurse -Force -LiteralPath $AppDir -ErrorAction SilentlyContinue; Ok "Deleted $AppDir."
    } else { Info "Kept $DataDir (your key and password are preserved)." }
  } elseif ($env:HERMES_WIPE -eq '1') {
    Remove-Item -Recurse -Force -LiteralPath $AppDir -ErrorAction SilentlyContinue; Ok "Deleted $AppDir."
  } else {
    Info "Kept $DataDir (set `$env:HERMES_WIPE='1' to remove non-interactively)."
  }
}

# ---- menu ----
function ShowMenu {
  NeedDocker
  while ($true) {
    $st = GetState
    Hr
    Write-Host  '  Hermes Local - control panel'
    Write-Host "  Image:    $Image"
    Write-Host "  Detected: $st"
    Hr
    Write-Host  '  1) Status / health'
    Write-Host  '  2) Update to latest'
    Write-Host  '  3) Logs'
    Write-Host  '  4) Restart'
    Write-Host  '  5) Stop'
    Write-Host  '  6) Uninstall'
    Write-Host  '  7) Reinstall / reconfigure'
    Write-Host  '  8) Quit'
    $c = Ask '>'
    switch ($c) {
      '1' { DoStatus }
      '2' { DoUpdate }
      '3' { DoLogs }
      '4' { DoRestart }
      '5' { DoStop }
      '6' { DoUninstall }
      '7' { DoInstall }
      '8' { Ok 'Bye.'; return }
      'q' { Ok 'Bye.'; return }
      ''  { }
      default { Warn 'Pick 1-8.' }
    }
  }
}

# ---- entry / dispatch ----
function Main {
  $action = $env:HERMES_ACTION
  if ([string]::IsNullOrEmpty($action)) {
    if (HaveTty) {
      if ((GetState) -eq 'missing') { $action = 'install' } else { $action = 'menu' }
    } else {
      $action = 'install'
    }
  }
  switch ($action) {
    'install'   { DoInstall }
    'menu'      { ShowMenu }
    'status'    { DoStatus }
    'update'    { DoUpdate }
    'logs'      { DoLogs }
    'restart'   { DoRestart }
    'stop'      { DoStop }
    'uninstall' { DoUninstall }
    default     { Die "Unknown HERMES_ACTION='$action' (install|menu|status|update|logs|restart|stop|uninstall)" }
  }
}
Main
