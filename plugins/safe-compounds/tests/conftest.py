import json
import os
import sys

import pytest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PLUGIN_DIR = os.path.dirname(TESTS_DIR)

for path in (TESTS_DIR, PLUGIN_DIR):
    if path not in sys.path:
        sys.path.insert(0, path)


@pytest.fixture(scope="session")
def trusted_json(tmp_path_factory):
    """Write the base trusted-command set (from trust.py) to a temp file and
    return its path. Tests pin the trusted set to this via
    SAFE_COMPOUNDS_TRUSTED_JSON so they are hermetic and always match the source -
    no committed snapshot to drift out of sync."""
    from safe_compounds.trust import SHELL_BUILTINS, TRUSTED_COMMANDS
    path = tmp_path_factory.mktemp("fixtures") / "trusted_commands.json"
    path.write_text(json.dumps(sorted(SHELL_BUILTINS | TRUSTED_COMMANDS)), encoding="utf-8")
    return str(path)
