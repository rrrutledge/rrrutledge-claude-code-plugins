"""Classify Workflow tool calls as auto-allowable.

Two independent paths can auto-allow a call:

1. A *saved* workflow (`tool_input.name` referencing a fixed, previously
   authored script - built-in or from `.claude/workflows/`) is blanket-trusted
   the same way `trusted_script_dirs` trusts a directory's contents rather than
   re-inspecting every run, via `workflow_blanket_names` in config. An
   inline/dynamic script's own self-declared `meta.name` is never consulted for
   this path - nothing in that text is proof of what the script actually does.

2. An inline/dynamic `script` (or a `scriptPath` file) is read and sent to
   `scripts.ask_ai_about_workflow_script`, mirroring the existing node/python
   file-script AI check. A workflow script has no filesystem/network access of
   its own - its only real-world effect is the prompts it hands to `agent()`,
   and once the call is approved every subagent it spawns runs unattended with
   full tool access. So the check reads those prompts and applies the same
   read-only-or-reversible standard used everywhere else in this hook.
"""
from . import config
from .log import log_debug
from .paths import read_script_file, resolve_against_cwd
from .scripts import ask_ai_about_workflow_script

# Mirrors scripts.py's block-reason handshake: an AI verdict of DANGEROUS turns
# the silent prompt into an actionable block. Reset at the start of every hook
# invocation by hook.py.
_last_block_reason = None


def reset_block_reason():
    global _last_block_reason
    _last_block_reason = None


def get_block_reason():
    return _last_block_reason


def _record_block(reason):
    global _last_block_reason
    detail = f': {reason}' if reason else ''
    _last_block_reason = (
        f'BLOCKED: this Workflow script was judged unsafe to auto-run{detail}. '
        'Rewrite the agent() prompts so they only read, investigate, and return data - '
        'no writes, commits, pushes, posts, or sends. If it is legitimately privileged, '
        'save it under .claude/workflows/ and add its name to the safe-compounds '
        '"workflow_blanket_names" config instead of broadening the inline script.'
    )


def classify_workflow_tool(tool_input):
    """Return True to auto-allow a Workflow tool call, False to prompt."""
    name = tool_input.get('name')
    if name:
        blanket = config.get_config().get('workflow_blanket_names', [])
        if name in blanket:
            log_debug(f"Workflow '{name}': blanket-approved (configured)")
            return True
        log_debug(f"Workflow '{name}': not in blanket list, prompting")
        return False

    script = tool_input.get('script')
    script_path = tool_input.get('scriptPath')
    if not script and script_path:
        script = read_script_file(script_path)
        if script is None:
            log_debug(f"Workflow: scriptPath not readable ({resolve_against_cwd(script_path)}), prompting")
            return False

    if not script:
        log_debug("Workflow: no saved name, inline script, or scriptPath, prompting")
        return False

    verdict, reason = ask_ai_about_workflow_script(script)
    if verdict is False:
        _record_block(reason)
        log_debug(f"Workflow inline script: AI judged unsafe: {reason}")
        return False
    if verdict is True:
        log_debug("Workflow inline script: AI judged safe")
        return True
    log_debug("Workflow inline script: AI could not decide, prompting")
    return False
