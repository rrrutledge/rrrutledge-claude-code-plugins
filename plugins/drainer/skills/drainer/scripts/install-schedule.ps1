# install-schedule.ps1 - register (or remove) the ~5-min Scheduled Task that runs the keeper.
#
# This is the ONLY PowerShell in the keeper, and you run it BY HAND once (not from a Claude session),
# after the manual tryout is trusted. It registers a Windows Scheduled Task that runs the Python
# poller every N minutes.
#
#   powershell -File install-schedule.ps1 -RepoDir C:/Users/russe/Dev/personal-ai-pod [-IntervalMinutes 5]
#   powershell -File install-schedule.ps1 -RepoDir C:/Users/russe/Dev/personal-ai-pod -Remove
#
# Runs as your interactive logon (LogonType Interactive), so the poller runs under your own user
# profile and credentials.

param(
    [Parameter(Mandatory = $true)][string]$RepoDir,
    [int]$IntervalMinutes = 5,
    [switch]$Remove
)

$TaskName = 'DrainerKeeper'

if ($Remove) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Removed scheduled task '$TaskName'."
    return
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# Install the version-independent launcher to a STABLE path outside the versioned
# plugin cache, then point the task at it. The launcher resolves whichever drainer
# version is installed at run time, so routine plugin updates need no re-registration.
$stableDir = Join-Path $env:USERPROFILE '.claude\drainer'
if (-not (Test-Path $stableDir)) { New-Item -ItemType Directory -Path $stableDir -Force | Out-Null }
$launcher = Join-Path $stableDir 'launch-drainer.py'
Copy-Item -Path (Join-Path $scriptDir 'launch-drainer.py') -Destination $launcher -Force

# Find pythonw.exe with full path (Task Scheduler doesn't inherit user PATH)
$pythonw = & (Join-Path $scriptDir 'Find-Python.ps1') -Executable 'pythonw'

# pythonw (no console) so the recurring cycle runs silently in the background -- no black window flash
# every interval. Workers launch as headless background sessions, so nothing needs a console.
$action = New-ScheduledTaskAction -Execute $pythonw `
    -Argument ('"' + $launcher + '" --mode poller --repo "' + $RepoDir + '"')

$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes)

$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -MultipleInstances IgnoreNew

# Run as Russell's interactive logon. Background worker sessions need no desktop; the interactive
# logon is what runs the task under his user profile and credentials (his Claude login, plugin
# installs, and user environment).
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal `
    -Description "Drainer continuous keeper - one poller cycle every $IntervalMinutes min." `
    -Force | Out-Null

Write-Host "Registered '$TaskName': python run-poller.py --repo $RepoDir every $IntervalMinutes min."
Write-Host "Remove with:  install-schedule.ps1 -RepoDir $RepoDir -Remove"
