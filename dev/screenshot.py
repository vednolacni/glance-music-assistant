#!/usr/bin/env python3
"""Refresh widget/preview*.png from a running Glance, for example one connected to a real Music Assistant.

Start Glance with the config from dev/make_config.py first. The screenshots show the default
widget (id ma) and the big-cover widget (id ma-big) as they are at that moment, so start the
music you want to show before running this.

Requirements: pip install playwright && python -m playwright install chromium
Run:          python3 dev/screenshot.py [URL] [--player NAME]
              URL defaults to http://127.0.0.1:8080, --player opens that player's tab first
"""

from __future__ import annotations

import argparse
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
TARGETS = {"ma": "preview.png", "ma-big": "preview-big-cover.png"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("url", nargs="?", default="http://127.0.0.1:8080")
    parser.add_argument("--player", default="", help="name of the player tab to open first")
    args = parser.parse_args()

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1100}, device_scale_factor=2)
        page.goto(args.url)
        page.wait_for_function("window.maPlayer !== undefined", timeout=10000)
        for widget_id in TARGETS:
            if args.player:
                tab = page.locator(f'[data-ma-root="{widget_id}"] .tab-music-assistant', has_text=args.player)
                if tab.count() == 0:
                    raise SystemExit(f"no player tab matching {args.player!r} in widget {widget_id}")
                tab.first.click()
        page.mouse.move(0, 0)
        page.wait_for_function("[...document.images].every(img => img.complete)", timeout=10000)
        page.wait_for_timeout(800)  # Glance's entrance animation
        for widget_id, name in TARGETS.items():
            page.locator(".widget", has=page.locator(f'[data-ma-root="{widget_id}"]')).screenshot(
                path=ROOT / "widget" / name)
            print(f"saved widget/{name}")
        browser.close()


if __name__ == "__main__":
    main()
