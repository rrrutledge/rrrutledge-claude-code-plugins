# Extending the drainer - what each machine adds

The **engine and the providers** ship in this plugin and are generic.
A machine adds only two local things: a settings file and a `context.md`.

## 1. Settings - `.claude/drainer.local.md`
The single per-machine config (the plugin-settings pattern).
Copy `templates/drainer.local.example.md` to `.claude/drainer.local.md` and fill in: which `providers` are active (by name), per-provider config (e.g. Trello `boards`, label vocab), and `local_dir`; the template's comments describe each knob.
**You decide whether to version it** - keep it in a repo, in this project, or uncommitted.

## 2. context.md (the local brain)
Copy `templates/context.example.md` to `<local_dir>/context.md` and fill in **your** world: who you are, the systems you act in, your internal knowledge source (if any), and your tracker board.
The worker loads it at the start of every item.

It also goes into **every triage prompt**, where it is the largest fixed cost - so a `## ` section that only bears on one kind of item can declare a trigger and sit out the cycles it has nothing to say about (see "Triggers" below).

Gate only sections that are unambiguously about one source.
Most of the file is world knowledge that informs classifying anything, and it should stay untriggered: a section wrongly withheld doesn't announce itself, it shows up as triage quietly deciding without knowing something.

## Triggers
A `context.md` section, or a provider's `## AUTO-HANDLE` section (`docs/writing-a-provider.md`, "Standing rules"), can carry a `**Trigger:**` line that sends it to triage only when something in the current batch could match:

```markdown
## Fireflies recap emails
**Trigger:** `from=fred@fireflies\.ai; subject=^Your meeting recap`
```

Semicolon-separated `field=regex` pairs, ANDed, matched case-insensitively against the items in the current batch: `from`, `subject`, `preview`, and, for a `context.md` section, `source` for the provider name.
An AUTO-HANDLE trigger matches only that provider's items.
A section with no trigger, or one whose trigger can't be parsed, is always sent: a bad trigger costs a wasted prompt, never a rule that silently stops applying.

## Providers
The providers in `providers/` cover the shipped sources (email, Teams, Slack, Trello, Zoom, and more); enable the ones you want in `drainer.local.md` and give any config they need.
They're generic: you sign in as yourself.
To add a source that isn't there yet, including one specific to this machine, write a new provider - see `docs/writing-a-provider.md`.

## Credentials
OS credential store (Windows Credential Manager / Keychain) or environment variables (e.g. `TRELLO_API_KEY`/`TRELLO_TOKEN`).
**Never** in the plugin, a repo, or the settings file.
Providers fetch them at runtime.

## Sibling skills it uses
`message-draft` + `document-authoring` (drafting & voice), `trello` (Trello reads and writes) and `trello-outreach` (the outreach funnel and tracker cards), and `browser-chauffeur` (any step a provider or worker drives in a browser).
All in this marketplace.

## A new machine, start to finish
1. `claude plugin install drainer@rrrutledge-claude-code-plugins`.
2. `cp templates/drainer.local.example.md .claude/drainer.local.md`; enable the providers you want and fill in their config (set `local_dir`).
3. Create `<local_dir>/context.md` from `templates/context.example.md`.
4. Wire credentials into your OS store; ensure the sibling skills are installed.
5. Run a source by hand (`run-poller.py --dry-run`, then `--once`) until it's trustworthy.
6. Register the scheduled tasks with `scripts/install-schedule.ps1` and `scripts/install-digest-schedule.ps1`.
