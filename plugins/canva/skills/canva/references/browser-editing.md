# Editing a Canva design in the browser

The Connect API can't edit a design's pages, so page edits drive the Canva editor over CDP with Playwright.
`scripts/canva-edit.js` runs the edits, and `scripts/canva-editor.js` holds the helpers it is built from.
Every selector and wait in them held up across eight rounds of edits to a 40-page speaker deck (Nov 2026).
This file covers what the scripts can't decide for you: how to set up the browser, how to batch the work, and the traps behind each helper.

## The browser

Give Canva its own Edge, with its own profile on its own CDP port:

```bash
python <browser-chauffeur>/templates/chauffeur.py --fresh --port 9230 --profile-dir "C:\Users\<you>\.claude\browser-chauffeur\profile-canva-<job>" --url https://www.canva.com/
```

- The profile directory persists, so the Canva login carries over to the next launch with the same `--profile-dir`.
  The first launch needs the user to log in to Canva in that window.
- The shared chauffeur browser on 9222 freezes Canva: other sessions' tabs compete for the renderer, and the editor stalls mid-edit.
- Leave the login tab open, since closing a browser's last tab exits it.
  The scripts open their own tagged tab (`?cvapply=1` and the like) and close only that tab.
- Pass `--cdp-port=9230` to every `canva-edit.js` command.

**An idle Canva renderer freezes, and a frozen renderer hangs `connectOverCDP`.**
Run `node canva-edit.js --nudge --cdp-port=9230` before every batch.
It resizes each browser window 20 px and back over the browser-level CDP socket, which needs no Playwright connection and wakes the renderer.
Each run can leave the window about 1 px wider.
Once connected, `withDesign` also turns on focus emulation and sets the page lifecycle to active, so a background tab keeps rendering.

## How to run edits

- **One script at a time against one design, two or three pages per batch.**
  Long runs died to low memory, and two scripts in one design fight over the selection.
- **Edit from a fresh reload of the page.**
  `--apply` reloads before it touches a page, so a person's manual edits since the last run are kept, not overwritten.
- **Check after a reload, never before.**
  An edit shown in the editor can still be lost if the page reloads mid-save.
  Every command waits for "Saving" to clear, reloads, reads the page back, and reports `Verification passed` only if the read-back matches.
- **Resume, don't restart.**
  `--apply` logs each finished item and page in `<out>/progress.txt` and skips them on a rerun, so a crash costs one page.
  `--force` redoes them.
- **Keep screenshots out of the main context.**
  Run screenshot-heavy work and visual review in a subagent, which returns a table and file paths.
- **Coordinate with whoever owns the design.**
  When another session owns a design, send it the exact edit instead of editing alongside it.

## Units

The Arrange panel's W, H, X and Y inputs read in the design's own units: a 1920x1080 slide reads 1920x1080 whatever the zoom.
Write placement plans in those units and nothing needs converting.
`pageRect` gives the on-screen scale (`s`) for the cases that do need screen px, such as finding text.

## The editor, as the scripts see it

- **Pages:** each thumbnail in the page strip is `[data-role="timeline-scene"][aria-label="Page N"]`, and the strip renders only a window of them.
  `gotoPage` steps toward page N until its thumbnail exists, then waits for `[aria-roledescription="Presentation"][aria-label="Page N"]`.
- **Selecting a layer:** the Position panel (Arrange and Layers tabs) only opens with something selected.
  In the Layers tab, each layer is a row button 280-360 px wide whose left edge sits at x 80-100, front-most first, ending with "Background".
  A text layer's row label is its text; images read "Image", frames "Frame element".
- **Reading and setting a box:** the selected layer's W, H, X, Y are the panel's inputs whose values end in `px`.
  Set one with click, Ctrl+A, type, Enter.
- **Keep the aspect ratio:** lock "Ratio", set the height alone, then X and Y.
  The first size typed right after toggling the lock sometimes doesn't stick, so `placeSelected` reads back and retries once.
- **An image is selected** when the toolbar shows an "Eraser" button.
- **Uploading:** `setInputFiles` on the Uploads panel's `input[type=file]`.
  The upload is done when the first thumbnail has a new `src`, is no longer a `blob:` preview, matches the file's aspect ratio within 1.5%, and holds for 2 s.
- **Swapping a picture:** Replace keeps the layer's slot but also its box, so it only fits when the new file has the old image's aspect ratio.
  Otherwise add the new image, size it, delete the old layer, and move the new one back to the old slot with Arrange's Backward or Forward.
  A swap script once deleted the new photo instead of the old on five pages, so `--apply` identifies the old layer by its pre-edit box, excludes the new box, and confirms exactly one layer at the target box after a reload.
- **Deleting:** the Delete key removes an image or text, but not a "Frame element".
  Delete a frame through its row's "Open context menu for layer element" button and the menu's Delete item.
- **Text:** `textRuns` reads the page's visible text from the DOM, in design px.
  The DOM can also carry stray off-card text, so keep only runs inside the text layer's box.
  To retype one word, click its text, double-click the word, and type only once `window.getSelection()` reads exactly that word (`replaceWord`).
  Typing with nothing selected inserts a "Your paragraph text" box.
  Undo it with Ctrl+Z, re-reading the text after each press.
- **Screenshots:** the editor view is scaled and cluttered.
  `presentShot` sets a 1920x1080 viewport, clicks "Present full screen", parks the pointer in a corner so the controls fade, and captures the true slide.
  Review at that full size, since a contact sheet hides the defects that matter.
- **Copying between pages** is flaky, so insert from Uploads instead.

## Magic Expand

Magic Expand extends a photo with generated pixels, for example hair cut off by the frame or a body that ends at the shoulders.
It works on a free Canva account.
It runs only in a scratch design, never the real one, because it is a generative edit.

1. `canva-edit.js --expand --file=<png> --name=<slug> --out=<dir> --top=<px> --bottom=<px>` (also `--left`, `--right`) creates a scratch design the size of the photo plus the padding.
   It places the photo at its true size, runs Edit, See all, Magic Expand, Whole Page, Expand, and saves `<slug>-results.png` showing four results.
2. Look at the results screenshot yourself.
3. `--pick=K` (0-based, left to right, then down) applies a result and downloads the page to `<out>/<slug>.png`.
   `--more` asks for four new results instead.
4. `--close` closes the scratch tab.

Each run leaves an "Untitled design" in the account, and no script deletes designs, since a design delete is permanent.
Hand the user the list of scratch design ids from `<out>/run.log` to trash by hand.

On a low-res or busy source, Magic Expand often looks wrong: it has added crossed arms, a hand, patterns and a green blob.
Judge each result at full size, and skip the person rather than place an unnatural one.

## Other flows seen working

- **Background remover:** the toolbar button is "BG Remover", and it's done when the button colour is no longer `rgb(158,158,158)`.
- **Download:** File, Download, then the panel's last "Download" button, inside `Promise.all` with `waitForEvent('download')`.
- **New design of a given size:** `https://www.canva.com/design?create=true&width=W&height=H&units=px`.
- **Find a design:** `https://www.canva.com/s/designs?query=<text>`.
  Trash is at `https://www.canva.com/folder/trash`.
- **Duplicate the selected element:** Ctrl+D.
- **Version history:** File, Version history.
  It has no named restore points.
- **Sharing a design with a person:** check that "Invite to team" is unticked before submitting.
  Sharing reaches another person, so stage it and let the user press the final button.
