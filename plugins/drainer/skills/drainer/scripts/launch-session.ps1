param(
  [string]$Model,       # keep this param block in sync with the REAL launcher's
  [string]$PromptFile,  # (plugins/session-mgr/skills/resume-sessions/scripts/launch-session.ps1)
  [string]$SeedFile,
  [string]$SummaryFile,
  [string]$SessionName,
  [string]$SessionId,
  [string]$Resume
)
# Thin resolver/forwarder — NOT the launcher itself.
#
# WHY: spawn-tab.cmd runs THIS file via %~dp0, so the drainer always launches from
# its own version-pinned plugin copy, never from a floating working-clone path (a
# stale branch's launcher once set up workers with the wrong session environment).
# The real launcher lives in the session-mgr plugin; this resolver finds the
# NEWEST INSTALLED copy of it and forwards all args, so there is exactly one
# launcher and updates to it always take effect. On a machine where the plugin
# isn't installed yet, it falls back to the working-clone copy.

$ErrorActionPreference = 'Stop'

$base = Join-Path $env:USERPROFILE '.claude\plugins\cache\rrrutledge-claude-code-plugins\session-mgr'
$real = Get-ChildItem -Path $base -Directory -ErrorAction SilentlyContinue |
  Sort-Object { try { [version]$_.Name } catch { [version]'0.0.0' } } |
  ForEach-Object { Join-Path $_.FullName 'skills\resume-sessions\scripts\launch-session.ps1' } |
  Where-Object { Test-Path -LiteralPath $_ } |
  Select-Object -Last 1

if (-not $real) {
  # From plugins/drainer/skills/drainer/scripts up to plugins/, then into session-mgr.
  $fallback = Join-Path $PSScriptRoot '..\..\..\..\session-mgr\skills\resume-sessions\scripts\launch-session.ps1'
  if (Test-Path -LiteralPath $fallback) { $real = (Resolve-Path -LiteralPath $fallback).Path }
}

if (-not $real) {
  Write-Host "launch-session: could not locate the session-mgr plugin's launch-session.ps1 (no installed copy and no working-clone fallback)." -ForegroundColor Red
  exit 1
}

& $real @PSBoundParameters
