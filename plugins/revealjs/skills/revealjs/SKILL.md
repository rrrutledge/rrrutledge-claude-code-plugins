---
name: revealjs
description: Create polished, professional reveal.js presentations. Use when the user asks to create slides, a presentation, a deck, or a slideshow. Supports themes, multi-column layouts, code highlighting, animations, speaker notes, and custom styling. Generates HTML + CSS with no build step required.
---

# Reveal.js Presentations

Create HTML presentations using reveal.js. No build step required - just open the HTML in a browser.

## Design Principles

**CRITICAL**: Before creating any presentation, analyze the content and choose appropriate design elements:

1. **Consider the subject matter**: What tone, industry, or mood does it suggest?
2. **Check for branding**: If the user mentions a company/organization, consider their brand colors
3. **Match palette to content**: Select colors that reflect the subject
4. **State your approach**: Explain your design choices before writing code

**Requirements**:
- State your content-informed design approach BEFORE writing code
- Use web-safe fonts or Google Fonts via `@import` in CSS
- **Always use `pt` (points) for font sizes** - slides are fixed-size, so `pt` is predictable (like PowerPoint). Never use `em`, `rem`, or `px` for font sizes.
- When a slide has less content, scale text up with `.text-lg`, `.text-xl`, `.text-2xl`, `.text-3xl`, `.text-4xl` classes (defined in base-styles.css)

### Slide Content Principles

