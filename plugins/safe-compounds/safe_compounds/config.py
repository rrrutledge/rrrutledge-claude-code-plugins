"""Consumer configuration.

The plugin ships with no organization-specific data. A consumer supplies their
own trusted domains, MCP servers, commands, and script directories via a
machine-local JSON file (default ~/.claude/safe-compounds-config.json, or the
path in SAFE_COMPOUNDS_CONFIG_JSON).

Example ~/.claude/safe-compounds-config.json:
    {
      "trusted_commands": ["mycli"],
      "curl_domains": ["atlassian.net", "mycorp.sharepoint.com"],
      "mcp_blanket_servers": ["plugin_product-management_atlassian"],
      "mcp_blanket_tools": ["mcp__claude_ai_Gmail__apply_sensitive_message_label"],
      "trusted_script_dirs": ["my-plugins/"],
      "learned_sync_exclude": ["my-internal-cli"],
      "workflow_blanket_names": ["code-review"],
      "trusted_destination_dirs": ["~/OneDrive/Deliverables"]
    }

Every value here is a command *name* or path/domain string - a name-level
allowlist, not a safety verdict. (The hook decides "safe" per command at
runtime; this file just feeds the lists those decisions draw on.)

All keys are optional; defaults are empty, which means "trust nothing extra"
(curl is then limited to localhost and read-only GETs, no MCP server is
blanket-allowed, etc.).

`trusted_commands` is your private/company trust - it is never promoted to the
shared PR. `learned_sync_exclude` blocklists additional command names that the
AI might learn but should never be shared publicly (e.g. an internal tool name).

`workflow_blanket_names` auto-allows Workflow tool calls by their saved
workflow name (`tool_input.name` - a built-in workflow or one saved under
`.claude/workflows/`). An inline/dynamic `script` payload is never
blanket-allowed via this list, however it names itself, since nothing in
that text is proof of what it does. Instead, an inline script is sent to
an AI safety check (`scripts.ask_ai_about_workflow_script`) that reads the
prompts it hands to `agent()` calls - the only thing such a script can
actually do - every time it runs, since the text differs run to run.

`mcp_blanket_tools` auto-allows individual MCP tool calls by their full
name (`mcp__<server>__<operation>`), regardless of what verb the operation
name starts with. Use this when one specific tool on a server is safe to
run unattended but the server as a whole isn't (e.g. a Gmail label/trash
tool whose effect is private and reversible, without blanket-trusting
every other Gmail tool too).

`trusted_destination_dirs` adds *extra* dirs `cp`/`mv`/`ln` may write to
outside the repo, on top of the built-in default (`~/Downloads` - see
`paths.TRUSTED_DESTINATION_DIRS`). Writing outside the repo is otherwise
treated as elevated risk (an out-of-repo write can silently overwrite an
existing file), so beyond the built-in default, only paths you list here are
trusted as destinations.
"""
import json
import os

_DEFAULTS = {
    "trusted_commands": [],
    "curl_domains": [],
    "mcp_blanket_servers": [],
    "mcp_blanket_tools": [],
    "trusted_script_dirs": [],
    "learned_sync_exclude": [],
    "workflow_blanket_names": [],
    "trusted_destination_dirs": [],
}

_CONFIG = None


def _path():
    return os.environ.get(
        'SAFE_COMPOUNDS_CONFIG_JSON',
        os.path.expanduser('~/.claude/safe-compounds-config.json'),
    )


def get_config():
    """Return the merged config dict (cached for this process)."""
    global _CONFIG
    if _CONFIG is None:
        data = dict(_DEFAULTS)
        try:
            with open(_path(), encoding='utf-8') as f:
                loaded = json.load(f)
            for key in _DEFAULTS:
                if key in loaded:
                    data[key] = loaded[key]
        except Exception:
            pass
        _CONFIG = data
    return _CONFIG


def reset():
    """Clear the cached config (used by tests)."""
    global _CONFIG
    _CONFIG = None
