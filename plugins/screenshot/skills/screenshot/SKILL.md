---
skill: screenshot
description: Auto-invoke when the user mentions taking a screenshot, sharing a screenshot, or references "this screenshot" without providing a file path. Loads the most recent screenshot from the Windows Screenshots folder.
instructions: |-
  Read and display the most recent screenshot across all candidate Screenshots folders.

  Candidate folders (a machine can have more than one actively receiving screenshots at once):

  - ~/OneDrive/Pictures/Screenshots 1
  - ~/Pictures/Screenshots
  - ~/OneDrive/Pictures/Screenshots
  - ~/OneDrive*/Pictures/Screenshots (OneDrive for Business - use glob to find)

  Steps:

  1. **Run the folder-scan script** to get the screenshot path(s): `python "<plugin_dir>/skills/screenshot/scripts/find_latest_screenshot.py"` (resolve `<plugin_dir>` to this plugin's install directory).
     The script scans every candidate folder that exists and collects .png files from all of them (excluding desktop.ini), ranked newest first across the combined set.
     With no flags it prints the single latest path.
     `--index N` prints the Nth-most-recent path.
     `--count N` prints the N most recent paths, one per line, newest first.
     `--since N` prints every path modified within the last N minutes, one per line, newest first.
     An ordinal or positional look-back request selects `--index N`, counting N back from the latest.
     A plural quantity request selects `--count N`, where N is the requested quantity.
     A time-window request selects `--since N`, converting the requested window to minutes.
     Anything else uses the default.
  2. **Load and display the screenshot(s)** at the printed path(s), using the Read tool.
  3. **Treat extra args as context** - if the user provided additional text in the args beyond a look-back, count, or time-window request, treat it as a question or context about the screenshot.
  4. **Record it where the work is tracked** - when the screenshot shows the state of a conversation or task that a Trello card tracks, transcribe what it shows to a dated comment on that card in the same turn, per the `trello-outreach` skill's "Keep a tracked card current with what only Russell can see".
     The image lives only in this session, so the card comment is what a later session reads.
---
