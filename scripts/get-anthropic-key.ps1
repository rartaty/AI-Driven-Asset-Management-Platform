# ===============================================================
# scripts/get-anthropic-key.ps1
# ---------------------------------------------------------------
# Anthropic API key fetcher for Claude Code's apiKeyHelper.
# Outputs the API key to stdout (and nothing else) so Claude Code
# can capture it. Errors go to stderr.
#
# Reference: docs/adr/0003-aws-ssm-standard-tier.md
# Open Question: harness-taskboard.md SEC-6'
#
# Setup (one-time):
#   1. Add to ~/.claude/settings.json:
#        "apiKeyHelper": "powershell -NoProfile -ExecutionPolicy Bypass -File C:\\path\\to\\scripts\\get-anthropic-key.ps1"
#   2. Verify: 'powershell -File scripts\get-anthropic-key.ps1' prints just the key
#   3. Delete plaintext ~/.claude/.credentials.json (Claude will fetch via this script)
#
# Design notes:
# - Single SSM parameter call (no caching — apiKeyHelper is invoked once per
#   Claude Code startup, so caching is unnecessary and would add complexity)
# - English-only output (PowerShell 5.1 encoding caveat)
# - Exits with code 1 on any failure so Claude Code falls back to other auth methods
# ===============================================================

[CmdletBinding()]
param(
    [string]$Region = "ap-northeast-1",
    [string]$Path = "/projectbig/anthropic/api-key"
)

$ErrorActionPreference = "Stop"

# --- Pre-flight: AWS CLI presence ---
if (-not (Get-Command aws -ErrorAction SilentlyContinue)) {
    [Console]::Error.WriteLine("[get-anthropic-key.ps1] AWS CLI not found in PATH.")
    exit 1
}

# --- Fetch and emit ---
try {
    $value = aws ssm get-parameter `
        --name $Path `
        --with-decryption `
        --region $Region `
        --query "Parameter.Value" `
        --output text
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($value)) {
        [Console]::Error.WriteLine("[get-anthropic-key.ps1] SSM parameter '$Path' not found or empty.")
        exit 1
    }
    # Emit ONLY the key value (no newlines, no extra text — apiKeyHelper expects raw)
    [Console]::Out.Write($value.Trim())
    exit 0
}
catch {
    [Console]::Error.WriteLine("[get-anthropic-key.ps1] Failed to fetch secret: $($_.Exception.Message)")
    exit 1
}
