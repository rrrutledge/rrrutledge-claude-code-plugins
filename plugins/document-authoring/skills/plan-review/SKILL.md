---
name: plan-review
description: Present a plan or design spec for Russell to review as an editable HTML page opened locally, not as chat prose or a markdown file. Use whenever you have a genuine plan, design, or spec Russell will read and steer - an implementation plan, an architecture or design proposal, a spec with open decisions, a phased rollout. The page is a `.tmp` working document (never committed), built from a fixed shell by `plan-page.py` so every plan reviews in the same colored, editable format.
---

# plan-review

Russell reviews a plan far more easily as an editable HTML page than as chat prose or a markdown file:
color and structure separate the parts, callouts flag decisions and out-of-scope notes, pills mark each file as add/change/keep, and he can edit inline, strike a line, add a note, and download his edited copy.
So a plan or design spec for him to review is presented as an editable HTML page opened locally with `start <path>`, every time.

## When this fires

Present an editable HTML page whenever you have produced a **plan, design, or spec Russell will review and steer** before you act on it:
an implementation plan, an architecture or design proposal, a spec with open decisions for him to settle, a phased rollout, a "here's how I'll approach this" he needs to sign off on.

This is the default form for that artifact - reach for it without being asked.

## When it does not

- A direct chat answer, a quick list, a status update, or a short recommendation stays in chat.
  The page is for a plan substantial enough that Russell will read it carefully and edit it.
- A deliverable Russell asked for in another form - a slide deck, a Word doc, a Confluence page, a PDF - goes in that form.
- **Markdown that will become a real PR** takes the draft-PR route instead (open it as a GitHub draft PR, review on the Files Changed tab).
  A plan or spec is a `.tmp` working document that is never committed, so it is a distinct case: it gets this editable HTML page.
  The line is destination - shipped markdown becomes a PR; a working plan-for-review becomes an editable page.

This is the "HTML just for me to read - write a file and `start` it, not an Artifact" case from Russell's global rules, specialized to plans: a plan for review gets this editable template specifically.

## How to build the page

1. Write the plan's **body** - the sections only, not the title or the shell - to a fragment file in `.tmp/`, using the class vocabulary below.
   Start at `<h2>` for each section; the generator supplies the `<h1>`, the sub-lines, the toolbar, the autosave, and the download/reset controls.
2. Run the generator (its path resolves from this skill's directory):

   ```bash
   python plugins/document-authoring/skills/plan-review/plan-page.py \
     --title "What this plan is" \
     --out .tmp/plan-<slug>.html \
     --body .tmp/plan-<slug>-body.html \
     --subtitle "Implementation plan - draft PR only" \
     --subtitle "Verified against <thing> on 2026-09-22"
   ```

   `--subtitle` is optional and repeatable; each becomes a muted line under the title, and HTML is allowed in it (e.g. `<code>branch-name</code>`).
   The generator keys the browser autosave to the output filename and names the download after it, so those never drift.
3. Open it for Russell: `start .tmp/plan-<slug>.html`.

Keep the page in `.tmp/` - it is a working document, never committed and never PR'd, the same as any plan or spec.

## Class vocabulary for the body

Write the body as plain HTML sections using these classes; the shell styles them into the format Russell reviews against.

- **Sections**: `<h2>Section title</h2>`, then `<p>`, `<ul>`/`<ol>`, `<h3>Sub-heading</h3>` (h3 renders in the accent color).
- **Callouts** - a colored box with an uppercase label:
  - `<div class="callout ok"><span class="tag">Bottom line</span><p>...</p></div>` - green, for a settled point or the headline conclusion.
  - `<div class="callout decide"><span class="tag">Needs your call</span><p>...</p></div>` - amber, for an open decision Russell settles.
  - `<div class="callout out"><span class="tag">Out of scope</span><p>...</p></div>` - orange, for a follow-up or something deliberately left out.
- **Numbered decisions** - `<ol class="decisions"><li>...</li></ol>` renders each item with an accent circle badge; use it inside a `decide` callout for a list of open decisions.
- **File-change blocks** - one per file the plan touches:

  ```html
  <div class="file">
    <h3>path/to/file.py <span class="pill change">change</span></h3>
    <ul><li>What changes and why.</li></ul>
  </div>
  ```

  The pill is `<span class="pill remove">remove</span>`, `<span class="pill change">change</span>`, or `<span class="pill keep">keep</span>`.
- **Inline emphasis on a change**: `<span class="rm">drop</span>` (red) and `<span class="add">add</span>` (green) for a word inside a sentence.
- **Code**: inline `<code>...</code>`; a block is `<pre><code>...</code></pre>`.
- **Footer**: the generator appends the edit hint itself - do not add one.

Escape `<`, `>`, and `&` inside `<code>`/`<pre>` (`&lt;`, `&gt;`, `&amp;`) so literal angle brackets show.

## Reference

`plan-page.py` in this directory is the generator; its docstring covers the arguments.
The shell it emits is a single self-contained HTML file - no network, no dependencies - so the page opens and edits offline.
