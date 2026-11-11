# Editing a Canva design in the browser

The Connect API can't edit a design's pages, so page edits drive the Canva editor over CDP with Playwright.
`scripts/canva-edit.js` runs the edits, and `scripts/canva-editor.js` holds the helpers it is built from; their comments explain each selector and wait.
This file covers what the scripts leave to you: the browser, how to batch the work, and the calls to make along the way.

## The browser

Give Canva its own Edge, with its own profile on its own CDP port:

```bash
python <browser-chauffeur>/templates/chauffeur.py --fresh --port 9230 --profile-dir "C:\Users\<you>\.claude\browser-chauffeur\profile-canva-<job>" --url https://www.canva.com/
```

- The profile directory persists, so the Canva login carries over to the next launch with the same `--profile-dir`.
  The first launch needs the user to log in to Canva in that window.
- The shared chauffeur browser on 9222 freezes Canva: other sessions' tabs compete for the renderer, and the editor stalls mid-edit.
- Leave the login tab open, since closing a browser's last tab exits it.
  The scripts open their own tagged tab and close only that tab.
- Pass `--cdp-port=9230` to every `canva-edit.js` command.

**An idle Canva renderer freezes, and a frozen renderer hangs `connectOverCDP`.**
Run `node canva-edit.js --nudge --cdp-port=9230` before every batch.

## How to run edits

- **One script at a time against one design, two or three pages per batch.**
  Long runs die to low memory, and two scripts in one design fight over the selection.
- **Trust the last line.**
  Every command ends `Verification passed` or `Verification FAILED - N errors`, judged from a reload of the page.
- **Resume after a crash** by rerunning the same `--apply`; add `--force` to redo pages it already finished.
- **Keep screenshots out of the main context.**
  Run screenshot-heavy work and visual review in a subagent, which returns a table and file paths.
- **Review at full size.**
  `--shots` saves each page at 1920x1080; a contact sheet hides the defects that matter.
- **Coordinate with whoever owns the design.**
  When another session owns a design, send it the exact edit instead of editing alongside it.

## Units

The Arrange panel's W, H, X and Y read in the design's own units: a 1920x1080 slide reads 1920x1080 whatever the zoom.
Write placement plans in those units and nothing needs converting.

## Calls the scripts leave to you

- **Replace or add:** `--apply` swaps a picture in place when the new file has the old image's aspect ratio, and otherwise adds the new image and deletes the old.
  For a one-off edit in your own script, make the same choice: Replace keeps the old box, so it only fits a same-shaped file.
- **Copying between pages** is flaky, so insert from Uploads instead.
- **A stray "Your paragraph text" box** means something typed with nothing selected.
  Undo it with Ctrl+Z, re-reading the page text after each press.

## Magic Expand

Magic Expand extends a photo with generated pixels.
It works on a free Canva account.
It runs only in a scratch design, never the real one, because it is a generative edit.

1. `canva-edit.js --expand --file=<png> --name=<slug> --out=<dir> --top=<px> --bottom=<px>` (also `--left`, `--right`) stops at `<out>/<slug>-results.png`, showing four results.
2. Look at the results screenshot yourself.
3. `--pick=K` (0-based, left to right, then down) applies a result and downloads it to `<out>/<slug>.png`.
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
