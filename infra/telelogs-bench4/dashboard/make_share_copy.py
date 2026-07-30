#!/usr/bin/env python3
"""Produce a send-anywhere copy of the solution report.

solution_report.html is already fully self-contained — no CDN, no external CSS or
JS, no web fonts, no images, and the measured data is inlined as JSON. The only
thing that breaks when the file travels alone is the three links to its sibling
pages on the dashboard, so this strips those and leaves everything else identical.

    python3 make_share_copy.py        # -> solution_report_standalone.html

Re-run it whenever solution_report.html changes.
"""
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "solution_report.html"
DST = HERE / "solution_report_standalone.html"

html = SRC.read_text(encoding="utf-8")

# the nav entry pointing at the Vietnamese working log
html = html.replace('\n    <a href="tools_r3.html">Working log (VI)</a>', "")
# the two footer references
html = html.replace(
    'Working log with the full Vietnamese commentary: <a href="tools_r3.html">tools_r3.html</a> ·\n'
    '    live runs: <a href="tools.html">tools.html</a>.',
    "Standalone copy: the interactive report is complete on its own, with no external "
    "assets and no network access required.")

leftover = re.findall(r'href="[a-z_0-9]+\.html"', html)
assert not leftover, f"unresolved local links: {leftover}"
for pattern in (r'src="http', r'@import', r'https?://', r'fonts\.googleapis'):
    assert not re.search(pattern, html), f"external reference found: {pattern}"

DST.write_text(html, encoding="utf-8")
print(f"wrote {DST.name} ({DST.stat().st_size / 1024:.0f} KB, no external references)")
