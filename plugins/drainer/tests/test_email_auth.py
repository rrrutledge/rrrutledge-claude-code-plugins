"""Tests for provider_base.parse_email_auth — the envelope-authentication parser the drainer's security
screen weighs on email items. Pure function of raw Authentication-Results / Received-SPF header values
(what mail.js / gmail.js --auth expose), so no network or credentials are needed.

Run directly:
    python plugins/drainer/tests/test_email_auth.py
"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.abspath(os.path.join(HERE, ".."))
SCRIPTS = os.path.join(PLUGIN_ROOT, "skills", "drainer", "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

spec = importlib.util.spec_from_file_location("provider_base", os.path.join(SCRIPTS, "provider_base.py"))
provider_base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(provider_base)
parse = provider_base.parse_email_auth

failures = []


def check(name, got, want):
    if got == want:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}\n         got:  {got!r}\n         want: {want!r}")
        failures.append(name)


# --- authenticated, aligned mail from a real party: corroboration ---------------------------------
print("authenticated + aligned mail (DMARC pass) — corroboration")
ar = ("spf=pass (sender IP is 40.107.0.1) smtp.mailfrom=chase.com; dkim=pass (signature was verified) "
      "header.d=chase.com;dmarc=pass action=none header.from=chase.com;compauth=pass reason=100")
r = parse("alerts@chase.com", [ar], [])
check("dmarc pass", r["dmarc"], "pass")
check("dkim pass", r["dkim"], "pass")
check("spf pass", r["spf"], "pass")
check("fromDomain is what the reader sees", r["fromDomain"], "chase.com")
check("sendingDomain is the authenticated domain", r["sendingDomain"], "chase.com")
check("aligned true on DMARC pass", r["aligned"], True)


# --- spoof of a p=none domain: SPF fail, DMARC fail, misaligned sending domain --------------------
print("\nspoof of a From domain via an unrelated sender — the strong-flag fingerprint")
ar = ("mx.google.com; dkim=none; spf=fail (google.com: domain of bounce@evilmailer.net does not "
      "designate 1.2.3.4 as permitted sender) smtp.mailfrom=bounce@evilmailer.net; "
      "dmarc=fail (p=NONE sp=NONE dis=NONE) header.from=yourbank.com")
r = parse("security@yourbank.com", [ar], [])
check("dmarc fail", r["dmarc"], "fail")
check("dkim none", r["dkim"], "none")
check("spf fail", r["spf"], "fail")
check("fromDomain is the impersonated brand", r["fromDomain"], "yourbank.com")
check("sendingDomain is the real (unrelated) sender", r["sendingDomain"], "evilmailer.net")
check("aligned false — From and sending domain differ", r["aligned"], False)
check("summary names the misalignment", "misaligned with From" in r["summary"], True)


# --- lookalike / BEC: authenticates cleanly, but on a one-letter-off domain -----------------------
print("\nauthenticated lookalike domain — passes auth; the tell is the fromDomain itself")
ar = ("spf=pass smtp.mailfrom=yourcompany-corp.com; dkim=pass header.d=yourcompany-corp.com; "
      "dmarc=pass header.from=yourcompany-corp.com")
r = parse("ceo@yourcompany-corp.com", [ar], [])
check("dmarc pass (it really does authenticate)", r["dmarc"], "pass")
check("aligned true", r["aligned"], True)
check("fromDomain surfaces the lookalike for the screen to judge", r["fromDomain"], "yourcompany-corp.com")


# --- subdomain of the org domain still reads as aligned -------------------------------------------
print("\nsubdomain sender is aligned via the registrable domain")
ar = "spf=pass smtp.mailfrom=bounce.mail.chase.com; dmarc=fail header.from=chase.com"
r = parse("no-reply@chase.com", [ar], [])
check("registrable domains match -> aligned", r["aligned"], True)


# --- Received-SPF fallback when there is no Authentication-Results ---------------------------------
print("\nReceived-SPF alone (no Authentication-Results)")
spf = ("pass (google.com: domain of bounce@example.com designates 1.2.3.4 as permitted sender) "
       "client-ip=1.2.3.4; envelope-from=<bounce@example.com>;")
r = parse("hi@example.com", [], [spf])
check("spf read from Received-SPF", r["spf"], "pass")
check("sendingDomain from envelope-from", r["sendingDomain"], "example.com")
check("dmarc absent -> None", r["dmarc"], None)


# --- multiple DKIM signatures: a single pass wins -------------------------------------------------
print("\nmultiple DKIM results — a valid signature wins")
ar = "dkim=fail header.d=a.com; dkim=pass header.d=b.com; spf=pass smtp.mailfrom=b.com; dmarc=pass header.from=b.com"
r = parse("x@b.com", [ar], [])
check("dkim pass when any signature passes", r["dkim"], "pass")


# --- no auth headers at all -> None (absence is never a signal) -----------------------------------
print("\nno auth headers at all")
check("empty lists -> None", parse("x@y.com", [], []), None)
check("None args -> None", parse("x@y.com", None, None), None)


# --- multiple Authentication-Results header instances are all considered ---------------------------
print("\nmultiple Authentication-Results instances")
r = parse("x@y.com", ["dmarc=fail header.from=y.com", "spf=pass smtp.mailfrom=z.com"], [])
check("dmarc from the first instance", r["dmarc"], "fail")
check("spf from the second instance", r["spf"], "pass")
check("sendingDomain from the second instance", r["sendingDomain"], "z.com")


print(f"\n{'FAILED: ' + ', '.join(failures) if failures else 'all checks passed'}")
sys.exit(1 if failures else 0)
