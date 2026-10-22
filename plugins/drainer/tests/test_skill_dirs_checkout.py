"""Tests for provider_base.resolve_skill_dirs handing out the local plugin clone's skill dirs in place of
installed-cache paths: the cache is blocked to the Bash hook, so a worker told a cache path burns retries
before finding the clone.

Run directly:
    python plugins/drainer/tests/test_skill_dirs_checkout.py
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.abspath(os.path.join(HERE, "..", "skills", "drainer", "scripts"))

sys.path.insert(0, SCRIPTS)
import provider_base  # noqa: E402

failures = []


def check(name, got, want):
    if got == want:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}: got {got!r}, want {want!r}")
        failures.append(name)


def touch(path, text=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def build(home, with_node_modules):
    """A fake home with an installed cache (plugins `alpha` and `beta`, beta needing root deps) and a clone
    at ~/Dev/org/repo whose origin is the marketplace repo."""
    cache_plugins = os.path.join(home, ".claude", "plugins")
    for name in ("alpha", "beta"):
        touch(os.path.join(cache_plugins, "cache", "mkt", name, "1.0.0", "skills", name, "SKILL.md"))
    repo = os.path.join(home, "Dev", "org", "repo")
    touch(os.path.join(repo, ".git", "config"),
          '[remote "origin"]\n\turl = https://github.com/rrrutledge/rrrutledge-claude-code-plugins.git\n')
    for name in ("alpha", "beta"):
        touch(os.path.join(repo, "plugins", name, "skills", name, "SKILL.md"))
    touch(os.path.join(repo, "plugins", "beta", "package.json"), json.dumps({"dependencies": {"x": "1"}}))
    if with_node_modules:
        os.makedirs(os.path.join(repo, "plugins", "beta", "node_modules"))
    return cache_plugins, repo


def resolve(home, with_node_modules):
    cache_plugins, repo = build(home, with_node_modules)
    os.environ["USERPROFILE"] = home
    os.environ["HOME"] = home
    start = os.path.join(cache_plugins, "cache", "mkt", "alpha", "1.0.0", "skills", "alpha", "x.py")
    return provider_base.resolve_skill_dirs(start), repo


with tempfile.TemporaryDirectory() as home:
    dirs, repo = resolve(home, with_node_modules=False)
    check("plugin without deps resolves to the clone", dirs["alpha"],
          os.path.join(repo, "plugins", "alpha", "skills", "alpha"))
    check("plugin whose deps are missing in the clone keeps the cache path", "cache" in dirs["beta"], True)

with tempfile.TemporaryDirectory() as home:
    dirs, repo = resolve(home, with_node_modules=True)
    check("plugin with deps installed in the clone resolves to the clone", dirs["beta"],
          os.path.join(repo, "plugins", "beta", "skills", "beta"))

with tempfile.TemporaryDirectory() as home:
    cache_plugins, repo = build(home, True)
    touch(os.path.join(repo, ".git", "config"), '[remote "origin"]\n\turl = https://github.com/other/thing.git\n')
    os.environ["USERPROFILE"] = home
    os.environ["HOME"] = home
    check("a clone of a different repo is not used", provider_base.find_plugins_checkout(), None)

if failures:
    print(f"\n{len(failures)} failed")
    sys.exit(1)
print("\nall passed")
