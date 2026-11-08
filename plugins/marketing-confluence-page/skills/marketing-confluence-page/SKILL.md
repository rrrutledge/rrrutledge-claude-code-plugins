---
skill: marketing-confluence-page
description: Create a marketing-style Confluence page with alternating two-column sections (text + image), a hero intro, and a full-width CTA. Used for broad internal audience pages like feature announcements, onboarding guides, and platform documentation.
instructions: |-
  ## Marketing-Style Confluence Page

  This skill creates polished Confluence pages - alternating two-column sections with
  text on one side and an image on the other, separated by horizontal rules.

  ---

  ## Layout Pattern

  ```
  Hero          two_left_sidebar   image (left/narrow) + title+intro (right/wide)
  ──────────────────────────────────────────────────────
  Section A     two_right_sidebar  text (left) + image (right)
  ──────────────────────────────────────────────────────
  Section B     two_left_sidebar   image (left) + text (right)
  ──────────────────────────────────────────────────────
  ...alternating
  ──────────────────────────────────────────────────────
  CTA           fixed-width        tip callout, full width
  ```

  Key rules:
  - Alternate `two_right_sidebar` and `two_left_sidebar` so images zigzag left/right
  - Separate every section with a `fixed-width` HR divider
  - Hero always uses `two_left_sidebar` (image on left sidebar, text on right)
  - CTA uses `fixed-width` with a `tip` macro callout
  - Use `ac:breakout-mode="wide"` on all two-column sections
  - Image width: 380–400px in hero sidebar, 500px in body sections

  ---

  ## Two Section Types - Rules

  There are exactly two kinds of sections. Never mix them.

  ### Type A: Concept section (two-column, stock image)
  Use when the section explains a concept, reason, or benefit with no screenshot.
  - Image side: Find an appropriate stock photo from Unsplash (search for something that matches the section topic)
  - Text side: heading + 2–3 short paragraphs or a bullet list
  - Stock image width: 500px in body, 300px in hero sidebar

  ### Type B: Screenshot section (full-width, no stock image)
  Use when the section shows what the user sees or does in the product.
  - `fixed-width` layout - the entire section width
  - Contains: h2 heading + explanatory paragraph(s) + centered screenshot
  - No stock image - the screenshot IS the visual
  - Screenshot width: 880px, centered

  | Content                        | Section type          | Image          |
  |--------------------------------|-----------------------|----------------|
  | Why this matters / value       | Two-column (Type A)   | Unsplash stock |
  | Hero intro                     | Two-column (Type A)   | Stock or logo  |
  | Step-by-step / UI walkthroughs | Full-width (Type B)   | Real screenshot|
  | Flexibility / concepts         | Two-column (Type A)   | Unsplash stock |
  | CTA                            | Full-width            | None           |

  **NEVER put a full UI screenshot in a two-column cell.** It renders too small.
  **NEVER add a stock image to a screenshot section.** The screenshot is the visual.

  ---

  ## Storage Format Templates

  ### Horizontal rule divider
  ```xml
  <ac:layout-section ac:type="fixed-width" ac:breakout-mode="default">
    <ac:layout-cell><hr /></ac:layout-cell>
  </ac:layout-section>
  ```

  ### Image left, text right (two_left_sidebar)
  ```xml
  <ac:layout-section ac:type="two_left_sidebar" ac:breakout-mode="wide">
    <ac:layout-cell>
      <p>IMAGE_TAG_HERE</p>
    </ac:layout-cell>
    <ac:layout-cell>
      <h2>Section Title</h2>
      <p>Body text here.</p>
    </ac:layout-cell>
  </ac:layout-section>
  ```

  ### Text left, image right (two_right_sidebar)
  ```xml
  <ac:layout-section ac:type="two_right_sidebar" ac:breakout-mode="wide">
    <ac:layout-cell>
      <h2>Section Title</h2>
      <p>Body text here.</p>
    </ac:layout-cell>
    <ac:layout-cell>
      <p>IMAGE_TAG_HERE</p>
    </ac:layout-cell>
  </ac:layout-section>
  ```

  ### Screenshot section (Type B)
  ```xml
  <ac:layout-section ac:type="fixed-width" ac:breakout-mode="default">
    <ac:layout-cell>
      <h2>Section Heading</h2>
      <p>Explanatory paragraph.</p>
      <p style="text-align: center;">
        <ac:image ac:align="center" ac:layout="center" ac:custom-width="true" ac:width="880">
          <ri:attachment ri:filename="my-screenshot.png"/>
        </ac:image>
      </p>
    </ac:layout-cell>
  </ac:layout-section>
  ```

  ### Stock images via Unsplash (ri:url)
  Search Unsplash for a photo that matches the section topic. Use this format:
  ```xml
  <ac:image ac:align="center" ac:layout="center" ac:custom-width="true" ac:width="500">
    <ri:url ri:value="https://images.unsplash.com/photo-{PHOTO_ID}?w=600&amp;h=400&amp;fit=crop&amp;auto=format" />
  </ac:image>
  ```

  ### Callouts
  ```xml
  <!-- Info callout -->
  <ac:structured-macro ac:name="info" ac:schema-version="1">
    <ac:rich-text-body>
      <p><strong>Key point:</strong></p>
      <ul><li>Item one</li><li>Item two</li></ul>
    </ac:rich-text-body>
  </ac:structured-macro>

  <!-- Tip callout for CTA -->
  <ac:structured-macro ac:name="tip" ac:schema-version="1">
    <ac:rich-text-body>
      <p><strong><a href="URL">Action text &rarr;</a></strong></p>
      <p>Supporting text or feedback prompt.</p>
    </ac:rich-text-body>
  </ac:structured-macro>
  ```

  ---

  ## Python Helpers

  The `scripts/` directory next to this SKILL.md contains `confluence_page_helpers.py`
  with helper functions for building pages programmatically. Import it from `.tmp/` scripts:

  ```python
  sys.path.insert(0, '<path-to-skill>/scripts')
  from confluence_page_helpers import (
      api, HR,
      left_image_section, right_image_section, screenshot_section,
      attachment, unsplash, upload_attachment,
  )
  ```

  ---

  ## Workflow: Creating a New Page

  1. **Identify parent page** - find its Confluence page ID
  2. **Write the script** to `.tmp/create-PAGENAME.py` using the helpers
  3. **Run the script** - it POSTs to `/wiki/rest/api/content` and prints the page ID
  4. **Upload screenshots** as attachments using `upload_attachment()`
  5. **Review in browser** - open the page URL in Confluence to verify layout

  ---

  ## Content Guidelines

  - **Tone**: Clear, practical, grounded - not promotional
  - **Headings**: No emojis; sentence case or title case
  - **Paragraphs**: 2–3 sentences max per paragraph
  - **No transition framing**: Write in present tense as if this is simply how things work.
    Never say "we're changing X" or "X is being disabled". State the current reality.
  - **Info callout**: Use to highlight a short list of key requirements or facts
  - **Tip callout**: Reserve for the CTA at the bottom
---
