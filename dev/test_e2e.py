#!/usr/bin/env python3
"""End-to-end test of the widget in a real browser.

Starts the mock Music Assistant and Glance on spare ports, opens the page in
Chromium (Playwright) and checks rendering, every control, soft refresh,
auto refresh at the end of a track, error reporting and the read-only variant.

Requirements: glance binary on PATH (or $GLANCE_BIN), pip install aiohttp pillow playwright
Run:          python3 dev/test_e2e.py [--screenshots]
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
MA_PORT, GLANCE_PORT = 18095, 18080
MA = f"http://127.0.0.1:{MA_PORT}"
GLANCE = f"http://127.0.0.1:{GLANCE_PORT}"
GLANCE_BIN = os.environ.get("GLANCE_BIN") or shutil.which("glance") or "glance"


def wait_for(url: str, timeout: float = 10) -> None:
    end = time.time() + timeout
    while time.time() < end:
        try:
            urllib.request.urlopen(url, timeout=1)
            return
        except Exception:  # noqa: BLE001
            time.sleep(0.2)
    raise SystemExit(f"{url} did not come up")


def mock_log() -> list[dict]:
    return json.loads(urllib.request.urlopen(f"{MA}/_log").read())


def last_command(name: str) -> dict:
    for entry in reversed(mock_log()):
        if entry["command"] == name:
            return entry
    raise AssertionError(f"command {name} was not received")


def main() -> None:
    screenshots = "--screenshots" in sys.argv
    tmp = Path(tempfile.mkdtemp())
    config = tmp / "glance.yml"
    env = {**os.environ, "GLANCE_PORT": str(GLANCE_PORT), "CONFIG_OUT": str(config),
           "MOCK_MA_PORT": str(MA_PORT), "MA_URL": MA, "MA_TOKEN": "dev-token"}
    subprocess.run([sys.executable, ROOT / "dev" / "make_config.py"], env=env, check=True, capture_output=True)
    procs = [
        subprocess.Popen([sys.executable, ROOT / "dev" / "mock_ma.py"], env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT),
        subprocess.Popen([GLANCE_BIN, "--config", str(config)], env=env,
                         stdout=open(tmp / "glance.log", "w"), stderr=subprocess.STDOUT),
    ]
    try:
        wait_for(f"{MA}/_log")
        wait_for(f"{GLANCE}/api/healthz")
        run_browser_tests(screenshots)
    finally:
        for proc in procs:
            proc.terminate()
    print("\nALL TESTS PASSED")


def run_browser_tests(screenshots: bool) -> None:
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1100}, device_scale_factor=2)
        problems: list[str] = []
        page.on("pageerror", lambda err: problems.append(f"page error: {err}"))
        page.on("console", lambda msg: msg.type == "error" and "/imageproxy/" not in msg.location.get("url", "")
                and problems.append(f"console: {msg.text}"))  # the mock has one broken cover on purpose

        page.goto(GLANCE)
        widget = page.locator('[data-ma-root="ma"]')
        title = page.locator('[data-ma-root="ma"] .panel-music-assistant:visible .title-music-assistant')
        panel = '[data-ma-root="ma"] .panel-music-assistant:visible'

        def check(name: str, fn) -> None:
            fn()
            print(f"  ok  {name}")

        def click_bar(fraction: float) -> None:
            # Relative to the bar, not the viewport: Playwright may scroll the page while it
            # retries a click on an element that is still moving in Glance's entrance animation
            bar = page.locator(f"{panel} .bar-music-assistant")
            box = bar.bounding_box()
            bar.click(position={"x": box["width"] * fraction, "y": box["height"] / 2})

        check("renders the playing player first", lambda: expect(title).to_have_text("Night Ferry"))
        check("shows a tab per player with a queue", lambda: expect(
            page.locator('[data-ma-root="ma"] .tab-music-assistant')).to_have_text(["Living room", "Kitchen"]))
        check("hides players outside the user's player filter", lambda: expect(widget).not_to_contain_text("Web (Chrome"))
        check("lists four upcoming tracks", lambda: expect(
            page.locator(f"{panel} .row-music-assistant")).to_have_count(4))
        page.wait_for_function("window.maPlayer !== undefined", timeout=5000)
        page.evaluate("window.__noReload = true")

        def next_track() -> None:
            page.locator(f'{panel} [data-ma-cmd="player_queues/next"]').click()
            expect(title).to_have_text("Slow Orbit", timeout=6000)
            entry = last_command("player_queues/next")
            assert entry["transport"] == "ws" and entry["args"] == {"queue_id": "living_room"}, entry
            assert page.evaluate("window.__noReload === true"), "page did a full reload"
        check("next track over WebSocket, soft refresh without reload", next_track)

        def play_pause() -> None:
            page.locator(f'{panel} [data-ma-cmd="player_queues/play_pause"]').click()
            expect(page.locator(f'{panel} .play-music-assistant')).to_have_attribute("aria-label", "Play", timeout=6000)
        check("pause shows the play button", play_pause)
        page.locator(f'{panel} [data-ma-cmd="player_queues/play_pause"]').click()
        expect(page.locator(f'{panel} .play-music-assistant')).to_have_attribute("aria-label", "Pause", timeout=6000)

        def seek() -> None:
            click_bar(0.5)
            expect(page.locator(f"{panel} .time-music-assistant").first).to_have_text(
                re.compile(r"1:4[0-4]"), timeout=6000)  # keeps playing during the refresh
            position = last_command("player_queues/seek")["args"]["position"]
            assert 99 <= position <= 102, position  # Slow Orbit is 201 s long
        check("click on the progress bar seeks", seek)

        def shuffle() -> None:
            page.locator(f'{panel} [data-ma-cmd="player_queues/shuffle"]').click()
            expect(page.locator(f'{panel} [data-ma-cmd="player_queues/shuffle"]')).to_have_attribute(
                "aria-pressed", "true", timeout=6000)
        check("shuffle toggles", shuffle)

        def repeat() -> None:
            page.locator(f'{panel} [data-ma-cmd="player_queues/repeat"]').click()
            expect(page.locator(f'{panel} [data-ma-cmd="player_queues/repeat"]')).to_have_attribute(
                "aria-label", "Repeat: all", timeout=6000)
        check("repeat cycles off -> all", repeat)

        def volume() -> None:
            page.locator(f'{panel} [data-ma-cmd="players/cmd/volume_up"]').click()
            page.wait_for_timeout(600)
            assert last_command("players/cmd/volume_up")["args"] == {"player_id": "living_room"}
        check("volume up uses player_id", volume)

        def play_from_queue() -> None:
            page.locator(f"{panel} .row-music-assistant").nth(1).click()
            expect(title).to_have_text("Small Hours", timeout=6000)
            assert isinstance(last_command("player_queues/play_index")["args"]["index"], str)
        check("click in up next plays that track", play_from_queue)

        def tab_survives_refresh() -> None:
            page.locator('[data-ma-root="ma"] .tab-music-assistant', has_text="Kitchen").click()
            expect(title).to_have_text("Hollow Pines")
            page.locator(f'{panel} [data-ma-cmd="player_queues/play_pause"]').click()
            expect(page.locator(f'{panel} .play-music-assistant')).to_have_attribute("aria-label", "Pause", timeout=6000)
            expect(title).to_have_text("Hollow Pines")  # Kitchen stays open although Living room plays too
            assert last_command("player_queues/play_pause")["args"] == {"queue_id": "kitchen"}
        check("selected tab survives the refresh", tab_survives_refresh)

        def broken_cover() -> None:
            row = page.locator(f"{panel} .row-music-assistant", has_text="Undertow")
            expect(row.locator("span.thumb-music-assistant")).to_have_count(1)
            expect(row.locator("img")).to_have_count(0)
        check("a cover that fails to load falls back to the placeholder", broken_cover)

        def keyboard() -> None:
            row = page.locator(f"{panel} .row-music-assistant").first
            row.focus()
            page.keyboard.press("Enter")
            expect(title).to_have_text("Small Hours", timeout=6000)  # first upcoming track in Kitchen
        check("keyboard Enter on a queue row", keyboard)

        def auto_refresh_at_track_end() -> None:
            click_bar(0.995)
            expect(title).to_have_text("Afterglow Street", timeout=12000)  # next track, no click
        check("refreshes by itself when the track ends", auto_refresh_at_track_end)

        def read_only() -> None:
            ro = page.locator('[data-ma-root="ma-ro"]')
            expect(ro.locator("button")).to_have_count(0)
            assert ro.get_attribute("data-ma-token") is None
            assert "dev-token" not in ro.evaluate("el => el.outerHTML")
        check("read-only variant has no controls and no token", read_only)

        def error_message() -> None:
            widget.evaluate("el => el.setAttribute('data-ma-token', 'wrong')")
            page.locator(f'{panel} [data-ma-cmd="player_queues/next"]').click()
            expect(widget).to_have_attribute("data-ma-error", "Music Assistant login failed: Invalid or expired token",
                                             timeout=6000)
        check("wrong token shows a readable error", error_message)

        if screenshots:
            urllib.request.urlopen(urllib.request.Request(f"{MA}/_reset", method="POST"))
            page.goto("about:blank")  # stop the widgets' own refresh timers
            time.sleep(1.5)  # let Glance's 1 s widget cache expire so the reset state renders
            page.goto(GLANCE)
            page.mouse.move(0, 0)
            page.wait_for_function("window.maPlayer !== undefined", timeout=5000)
            page.wait_for_timeout(800)
            out = ROOT / "widget"
            page.locator(".widget", has=page.locator('[data-ma-root="ma"]')).screenshot(path=out / "preview.png")
            page.locator(".widget", has=page.locator('[data-ma-root="ma-big"]')).screenshot(
                path=out / "preview-big-cover.png")
            page.screenshot(path=ROOT / "dev" / "page.png", full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            page.wait_for_timeout(500)
            page.screenshot(path=ROOT / "dev" / "page-mobile.png", full_page=True)
            print("  ok  screenshots saved")

        browser.close()
        if problems:
            raise AssertionError("browser reported problems:\n" + "\n".join(problems))


if __name__ == "__main__":
    main()
