#!/usr/bin/env python3
"""Copy widget/music-assistant.yml into the YAML block of widget/README.md.

The community-widgets repository expects the widget YAML inside README.md,
while Glance users of this repository can $include the .yml file directly.
This keeps both identical.

Run:   python3 dev/sync_readme.py           update the README
       python3 dev/sync_readme.py --check   exit 1 if the README is out of date
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "widget" / "README.md"
START, END = "<!-- widget-yaml:start -->", "<!-- widget-yaml:end -->"

yaml_text = (ROOT / "widget" / "music-assistant.yml").read_text(encoding="utf-8").rstrip("\n")
readme = README.read_text(encoding="utf-8")
head, rest = readme.split(START, 1)
_, tail = rest.split(END, 1)
updated = f"{head}{START}\n```yaml\n{yaml_text}\n```\n{END}{tail}"

if "--check" in sys.argv:
    if updated != readme:
        sys.exit("widget/README.md is out of date, run: python3 dev/sync_readme.py")
    print("widget/README.md is in sync")
elif updated != readme:
    README.write_text(updated, encoding="utf-8")
    print("updated widget/README.md")
else:
    print("widget/README.md already in sync")
