#!/usr/bin/env python3
"""Wrap an authored plan/spec body in the editable-HTML review shell.

The shell is fixed: a sticky toolbar (edit-status, "Download edited copy",
"Reset to original"), a `contenteditable` article, `localStorage` autosave keyed
to the output filename, and the callout / pill / file-change CSS Russell reviews
against. The author writes only the section content - the part that carries the
plan's meaning - using the class vocabulary documented in SKILL.md, and this
generator supplies everything else and wires the title, the autosave key, and
the download filename so they can't drift apart.

Usage:
    python plan-page.py --title "..." --out .tmp/plan-foo.html --body .tmp/plan-foo-body.html
    python plan-page.py --title "..." --out .tmp/plan-foo.html --body .tmp/plan-foo-body.html \
        --subtitle "Implementation plan - draft PR only" --subtitle "Verified 2026-09-22"

The output always lands in .tmp/ (a working document, never committed). Open it
with `start <path>`.
"""

import argparse
import html
import re
from pathlib import Path

SHELL = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title_attr}</title>
<style>
  :root {{
    --bg: #f7f7f5;
    --panel: #ffffff;
    --ink: #23211d;
    --muted: #6b675f;
    --line: #e4e1da;
    --accent: #b45309;
    --accent-soft: #fef3e2;
    --ok: #15803d;
    --ok-soft: #eaf6ee;
    --warn: #9a3412;
    --warn-soft: #fdece3;
    --del: #b91c1c;
    --del-soft: #fbe9e9;
  }}
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; padding: 0; }}
  body {{
    background: var(--bg);
    color: var(--ink);
    font: 16px/1.62 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    -webkit-font-smoothing: antialiased;
  }}
  .toolbar {{
    position: sticky; top: 0; z-index: 10;
    display: flex; align-items: center; gap: 12px;
    padding: 10px 20px;
    background: rgba(247,247,245,0.92);
    backdrop-filter: blur(6px);
    border-bottom: 1px solid var(--line);
  }}
  .toolbar .status {{ color: var(--muted); font-size: 13px; margin-right: auto; }}
  .toolbar .status b {{ color: var(--ok); font-weight: 600; }}
  button {{
    font: inherit; font-size: 13px;
    padding: 6px 12px; border-radius: 7px;
    border: 1px solid var(--line); background: var(--panel); color: var(--ink);
    cursor: pointer;
  }}
  button:hover {{ border-color: #cfcbc2; }}
  button.primary {{ background: var(--accent); border-color: var(--accent); color: #fff; }}
  main {{ max-width: 860px; margin: 26px auto 90px; padding: 0 22px; }}
  .doc {{
    background: var(--panel);
    border: 1px solid var(--line);
    border-radius: 12px;
    padding: 40px 46px;
    box-shadow: 0 1px 2px rgba(0,0,0,0.03);
  }}
  .doc:focus {{ outline: none; }}
  .doc[contenteditable="true"]:focus-within {{ box-shadow: 0 0 0 2px var(--accent-soft), 0 1px 2px rgba(0,0,0,0.03); }}
  h1 {{ font-size: 27px; line-height: 1.25; margin: 0 0 6px; letter-spacing: -0.01em; }}
  .sub {{ color: var(--muted); font-size: 14px; margin: 0 0 4px; }}
  h2 {{
    font-size: 19px; margin: 34px 0 10px; padding-top: 20px;
    border-top: 1px solid var(--line); letter-spacing: -0.01em;
  }}
  h3 {{ font-size: 15px; margin: 20px 0 6px; color: var(--accent); }}
  p {{ margin: 0 0 12px; }}
  ul, ol {{ margin: 0 0 12px; padding-left: 22px; }}
  li {{ margin: 5px 0; }}
  code {{
    font: 13px/1.5 "SF Mono", ui-monospace, "Cascadia Code", Consolas, monospace;
    background: #f0efec; padding: 1px 5px; border-radius: 4px;
  }}
  pre {{
    background: #2b2924; color: #f3f0e9; border-radius: 9px;
    padding: 14px 16px; overflow-x: auto; margin: 0 0 14px;
  }}
  pre code {{ background: none; color: inherit; padding: 0; font-size: 12.5px; }}
  .callout {{ border-radius: 9px; padding: 13px 16px; margin: 0 0 14px; border: 1px solid var(--line); }}
  .callout .tag {{
    display: inline-block; font-size: 11px; font-weight: 700; letter-spacing: 0.06em;
    text-transform: uppercase; margin-bottom: 4px;
  }}
  .callout.ok {{ background: var(--ok-soft); border-color: #cfe6d5; }}
  .callout.ok .tag {{ color: var(--ok); }}
  .callout.decide {{ background: var(--accent-soft); border-color: #f3ddb8; }}
  .callout.decide .tag {{ color: var(--accent); }}
  .callout.out {{ background: var(--warn-soft); border-color: #f4cbb2; }}
  .callout.out .tag {{ color: var(--warn); }}
  .callout p:last-child, .callout ul:last-child, .callout ol:last-child {{ margin-bottom: 0; }}
  .file {{ border-left: 3px solid var(--line); padding: 2px 0 2px 14px; margin: 0 0 16px; }}
  .file h3 {{ margin-top: 8px; }}
  .rm {{ color: var(--del); font-weight: 600; }}
  .add {{ color: var(--ok); font-weight: 600; }}
  .pill {{
    display: inline-block; font-size: 11px; font-weight: 700; padding: 1px 8px; border-radius: 20px;
    vertical-align: middle; margin-left: 6px;
  }}
  .pill.remove {{ background: var(--del-soft); color: var(--del); }}
  .pill.change {{ background: var(--accent-soft); color: var(--accent); }}
  .pill.keep {{ background: var(--ok-soft); color: var(--ok); }}
  .decisions {{ counter-reset: dec; list-style: none; padding-left: 0; }}
  .decisions > li {{ position: relative; padding-left: 34px; margin: 10px 0; }}
  .decisions > li::before {{
    counter-increment: dec; content: counter(dec);
    position: absolute; left: 0; top: 1px;
    width: 22px; height: 22px; border-radius: 50%;
    background: var(--accent); color: #fff; font-size: 12px; font-weight: 700;
    display: flex; align-items: center; justify-content: center;
  }}
  .hint {{ color: var(--muted); font-size: 13px; }}
</style>
</head>
<body>
<div class="toolbar">
  <span class="status" id="status">Editable - your changes save automatically in this browser.</span>
  <button id="export" class="primary">Download edited copy</button>
  <button id="reset">Reset to original</button>
</div>

<main>
<article class="doc" id="doc" contenteditable="true" spellcheck="false">

<h1>{title_html}</h1>
{subtitles}
{body}

<p class="hint">Edit anything above - strike a change, add a note, reorder. Your edits save in this browser; "Download edited copy" saves a file, "Reset to original" starts over.</p>

</article>
</main>

<script>
  const KEY = "{key}";
  const doc = document.getElementById("doc");
  const status = document.getElementById("status");
  const ORIGINAL = doc.innerHTML;

  const saved = localStorage.getItem(KEY);
  if (saved !== null) {{ doc.innerHTML = saved; markSaved("Restored your edits."); }}

  let t = null;
  doc.addEventListener("input", () => {{
    status.textContent = "Editing...";
    clearTimeout(t);
    t = setTimeout(() => {{ localStorage.setItem(KEY, doc.innerHTML); markSaved(); }}, 400);
  }});

  function markSaved(prefix) {{
    const time = new Date().toLocaleTimeString([], {{hour: "2-digit", minute: "2-digit"}});
    status.innerHTML = (prefix ? prefix + " " : "") + "<b>Saved</b> " + time + " - stays in this browser.";
  }}

  document.getElementById("reset").addEventListener("click", () => {{
    if (!confirm("Discard your edits and restore the original?")) return;
    localStorage.removeItem(KEY);
    doc.innerHTML = ORIGINAL;
    status.textContent = "Reset to original.";
  }});

  document.getElementById("export").addEventListener("click", () => {{
    const html = "<!DOCTYPE html>\\n<html lang=\\"en\\"><head><meta charset=\\"utf-8\\">" +
      "<title>{title_js}</title></head><body>" +
      doc.innerHTML + "</body></html>";
    const blob = new Blob([html], {{type: "text/html"}});
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "{download}";
    a.click();
    URL.revokeObjectURL(a.href);
  }});
</script>
</body>
</html>
"""


def slug(name: str) -> str:
    """A localStorage-safe slug from the output filename stem."""
    stem = Path(name).stem
    s = re.sub(r"[^a-zA-Z0-9]+", "-", stem).strip("-").lower()
    return s or "plan"


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate an editable-HTML plan/spec review page.")
    ap.add_argument("--title", required=True, help="Plan title - the h1 and the browser tab title.")
    ap.add_argument("--out", required=True, help="Output .html path (put it in .tmp/).")
    ap.add_argument("--body", required=True, help="Path to the authored body fragment (the sections).")
    ap.add_argument("--subtitle", action="append", default=[],
                    help="A muted sub-line under the title (repeatable). HTML allowed, e.g. <code>...</code>.")
    args = ap.parse_args()

    body = Path(args.body).read_text(encoding="utf-8").strip()
    subtitles = "\n".join(f'<p class="sub">{s}</p>' for s in args.subtitle)
    key = slug(args.out) + "-v1"
    download = Path(args.out).stem + "-edited.html"

    page = SHELL.format(
        title_attr=html.escape(args.title),
        title_html=html.escape(args.title),
        title_js=args.title.replace("\\", "\\\\").replace('"', '\\"'),
        subtitles=subtitles,
        body=body,
        key=key,
        download=download,
    )

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")
    print(f"Wrote {out}")
    print(f"Open it with:  start {out}")


if __name__ == "__main__":
    main()
