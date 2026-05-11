# ===============================================================
# scripts/load-secrets.ps1
# ---------------------------------------------------------------
# Loads all /projectbig/* SecureString parameters from AWS SSM
# into the current PowerShell session as environment variables.
# Optionally launches Claude Code or the FastAPI backend afterward.
#
# Reference: docs/adr/0003-aws-ssm-standard-tier.md
# Open Question: harness-taskboard.md SEC-1
#
# Usage:
#   .\scripts\load-secrets.ps1                  # Just load secrets
#   .\scripts\load-secrets.ps1 -List            # Dry-run (mappings only)
#   .\scripts\load-secrets.ps1 -StartClaude     # Load + start `claude`
#   .\scripts\load-secrets.ps1 -StartBackend    # Load + start FastAPI dev server
#
# Design notes:
# - Application code should call core.aws_ssm.get_secret() as canonical path.
# - This script is a *convenience* tool for ad-hoc shell sessions.
# - Missing parameters are warned (not fatal) so partially-prepared envs work
#   (e.g., kabu STATION application pending, OpenCanvas not yet set up).
# - English output only (PowerShell 5.1 ANSI/UTF-8 encoding caveat).
# ===============================================================

[CmdletBinding()]
param(
    [switch]$StartClaude,
    [switch]$StartBackend,
    [switch]$List,
    [string]$Region = "ap-northeast-1",
    [string]$Prefix = "/projectbig/"
)

$ErrorActionPreference = "Stop"

# --- Mapping: SSM path suffix -> environment variable name ---
$mapping = [ordered]@{
    "kabucom/password"           = "KABUCOM_PASSWORD"
    "kabucom/api-password"       = "KABUCOM_API_PASSWORD"
    "opencanvas/client-id"       = "BANK_API_CLIENT_ID"
    "opencanvas/client-secret"   = "BANK_API_CLIENT_SECRET"
    "discord/webhook-trading"    = "DISCORD_WEBHOOK_TRADING"
    "discord/webhook-system"     = "DISCORD_WEBHOOK_SYSTEM"
    "discord/webhook-alerts"     = "DISCORD_WEBHOOK_ALERTS"
    "discord/bot-token"          = "DISCORD_BOT_TOKEN"
    "anthropic/api-key"          = "ANTHROPIC_API_KEY"
    "edinet/api-key"             = "EDINET_API_KEY"
}

# --- Pre-flight: AWS CLI presence ---
if (-not (Get-Command aws -ErrorAction SilentlyContinue)) {
    Write-Host "[FAIL] AWS CLI not found in PATH." -ForegroundColor Red
    Write-Host "       Install from https://aws.amazon.com/cli/ and run 'aws configure'." -ForegroundColor Red
    exit 1
}

# --- List mode (dry-run) ---
if ($List) {
    Write-Host ""
    Write-Host "=== load-secrets.ps1 (dry-run) ===" -ForegroundColor Cyan
    Write-Host "Region: $Region"
    Write-Host "Prefix: $Prefix"
    Write-Host ""
    Write-Host "Will attempt to load these SSM parameters:"
    foreach ($entry in $mapping.GetEnumerator()) {
        $path = "$Prefix$($entry.Key)"
        $envVar = $entry.Value
        Write-Host ("  {0,-30} <- {1}" -f $envVar, $path)
    }
    Write-Host ""
    Write-Host "Run without -List to actually load." -ForegroundColor Yellow
    exit 0
}

# --- Load each secret into session env vars ---
Write-Host ""
Write-Host "=== Loading secrets from AWS SSM (region=$Region) ===" -ForegroundColor Cyan

$loaded = 0
$skipped = 0
foreach ($entry in $mapping.GetEnumerator()) {
    $path = "$Prefix$($entry.Key)"
    $envVar = $entry.Value
    try {
        $value = aws ssm get-parameter `
            --name $path `
            --with-decryption `
            --region $Region `
            --query "Parameter.Value" `
            --output text
        if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($value)) {
            Write-Host ("  [SKIP] {0,-30} ({1} not found)" -f $envVar, $path) -ForegroundColor DarkYellow
            $skipped++
            continue
        }
        Set-Item -Path "env:$envVar" -Value $value
        Write-Host ("  [OK]   {0,-30} <- {1}" -f $envVar, $path) -ForegroundColor Green
        $loaded++
    }
    catch {
        Write-Host ("  [FAIL] {0,-30} ({1})" -f $envVar, $_.Exception.Message) -ForegroundColor Red
        $skipped++
    }
}

Write-Host ""
Write-Host ("Loaded {0} secrets ({1} skipped/missing)" -f $loaded, $skipped) -ForegroundColor Cyan

if ($loaded -eq 0) {
    Write-Host ""
    Write-Host "[WARN] No secrets loaded. Check:" -ForegroundColor Yellow
    Write-Host "  1. AWS CLI is configured: 'aws sts get-caller-identity'" -ForegroundColor Yellow
    Write-Host "  2. IAM user has ssm:GetParameter permission on $Prefix*" -ForegroundColor Yellow
    Write-Host "  3. Parameters exist: 'aws ssm describe-parameters'" -ForegroundColor Yellow
    Write-Host "  4. Register them via: 'python scripts\register_secrets.py'" -ForegroundColor Yellow
}

# --- Optional: launch downstream tools ---
if ($StartClaude) {
    Write-Host ""
    Write-Host "Starting Claude Code with loaded session env..." -ForegroundColor Cyan
    if (Get-Command claude -ErrorAction SilentlyContinue) {
        & claude
    } else {
        Write-Host "[FAIL] 'claude' command not found in PATH." -ForegroundColor Red
        exit 1
    }
}
elseif ($StartBackend) {
    Write-Host ""
    Write-Host "Starting FastAPI backend (uvicorn dev server)..." -ForegroundColor Cyan
    $venvActivate = Join-Path $PSScriptRoot ".." "backend" "venv" "Scripts" "Activate.ps1"
    if (-not (Test-Path $venvActivate)) {
        Write-Host "[FAIL] backend venv not found at: $venvActivate" -ForegroundColor Red
        Write-Host "       Run: cd backend; python -m venv venv; .\venv\Scripts\Activate.ps1; pip install -r requirements.txt" -ForegroundColor Yellow
        exit 1
    }
    & $venvActivate
    Push-Location (Join-Path $PSScriptRoot ".." "backend")
    try {
        & uvicorn src.main:app --reload
    } finally {
        Pop-Location
    }
}
