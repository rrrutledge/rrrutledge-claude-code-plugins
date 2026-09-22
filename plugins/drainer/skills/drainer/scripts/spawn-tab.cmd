@echo off
REM spawn-tab.cmd - open ONE Windows Terminal tab running a fresh interactive Claude worker session.
REM Called from run-poller.py via:  subprocess.Popen(["cmd","/c", spawn-tab.cmd, TITLE, REPO, PROMPTFILE, MODEL, SUMMARYFILE])
REM A .cmd shim is used (not a direct Popen of wt.exe) because wt.exe tokenization breaks on quoted
REM paths passed through a Python subprocess; cmd escaping handles it reliably.
REM
REM   %1 TITLE        initial tab title (the worker's Claude session renames the tab itself once it starts)
REM   %2 REPO         starting directory (the drainer project)
REM   %3 PROMPTFILE   file holding the worker's full instructions (launch-session.ps1 -PromptFile)
REM   %4 MODEL        explicit model id for the worker (so it doesn't inherit the session default)
REM   %5 SUMMARYFILE  OPTIONAL file with a one-line item summary; launch-session.ps1 leads the seed with it
REM                   so the worker's Claude session names the tab descriptively. The digest spawns with
REM                   NO 5th arg.
set "TITLE=%~1"
set "REPO=%~2"
set "PFILE=%~3"
set "MODEL=%~4"
set "SFILE=%~5"
set "WT=%LOCALAPPDATA%\Microsoft\WindowsApps\wt.exe"
REM Run the resolver shipped NEXT TO this file in the installed plugin (%~dp0 = this
REM script's own dir), NOT a working-clone path. run-poller.py invokes this .cmd from
REM the version-pinned plugin cache, so %~dp0launch-session.ps1 is the pinned resolver;
REM it forwards to the newest installed real launcher (in the session-mgr plugin). A
REM worker is never launched from whatever branch the dev clone sits on.
set "LAUNCHER=%~dp0launch-session.ps1"
REM -w drainer-bg: collect worker tabs in a single, consistently-named "drainer-bg" window Russell
REM never works in (he reads finished tabs on the Claude app/website, not by hunting terminal tabs),
REM rather than -w 0 (most-recently-used), which is unpredictable when the scheduled task creates the
REM window. Targeting a window that is never his foreground is what stops the spawn from stealing focus:
REM WT switches the active tab only of the window it activates, and adding a tab to a non-foreground
REM window doesn't activate it, so his cursor stays on whatever he's doing (browser, slides, voice).
REM No --no-focus: it can't help here and breaks the launch. Placed before new-tab (either before or
REM after -w) WT 1.24 rejects it and swallows the tab; placed after new-tab it launches but has no focus
REM effect (verified live). The non-foreground target is what does the work, so --no-focus is omitted.
REM (The very first spawn must CREATE drainer-bg; from the headless poller, which has no recent user
REM input, Windows denies it foreground rights, so even that creation doesn't grab focus.)
REM No --suppressApplicationTitle: the worker's Claude session sets the tab title itself, which is what
REM shows its "needs attention" star when it yields to Russell. We steer that self-chosen title to be
REM descriptive by leading the seed with the item summary (launch-session.ps1 -SummaryFile).
REM
REM Only pass -SummaryFile when a 5th arg was actually given. An EMPTY "%SFILE%" gets eaten by wt's
REM tokenizer, leaving a dangling "-SummaryFile" with no value, which makes launch-session.ps1 fail with
REM "Missing an argument for parameter 'SummaryFile'". The digest spawns with no summary, so it must omit
REM the flag entirely rather than pass it empty.
REM
REM The worker's PowerShell host loads the user's $PROFILE like any other tab, which sets
REM $env:CLAUDE_HOST_PID (self-close) and $env:BROWSER_CHAUFFEUR_OWNER_PID (browser tab ownership)
REM automatically, with no per-launcher wiring. Safe for this unattended path — it's an ordinary
REM PowerShell host startup, no different from an interactively-opened tab.
if "%SFILE%"=="" (
  "%WT%" -w drainer-bg new-tab --title "%TITLE%" --startingDirectory "%REPO%" powershell -NoExit -File "%LAUNCHER%" -PromptFile "%PFILE%" -Model "%MODEL%"
) else (
  "%WT%" -w drainer-bg new-tab --title "%TITLE%" --startingDirectory "%REPO%" powershell -NoExit -File "%LAUNCHER%" -PromptFile "%PFILE%" -Model "%MODEL%" -SummaryFile "%SFILE%"
)
