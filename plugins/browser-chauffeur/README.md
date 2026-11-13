# browser-chauffeur

**Stop wrestling with browser automation.** Just tell Claude what you need done on a website - browser-chauffeur handles the rest.

Claude becomes your personal browser chauffeur: you give directions, Claude drives. Navigate complex web apps, fill out forms, extract data, click through multi-step workflows - all through natural language. No more writing fragile Selenium scripts or fighting with selectors that break every deployment.

## Why browser-chauffeur?

- **Self-healing automation** - when something breaks (and it will), Claude doesn't give up. It reads diagnostic screenshots, figures out what changed, fixes the script, and retries. Overlays, UI updates, login redirects - handled automatically.
- **One persistent browser with your logins** - Node.js CDP scripts drive a dedicated Edge or Chrome profile that keeps every sign-in across tasks. Corporate SSO? Already solved.
- **Zero selector guesswork** - Claude inspects the live page before every action, uses semantic selectors (roles, labels, text) instead of brittle CSS classes, and verifies each step succeeded before moving on.
- **Specs that get better over time** - once a flow works, write it down as a skill spec (business rules, invariants, last-known-good selectors). Each run generates a fresh script from the spec, and when the UI changes, the fix lands in the spec.

## Prerequisites

- **Node.js** - for running browser automation scripts
- **Edge or Chrome** - any Chromium-based browser already on your machine
- **playwright-core** - installed automatically on first use; no manual setup needed

## Install

```
/plugin marketplace add rrrutledge/rrrutledge-claude-code-plugins
/plugin install browser-chauffeur@rrrutledge-plugins
```

## Usage

Just describe what you want to do on a website:

> "Use browser-chauffeur to go to [site] and [task]."

Or let Claude auto-invoke it when you describe a browser task.

## Skill trigger

Auto-invokes when you ask Claude to perform a task on a website - filling a form, clicking through a workflow, navigating a web app, or extracting information.
