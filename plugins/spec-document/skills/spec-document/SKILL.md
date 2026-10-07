---
name: spec-document
description: Write a spec for something Russell proposes to build, in his standard five-section format - Business value, Problem, What we'll do, Visuals, Detailed notes - as an HTML page exported to PDF. Use when Russell asks for a spec, proposal, or design write-up of a thing to build, or when a task needs one produced.
---

# Spec document

Russell's specs follow one format.
Every spec starts as a copy of [`assets/spec-template.html`](assets/spec-template.html) and ships as a PDF exported from that page.

## The format

A spec opens with its title and goes straight into five sections, in this order, with these exact headings.
A finished spec runs one to two printed pages.

1. **Business value** - who will use this, and the end goal they're trying to accomplish.
   This section describes the outcome the user is after and the value they get when they reach it.
2. **Problem** - what gets in the way of them reaching that end goal today.
   This section describes the obstacle concretely: what they run into and what it costs them.
3. **What we'll do** - how we'll help them overcome that problem so they can reach the end goal.
   This section describes the solution as what they'll be able to do, and leaves implementation detail to Detailed notes.
4. **Visuals** - diagrams, mockups, and screenshots of what it will look like.
   Each visual sits full width with a one-line caption.
5. **Detailed notes** - everything a builder or reviewer needs beyond the summary: scope in and out, options considered, dependencies, rollout phases, risks, and open questions.
   This section carries whatever length the spec has.

The three opening sections form one chain: the user's end goal, the obstacle in its way, and how we clear that obstacle.
Write each section so it answers the one before it.

## Workflow

1. Copy the template to `<repo>/.tmp/spec-<slug>/spec.html` and put any image files beside it.
2. Replace the title and each section's italic `<p class="prompt">` with the real content.
   The template's header comment lists the class vocabulary for figures, side-by-side visuals, notes, and tables.
3. Build visuals as Mermaid diagrams (`<pre class="mermaid">`), inline SVG mockups, or screenshot images, each inside a `<figure>` with a `<figcaption>`.
4. Export the PDF:
   ```bash
   python <skill-dir>/scripts/export_pdf.py <repo>/.tmp/spec-<slug>/spec.html
   ```
   The script prints the PDF path beside the HTML, using headless Chrome or Edge with enough render time for Mermaid's CDN script.
5. Have a subagent read the PDF and report page count and any clipped or overflowing content, then fix and re-export until it renders clean.
6. Open the PDF for Russell with `start <path>` and iterate on his feedback, re-exporting and re-sharing on every round.
