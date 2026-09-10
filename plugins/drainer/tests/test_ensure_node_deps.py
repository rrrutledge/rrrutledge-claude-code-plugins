"""Tests for the poller's Node-dependency self-heal (provider_base.ensure_skill_node_deps and helpers).

A freshly rolled-out or wiped node_modules must be repaired before the first enumerate so a Node-tool
provider doesn't fail every cycle with `Cannot find module`. The two install shapes are told apart by an
adjacent setup.js: a skill with one (gmail) has its own idempotent bootstrap run; a skill without one
(ms-graph) gets `npm install` in its package.json's directory, but only when that node_modules is actually
absent. The work runs at most once per plugin copy per process.

The real install calls (run_node / _npm_install) are stubbed with recorders, so this test needs neither
node nor npm and asserts the DECISION, not the install itself.

Run directly:
    python plugins/drainer/tests/test_ensure_node_deps.py
"""
import importlib.util
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "skills", "drainer", "scripts")
sys.path.insert(0, SCRIPTS)
spec = importlib.util.spec_from_file_location("provider_base", os.path.join(SCRIPTS, "provider_base.py"))
pb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pb)

failures = []


def check(name, got, want):
    if got == want:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}\n         got:  {got!r}\n         want: {want!r}")
        failures.append(name)


def write(path, text=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def make_plugin(root, name, *, at_root_deps=False, scripts_setup=False, node_modules=False, no_deps=False):
    """Build a fake installed plugin tree and return the skill script path the adapter would resolve."""
    plugin_dir = os.path.join(root, "plugins", name)
    scripts_dir = os.path.join(plugin_dir, "skills", name, "scripts")
    script = os.path.join(scripts_dir, f"{name}.js")
    write(script, "// tool")
    deps = {} if no_deps else {"some-pkg": "^1.0.0"}
    if at_root_deps:
        write(os.path.join(plugin_dir, "package.json"), json.dumps({"dependencies": deps}))
    if scripts_setup:
        write(os.path.join(scripts_dir, "package.json"), json.dumps({"dependencies": deps}))
        write(os.path.join(scripts_dir, "setup.js"), "// bootstrap")
    if node_modules:
        write(os.path.join(plugin_dir, "node_modules", "some-pkg", "index.js"), "")
    return script, plugin_dir, scripts_dir


class Recorder:
    """Stubs run_node / _npm_install and records what would have been installed."""

    def __init__(self):
        self.setups = []
        self.npm_dirs = []

    def run_node(self, args, **kw):
        self.setups.append(args[0])
        return None

    def npm_install(self, cwd):
        self.npm_dirs.append(cwd)
        return True


def with_recorder(fn):
    rec = Recorder()
    orig_node, orig_npm, orig_seen = pb.run_node, pb._npm_install, pb._ensured_plugin_dirs
    pb.run_node, pb._npm_install, pb._ensured_plugin_dirs = rec.run_node, rec.npm_install, set()
    try:
        fn(rec)
    finally:
        pb.run_node, pb._npm_install, pb._ensured_plugin_dirs = orig_node, orig_npm, orig_seen
    return rec


# --- _plugin_dir_of: derive the plugin root from a resolved script path (dev + cache layouts) -----
print("_plugin_dir_of")
dev = os.path.join("C:", os.sep, "p", "plugins", "ms-graph", "skills", "ms-graph", "scripts", "mail.js")
check("dev sibling layout", pb._plugin_dir_of(dev, "ms-graph"),
      os.path.join("C:", os.sep, "p", "plugins", "ms-graph"))
cache = os.path.join(os.sep, "c", "plugins", "cache", "mkt", "gmail", "1.10.0",
                     "skills", "gmail", "scripts", "gmail.js")
check("versioned cache layout", pb._plugin_dir_of(cache, "gmail"),
      os.path.join(os.sep, "c", "plugins", "cache", "mkt", "gmail", "1.10.0"))
check("skill segment absent -> None", pb._plugin_dir_of(dev, "not-there"), None)


# --- _pkg_dirs_with_deps: find package.json declaring deps, skip node_modules + dep-less manifests -
print("\n_pkg_dirs_with_deps")
with tempfile.TemporaryDirectory() as tmp:
    _, plugin_dir, _ = make_plugin(tmp, "ms-graph", at_root_deps=True, node_modules=True)
    # a manifest inside node_modules must be ignored, even though it declares deps
    write(os.path.join(plugin_dir, "node_modules", "some-pkg", "package.json"),
          json.dumps({"dependencies": {"nested": "^1"}}))
    found = pb._pkg_dirs_with_deps(plugin_dir)
    check("root manifest with deps is found", plugin_dir in found, True)
    check("only the one manifest (node_modules pruned)", len(found), 1)

with tempfile.TemporaryDirectory() as tmp:
    _, plugin_dir, _ = make_plugin(tmp, "empty", at_root_deps=True, no_deps=True)
    check("a manifest with empty dependencies is skipped", pb._pkg_dirs_with_deps(plugin_dir), [])


# --- ensure_skill_node_deps: setup.js vs npm install, the missing-only guard, and dedup -----------
print("\nensure_skill_node_deps")

# gmail shape: setup.js beside the scripts package.json -> run it, never npm install
with tempfile.TemporaryDirectory() as tmp:
    script, _, scripts_dir = make_plugin(tmp, "gmail", scripts_setup=True)
    rec = with_recorder(lambda r: pb.ensure_skill_node_deps(script, "gmail"))
    check("gmail: setup.js is run", rec.setups, [os.path.join(scripts_dir, "setup.js")])
    check("gmail: npm install is NOT used", rec.npm_dirs, [])

# ms-graph shape, node_modules absent -> npm install at the plugin root
with tempfile.TemporaryDirectory() as tmp:
    script, plugin_dir, _ = make_plugin(tmp, "ms-graph", at_root_deps=True, node_modules=False)
    rec = with_recorder(lambda r: pb.ensure_skill_node_deps(script, "ms-graph"))
    check("ms-graph missing store: npm install at plugin root", rec.npm_dirs, [plugin_dir])
    check("ms-graph missing store: no setup.js run", rec.setups, [])

# ms-graph shape, node_modules already present -> the happy path installs nothing
with tempfile.TemporaryDirectory() as tmp:
    script, plugin_dir, _ = make_plugin(tmp, "ms-graph", at_root_deps=True, node_modules=True)
    rec = with_recorder(lambda r: pb.ensure_skill_node_deps(script, "ms-graph"))
    check("ms-graph present store: nothing installed", (rec.npm_dirs, rec.setups), ([], []))

# dedup: two adapters sharing one plugin copy heal it once
with tempfile.TemporaryDirectory() as tmp:
    script, plugin_dir, _ = make_plugin(tmp, "ms-graph", at_root_deps=True, node_modules=False)

    def twice(r):
        pb.ensure_skill_node_deps(script, "ms-graph")
        pb.ensure_skill_node_deps(script, "ms-graph")  # outlook-graph-junk, same plugin copy

    rec = with_recorder(twice)
    check("shared plugin copy healed once per process", rec.npm_dirs, [plugin_dir])


print(f"\n{'FAILED: ' + ', '.join(failures) if failures else 'all checks passed'}")
sys.exit(1 if failures else 0)
