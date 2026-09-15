# Extending the drainer - what each machine adds

The **engine and the providers** ship in this plugin and are generic and identity-free.
A machine adds only two local things: a settings file and a `context.md`.
Credentials always live in your OS store / env, never in any repo.

## 1. Settings - `.claude/drainer.local.md`
The single per-machine config (the plugin-settings pattern).
Copy `templates/drainer.local.example.md` to `.claude/drainer.local.md` and fill in: which `providers` are active (by name), per-provider config (e.g. Trello `boards`, label vocab), `local_dir`, and the harvest interval.
**You decide whether to version it** - keep it in a repo, in this project, or uncommitted.

## 2. context.md (the local brain)
Copy `templates/context.example.md` to `<local_dir>/context.md` and fill in **your** world: who you are, the systems you act in, your internal knowledge source (if any), your tracker board, and the standing behavioral rules.
The worker loads it at the start of every item.

It also goes into **every triage prompt**, where it is the largest fixed cost - so a `## ` section that only bears on one kind of item can declare a trigger and sit out the cycles it has nothing to say about:

```markdown
## Fireflies recap emails
**Trigger:** `from=fred@fireflies\.ai; subject=^Your meeting recap`
```

Semicolon-separated `field=regex` pairs, ANDed, matched case-insensitively against the items in the current batch - `from`, `subject`, `preview`, or `source` for the provider name.
The section is sent only when something matches.

Gate only sections that are unambiguously about one source.
Most of the file is world knowledge that informs classifying anything, and it should stay untriggered: a section wrongly withheld doesn't announce itself, it shows up as triage quietly deciding without knowing something.
A section with no trigger, or one whose trigger can't be parsed, is always sent.

## Providers
The providers in `providers/` cover Outlook (web), Teams (web), and Trello outreach; enable the ones you want in `drainer.local.md` and give any config they need (Trello board id; a Slack workspace subdomain).
They're generic: you sign in as yourself.
To add a source that isn't there yet, write a new provider - see `docs/writing-a-provider.md`.

### Machine-local providers - `<local_dir>/providers/`
A source specific to one machine - a work-only internal system, a personal-only account - lives in **`<local_dir>/providers/`** next to your `context.md`, so it stays out of the shared plugin (which is generic and identity-free) and never follows the plugin to another machine.
Drop its two files (`<name>-adapter.py` + `<name>-provider.md`) there and enable `<name>` in `drainer.local.md` exactly like a shipped provider.
The engine resolves each enabled provider by searching the plugin's `providers/` first, then `<local_dir>/providers/`, so local and shipped providers run through the same loop identically.

## Credentials
OS credential store (Windows Credential Manager / Keychain) or environment variables (e.g. `TRELLO_KEY`/`TRELLO_TOKEN`).
**Never** in the plugin or the settings file.
Providers fetch them at runtime.

## Sibling skills it uses
`message-draft` + `document-authoring` (drafting & voice), `trello-outreach` (Trello reads/mutations & tracker cards), and `browser-chauffeur` (the Outlook/Teams browser providers).
All in this marketplace.

## A new machine, start to finish
1. `claude plugin install drainer@rrrutledge-claude-code-plugins`.
2. `cp templates/drainer.local.example.md .claude/drainer.local.md`; enable the providers you want and fill in their config (set `local_dir`).
3. Create `<local_dir>/context.md` from `templates/context.example.md`.
4. Wire credentials into your OS store; ensure the sibling skills are installed.
5. Add scheduling glue (overlap lock, one interval) for your OS.
6. Run a source by hand until trustworthy, then schedule a single harvest of all sources.
