# Editing a Canva design in the browser

The Connect API can't edit a design's pages, so page edits drive the Canva editor over CDP with Playwright.
`scripts/canva-edit.js` runs the edits, and `scripts/canva-editor.js` holds the helpers it is built from; their comments explain each selector and wait.
This file covers what the scripts leave to you: the browser, how to batch the work, and the calls to make along the way.

## The browser

Run `canva-edit.js` through browser-chauffeur like any browser script: Phase 0 brings up its browser and passes `--cdp-port`, which defaults to the chauffeur's 9222.
Each command opens its own tab and closes only that tab.

**An idle Canva renderer freezes, and a frozen renderer hangs `connectOverCDP`.**
Run `node canva-edit.js --nudge --cdp-port=<port>` before every batch.

When the editor still stalls mid-edit because other sessions' tabs in the shared browser compete for its renderer, give the job its own Edge on its own port and pass that port instead:

```bash
python <browser-chauffeur>/templates/chauffeur.py --fresh --port 9230 --profile-dir "C:\Users\<you>\.claude\browser-chauffeur\profile-canva-<job>" --url https://www.canva.com/
```

The profile directory keeps the Canva login for the next launch with the same `--profile-dir`; the first launch needs the user to log in in that window.
Leave that login tab open, since closing a browser's last tab exits it.

## How to run edits

- **One script at a time against one design, two or three pages per batch.**
  Long runs die to low memory, and two scripts in one design fight over the selection.
- **Trust the last line.**
  Every command ends `Verification passed` or `Verification FAILED - N errors`.
- **Resume after a crash** by rerunning the same `--apply`; add `--force` to redo pages it already finished.
- **Run screenshot-heavy work in a subagent.**
  It returns a table and file paths, and the images stay in its context.
- **Review at full size.**
  `--shots` saves each page at 1920x1080, the size the defects show at.
- **Coordinate with whoever owns the design.**
  When another session owns a design, send it the exact edit to make.

## Units

The Arrange panel's W, H, X and Y read in the design's own units: a 1920x1080 slide reads 1920x1080 whatever the zoom.
Write placement plans in those units and nothing needs converting.

## Calls the scripts leave to you

- **Replace or add:** in a one-off script, use Replace for a file with the old image's aspect ratio, since Replace keeps the old box; for any other file, add the new image and delete the old.
- **Insert from Uploads** to put an image on another page.
- **A stray "Your paragraph text" box** means something typed with nothing selected.
  Undo it with Ctrl+Z, re-reading the page text after each press.

## Magic Expand

Magic Expand extends a photo with generated pixels.
It works on a free Canva account.

1. `canva-edit.js --expand --file=<png> --name=<slug> --out=<dir> --top=<px> --bottom=<px>` (also `--left`, `--right`) stops at `<out>/<slug>-results.png`, showing four results.
2. Look at the results screenshot yourself.
3. `--pick=K` (0-based, left to right, then down) applies a result and downloads it to `<out>/<slug>.png`.
   `--more` asks for four new results instead.
4. `--close` closes the scratch tab.

Each run leaves its scratch design in the account as an "Untitled design".
A design delete is permanent, so hand the user the scratch design ids from `<out>/run.log` to trash themselves.

On a low-res or busy source, Magic Expand often invents limbs, patterns or blobs.
Judge each result at full size and place a natural-looking one; when none is, use that person's photo unexpanded.

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
