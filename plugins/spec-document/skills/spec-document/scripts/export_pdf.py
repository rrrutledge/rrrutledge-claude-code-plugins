"""Export a spec HTML page to PDF with headless Chrome or Edge.

Usage: python export_pdf.py <spec.html> [out.pdf]

Writes the PDF beside the HTML (same name, .pdf) unless an output path is given.
A virtual-time budget gives the Mermaid CDN script time to render diagrams before printing.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

BROWSERS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
]


def find_browser():
    for path in BROWSERS:
        if os.path.exists(path):
            return path
    for name in ("google-chrome", "chromium", "chrome", "msedge"):
        found = shutil.which(name)
        if found:
            return found
    sys.exit("No Chrome or Edge found for PDF export.")


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    html = Path(sys.argv[1]).resolve()
    out = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else html.with_suffix(".pdf")
    subprocess.run(
        [
            find_browser(),
            "--headless=new",
            "--disable-gpu",
            "--no-pdf-header-footer",
            "--virtual-time-budget=15000",
            f"--print-to-pdf={out}",
            html.as_uri(),
        ],
        check=True,
        capture_output=True,
    )
    if not out.exists() or out.stat().st_size == 0:
        sys.exit(f"PDF export FAILED - {out} was not written.")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
