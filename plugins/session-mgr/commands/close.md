---
description: Close this session in one shot - a background session stops itself, and a terminal session also closes its tab, no double "exit"
---

Close this Claude Code session the same way a drainer worker self-closes when it's done.

Run, via the Bash tool:

```bash
python "${CLAUDE_PLUGIN_ROOT}/skills/resume-sessions/scripts/end-session.py"
```

This fires the plugin's `SessionEnd` hooks with the real payload (so the live-session registry is cleaned up correctly), then ends the session.
A background session (a `claude --bg` session, the kind every automated launch creates) ends with `claude stop`, and its conversation stays resumable.
A session Russell started by hand in a terminal ends by killing its hosting tab, which needs `CLAUDE_HOST_PID` from his PowerShell profile.
When the script can identify neither kind, it prints why and exits without closing anything - in that case tell the user and let them close the session normally with `exit`.

Do not narrate or summarize before running this - there's no session left to report back to once it fires.