**Diverse presentation is key.** Even when slides have similar content types, vary the visual presentation:
- Use **different layouts** across slides: columns on one, stacked containers on another, styled cards on a third
- Don't repeat the same layout pattern on consecutive slides
- Use inline CSS grid for multi-column layouts (each slide's needs vary)
- When a visual pattern repeats 3+ times, create a CSS class instead of inline styles
- **All visible text must be inside a text element** (`<p>`, `<li>`, or `<h1>`–`<h6>`) - never bare text in `<div>` or `<span>`

### Images - Required on Every Slide

**Every slide must have a visual element** - an image, logo, or icon-based illustration. Slides without any imagery feel flat and lose the audience.

**How to find images:** Use free, no-attribution-required sources:
- **Unsplash** (photos): `https://source.unsplash.com/800x600/?{keyword}` - replace `{keyword}` with a relevant noun or concept
- **Font Awesome icons** (already loaded via CDN in the scaffold) - use for icon-based decorative elements when a photo would be too busy

**Image selection strategy - riff on a key noun or verb:**
- Pick one key word from the slide's title or main idea (e.g., "timeline" → clock/calendar, "community" → people/crowd, "speaker" → microphone/stage)
- The connection can be loose - the audience will get it and it makes the slide more memorable
- Avoid purely literal matches; a creative tangent is more interesting

**Layout patterns - vary across slides:**

1. **Split layout** (most common): image fills one half, content the other.
   ```html
   <div style="display:grid;grid-template-columns:1fr 1fr;gap:0;height:100%;margin:-40px -60px;padding:0;">
     <div style="background:url('https://source.unsplash.com/800x600/?keyword') center/cover no-repeat;"></div>
     <div style="padding:40px 50px;display:flex;flex-direction:column;justify-content:center;">
       <!-- slide content here -->
     </div>
   </div>
   ```

2. **Watermark / full-bleed background**: image behind content with a dark overlay for readability.
   ```html
   <section style="background:linear-gradient(rgba(10,20,40,0.78),rgba(10,20,40,0.78)),url('https://source.unsplash.com/1280x720/?keyword') center/cover no-repeat;">
   ```

3. **Corner / inset image**: image in a rounded card in one corner, content fills the rest. Good for slides with a lot of text.
   ```html
   <img src="https://source.unsplash.com/400x300/?keyword" style="float:right;width:38%;border-radius:12px;margin:0 0 16px 24px;object-fit:cover;">
   ```

4. **Icon-as-hero**: when a photo would be too busy, use a large Font Awesome icon as a decorative visual.
   ```html
   <i class="fa-solid fa-microphone" style="font-size:120pt;color:var(--primary-color);opacity:0.15;position:absolute;right:60px;bottom:40px;"></i>
   ```

**Company branding - if the organization has a logo:**
- Place it on the title or closing slide (not every slide).
- If the logo PNG has a white background (not transparent), wrap it in a pill with `background:rgba(255,255,255,0.30)` - never CSS `opacity` on the container, which dims the image inside.
- For full-bleed photo backgrounds, ~50% overlay opacity is a good starting point; above 70% kills the photo's energy.

**Sourcing photos from internal document systems (e.g. Confluence):**
- Navigate to the relevant page via browser-chauffeur, scroll through to trigger lazy-loading, then capture photos with `page.locator('img').screenshot()` on each large image element.
- Do NOT use `page.evaluate(fetch(...))` - many internal CDN URLs carry short-lived auth tokens that block cross-origin fetch (CORS). Screenshot the elements directly instead.

## Workflow

### Step 1: Plan the Structure

Based on the user's content, determine how many slides are needed, which should be section dividers, and where to use vertical stacks.

### Step 2: Generate the Scaffold

Use the `create-presentation.js` script in the `scripts/` directory next to this SKILL.md:

```bash
node <path-to-skill>/scripts/create-presentation.js --structure 1,1,d,3,1,d,1 --title "My Presentation" --output presentation.html
```

**Options:**
- `--slides N` - N horizontal slides (simple mode)
- `--structure <list>` - Mixed layout: `1` = single slide, `N` = vertical stack, `d` = section divider
- `--output <file>`, `--title <text>`, `--styles <file>`

The scaffold auto-copies `base-styles.css` as `styles.css` into the output directory.

### Step 3: Customize the CSS

Edit `styles.css` - customize the CSS variables (especially colors and fonts) for your theme. The base file uses `--background-color`, `--primary-color`, `--text-color`, etc.

For Google Fonts, add `@import` at the top of CSS and set `--heading-font` / `--body-font`.

### Step 4: Fill in the HTML Content

**Use the Edit tool incrementally** - one or a few slides at a time. The scaffold generates unique placeholder text per slide (e.g., `Slide 2 Title Here`) for easy targeting.

Key patterns:
- Every `<section>` should have a unique `id`
- Use `class="section-divider"` for centered section title slides
- Wrap main content in `<div class="content">` for consistent spacing
- Use `<div class="footnote">` for attribution at bottom

### Step 5: Check for Content Overflow - REQUIRED before commit

**Never skip this.** Run the overflow checker and fix every flagged slide before moving on.

```bash
node <path-to-skill>/scripts/check-overflow.js presentation.html
```

### Step 6: Visual Review with Screenshots - REQUIRED before commit

**Never skip this.** Take a screenshot of every slide and read each one with the Read tool. Do not declare the presentation done until you have visually confirmed every slide looks correct.

```bash
npx decktape reveal "presentation.html?export" output.pdf \
  --screenshots \
  --screenshots-directory screenshots/
```

The `?export` parameter disables chart animations for cleaner rendering.

On each screenshot, verify:
- No content is cut off at the bottom or sides of the slide
- No text overlaps other text or images
- All images load (no broken-image icons)
- Multi-column layouts aren't collapsing
- Photo-bg slides have legible text contrast

Fix any issues - tighten `margin-bottom` on list items, reduce `font-size` on the list, or shorten bullet text - then re-screenshot the affected slides to confirm the fix before committing.

### Step 7: Suggest Browser Editing

After completing the presentation, let the user know they can edit text directly:

```bash
node <path-to-skill>/scripts/edit-html.js presentation.html
```

Click any text to edit inline, then save changes back to the file.

## Scrollable Horizontal Content (Gantt charts, timelines)

For slides with content wider than the slide (timelines, Gantt charts, multi-month calendars):

**Container pattern:**
```html
<div style="overflow-x:auto;overflow-y:hidden;width:100%;cursor:grab;">
  <div style="width:2400px;position:relative;">
    <!-- wide content here -->
  </div>
</div>
```

**Critical: clip vertical lines to the chart rows only.**
Absolutely positioned vertical lines (week markers, today indicators) will bleed through ALL content below them - including any phase/legend cards - unless contained. Use a wrapper with `overflow:hidden` around just the row area:
```html
<div style="position:relative;overflow:hidden;">
  <!-- week lines inside here, clipped by overflow:hidden -->
  <div style="position:absolute;top:0;bottom:0;left:110px;width:1px;background:rgba(255,255,255,0.22);"></div>
  <!-- gantt rows -->
  <div class="sg-row">...</div>
</div>
<!-- phase cards or legends OUTSIDE the overflow:hidden wrapper - lines cannot reach here -->
<div>phase cards...</div>
```
Using `height:Npx` on the line div is NOT reliable - actual rendered heights vary. `overflow:hidden` on the row wrapper is the only foolproof approach.

**Phase cards aligned to timeline positions:**
When showing phase descriptions below a Gantt, position them with `position:absolute;left:Xpx;width:Ypx` matching their date-range pixels so they align with the bars above. Use `position:relative` on a full-width container:
```html
<div style="position:relative;width:2400px;height:200px;">
  <div style="position:absolute;left:141px;width:440px;...">Phase 1</div>
  <div style="position:absolute;left:581px;width:874px;...">Phase 2</div>
</div>
```

## Standalone (shareable) HTML

After completing a presentation that references local files (images, CSS), offer to build a self-contained version. Inline the CSS and embed images as base64:

```javascript
// .tmp/build-standalone.js
const fs = require('fs'), path = require('path');
let html = fs.readFileSync('presentation.html', 'utf8');

// Inline CSS
const css = fs.readFileSync('styles.css', 'utf8');
html = html.replace('<link rel="stylesheet" href="styles.css">', `<style>\n${css}\n</style>`);

// Embed local images
for (const imgFile of ['logo.png', 'photo.png']) {
  const data = fs.readFileSync(imgFile);
  const b64 = `data:image/png;base64,${data.toString('base64')}`;
  html = html.split(`src="${imgFile}"`).join(`src="${b64}"`);
}
fs.writeFileSync('presentation-STANDALONE.html', html);
```

The standalone file still requires internet for Reveal.js / Font Awesome CDN, which is a safe assumption for any laptop. Typical size is 1–2MB with two photos embedded.

## Charts

**Before adding ANY chart, read [references/charts.md](references/charts.md).** Charts require a specific flexbox pattern to size correctly. The key requirement: `maintainAspectRatio: false` in chart options, inside a flex container with `min-height: 0`.
