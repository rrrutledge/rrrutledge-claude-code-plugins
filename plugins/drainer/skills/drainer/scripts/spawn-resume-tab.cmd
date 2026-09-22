@echo off
REM spawn-resume-tab.cmd - open ONE Windows Terminal tab RESUMING an existing Claude session.
REM Called from run-poller.py via:  subprocess.Popen(["cmd","/c", spawn-resume-tab.cmd, TITLE, CWD, SESSIONID])
REM
REM   %1 TITLE      initial tab title (the resumed Claude session renames the tab itself once it starts)
REM   %2 CWD        the session's OWN original working directory (NOT the drainer's repo) —
REM                 launch-session.ps1's -Resume branch just runs `claude --resume <id>`, so the
REM                 process's starting directory is what determines where the resumed session lands.
REM   %3 SESSIONID  the session's guid to resume (launch-session.ps1 -Resume <SESSIONID>)
set "TITLE=%~1"
set "CWD=%~2"
set "SESSIONID=%~3"
set "WT=%LOCALAPPDATA%\Microsoft\WindowsApps\wt.exe"
REM Resolver shipped NEXT TO this file in the installed plugin (%~dp0) — same pattern as
REM spawn-tab.cmd: never launched from whatever branch a dev clone happens to sit on.
set "LAUNCHER=%~dp0launch-session.ps1"
REM Same "-w drainer-bg" window as every other drainer-spawned tab, deliberately — this is
REM unattended automation like any other drainer dispatch (not Russell invoking the manual
REM resume-sessions skill, which uses -w 0). drainer-bg is a window Russell never works in, so
REM landing a tab there never steals focus from what he's doing (see spawn-tab.cmd for the full why).
REM No -Model: `claude --resume` restores the session's own prior model as part of resuming
REM state, so passing nothing is correct (matches the manual resume-sessions skill's own
REM invocation, which also passes no -Model).
"%WT%" -w drainer-bg new-tab --title "%TITLE%" --startingDirectory "%CWD%" powershell -NoExit -File "%LAUNCHER%" -Resume "%SESSIONID%"
