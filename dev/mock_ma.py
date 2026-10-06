#!/usr/bin/env python3
"""Minimal Music Assistant mock for developing the Glance widget.

Implements only what the widget uses, with the same shapes as Music Assistant 2.10:

  POST /api                       JSON-RPC, Bearer token, no CORS headers (like MA)
  GET  /ws                        WebSocket API: server_info -> auth -> commands, pushes events
  GET  /imageproxy/<id>?size=N    generated cover art, public (like MA)

Extra endpoints for tests:

  GET  /_log                      every command received (transport, command, args)
  POST /_reset                    restore the initial state

Run:  python3 dev/mock_ma.py            (port 8095, token "dev-token")
Env:  MOCK_MA_PORT, MOCK_MA_TOKEN
"""

from __future__ import annotations

import colorsys
import copy
import hashlib
import io
import json
import os
import time

from aiohttp import WSMsgType, web
from PIL import Image, ImageDraw

TOKEN = os.environ.get("MOCK_MA_TOKEN", "dev-token")
PORT = int(os.environ.get("MOCK_MA_PORT", "8095"))
ALLOWED_SIZES = {0, 80, 160, 256, 512, 1024}
# The token belongs to a user with this player filter; web_chrome is someone else's browser player
PLAYER_FILTER = {"living_room", "kitchen", "bedroom", "office"}
# MA sends errors in the connection's language; this is the English text for a missing permission
NO_PERMISSION = "You do not have permission to perform this action."

# Invented catalogue: (title, artists, album, duration in seconds)
CATALOGUE = [
    ("Glass Harbour", ["Marta Veil"], "Low Tide Radio", 214),
    ("Paper Lanterns", ["Marta Veil", "Ino"], "Low Tide Radio", 187),
    ("Copper Sky", ["The Quiet Engines"], "Field Recordings", 243),
    ("Night Ferry", ["Lumen & Vale"], "Night Ferry", 229),
    ("Slow Orbit", ["Kestrel Avenue"], "Satellites", 201),
    ("Hollow Pines", ["Ada North"], "Winter Bloom", 256),
    ("Small Hours", ["The Quiet Engines"], "Field Recordings", 198),
    ("Afterglow Street", ["Neon Harbor"], "Afterglow Street", 236),
    ("Undertow", ["Marta Veil"], "Low Tide Radio", 262),
    ("Signal Fires", ["Kestrel Avenue", "Ada North"], "Satellites", 219),
]


def _image(album: str) -> dict:
    path = f"https://covers.example/{album.replace(' ', '_')}.jpg"
    return {
        "type": "thumb",
        "path": path,
        "provider": "mock",
        "remotely_accessible": False,
        "proxy_id": hashlib.sha256(f"mock/{path}".encode()).hexdigest(),
    }


def _queue_item(queue_id: str, position: int, entry: tuple) -> dict:
    title, artists, album, duration = entry
    track_id = str(CATALOGUE.index(entry) + 1)
    return {
        "queue_id": queue_id,
        "queue_item_id": hashlib.md5(f"{queue_id}/{position}".encode()).hexdigest(),
        "name": f"{', '.join(artists)} - {title}",
        "duration": duration,
        "sort_index": position,
        "streamdetails": None,
        "media_item": {
            "item_id": track_id,
            "provider": "library",
            "name": title,
            "media_type": "track",
            "uri": f"library://track/{track_id}",
            "duration": duration,
            "artists": [
                {
                    "item_id": str(100 + i),
                    "provider": "library",
                    "name": artist,
                    "media_type": "artist",
                    "uri": f"library://artist/{100 + i}",
                }
                for i, artist in enumerate(artists)
            ],
            "album": {
                "item_id": f"a{track_id}",
                "provider": "library",
                "name": album,
                "media_type": "album",
                "uri": f"library://album/a{track_id}",
            },
        },
        "image": _image(album),
        "index": 0,
        "available": True,
    }


def _queue(queue_id: str, name: str, state: str, length: int, index: int | None,
           elapsed: float, available: bool = True, start: int = 0) -> dict:
    items = [_queue_item(queue_id, i, CATALOGUE[(start + i) % len(CATALOGUE)]) for i in range(length)]
    return {
        "queue": {
            "queue_id": queue_id,
            "active": state != "idle",
            "display_name": name,
            "available": available,
            "items": length,
            "shuffle_enabled": False,
            "repeat_mode": "off",
            "crossfade_enabled": False,
            "autoplay_enabled": False,
            "current_index": index,
            "index_in_buffer": index,
            "ended": False,
            "elapsed_time": elapsed,
            # MA reports when elapsed_time was measured; clients add the time since then
            "elapsed_time_last_updated": time.time() - (12 if state == "playing" else 0),
            "playback_speed": 1.0,
            "state": state,
            "current_item": items[index] if index is not None else None,
            "next_item": items[index + 1] if index is not None and index + 1 < length else None,
            "flow_mode": False,
            "resume_pos": 0,
            "is_dynamic": False,
            "extra_attributes": {},
        },
        "items": items,
        "volume": 35,
    }


def initial_state() -> dict:
    return {
        "living_room": _queue("living_room", "Living room", "playing", 38, 3, 51.0),
        "kitchen": _queue("kitchen", "Kitchen", "paused", 12, 0, 95.0, start=5),
        "bedroom": _queue("bedroom", "Bedroom", "idle", 0, None, 0.0),
        "office": _queue("office", "Office", "idle", 4, 0, 0.0, available=False, start=2),
        "web_chrome": _queue("web_chrome", "Web (Chrome on Mac)", "playing", 6, 1, 20.0, start=7),
    }


# Cover art the image proxy cannot fetch, like a radio logo whose URL moved
BROKEN_COVERS = {_image("Low Tide Radio")["proxy_id"]}


class MockMA:
    def __init__(self) -> None:
        self.state = initial_state()
        self.favorites: set[str] = set()
        self.log: list[dict] = []
        self.sockets: set[web.WebSocketResponse] = set()

    # -- playback simulation -------------------------------------------------
    def _elapsed(self, q: dict) -> float:
        if q["state"] != "playing":
            return q["elapsed_time"]
        return q["elapsed_time"] + time.time() - q["elapsed_time_last_updated"]

    def _set_position(self, data: dict, index: int | None, elapsed: float, state: str) -> None:
        q, items = data["queue"], data["items"]
        q["current_index"] = index
        q["index_in_buffer"] = index
        q["current_item"] = items[index] if index is not None else None
        q["next_item"] = items[index + 1] if index is not None and index + 1 < len(items) else None
        q["elapsed_time"] = float(elapsed)
        q["elapsed_time_last_updated"] = time.time()
        q["state"] = state
        q["active"] = state != "idle"

    def tick(self) -> None:
        """Advance playing queues whose track has ended."""
        for data in self.state.values():
            q = data["queue"]
            while q["state"] == "playing" and q["current_item"]:
                over = self._elapsed(q) - q["current_item"]["duration"]
                if over < 0:
                    break
                nxt = q["current_index"] + 1
                if nxt >= len(data["items"]):
                    if q["repeat_mode"] == "all":
                        nxt = 0
                    else:
                        self._set_position(data, q["current_index"], q["current_item"]["duration"], "idle")
                        break
                self._set_position(data, nxt, over, "playing")

    # -- commands --------------------------------------------------------------
    def _queue_data(self, args: dict, key: str = "queue_id") -> dict:
        qid = args.get(key)
        if qid not in self.state:
            raise ValueError(f"Unknown player {qid}")
        return self.state[qid]

    def run(self, command: str, args: dict | None, transport: str):
        args = args or {}
        self.log.append({"transport": transport, "command": command, "args": args, "ts": time.time()})
        self.tick()
        if command == "player_queues/all":
            return [d["queue"] for d in self.state.values()]
        if command == "player_queues/get":
            return self._queue_data(args)["queue"]
        if command == "player_queues/items":
            data = self._queue_data(args)
            offset, limit = int(args.get("offset", 0)), int(args.get("limit", 500))
            return data["items"][offset: offset + limit]
        if command == "players/all":  # MA applies the player filter here, not to player_queues/all
            return [{"player_id": k, "name": d["queue"]["display_name"], "volume_level": d["volume"]}
                    for k, d in self.state.items() if k in PLAYER_FILTER]

        if command == "music/item_by_uri":  # MA returns the library item, with its current favourite flag
            uri = args["uri"]
            for i, (title, _, _, _) in enumerate(CATALOGUE):
                if uri == f"library://track/{i + 1}":
                    return {"item_id": str(i + 1), "provider": "library", "media_type": "track", "uri": uri,
                            "name": title, "favorite": uri in self.favorites}
            raise ValueError(f"Unknown item {uri}")
        if command == "music/favorites/add_item":
            self.favorites.add(args["item"])
            return None
        if command == "music/favorites/remove_item":
            self.favorites.discard(f"library://{args['media_type']}/{args['library_item_id']}")
            return None

        target = args.get("queue_id", args.get("player_id"))
        if target in self.state and target not in PLAYER_FILTER:
            raise PermissionError(f"glance does not have access to player {target}")

        if command.startswith("players/cmd/volume"):
            data = self._queue_data(args, "player_id")
            if command.endswith("volume_up"):
                data["volume"] = min(100, data["volume"] + 5)
            elif command.endswith("volume_down"):
                data["volume"] = max(0, data["volume"] - 5)
            else:
                data["volume"] = int(args["volume_level"])
            return None

        data = self._queue_data(args)
        q = data["queue"]
        if not data["items"]:
            raise ValueError("Queue is empty")
        index = q["current_index"] or 0
        if command == "player_queues/play_pause":
            new_state = "paused" if q["state"] == "playing" else "playing"
            self._set_position(data, index, self._elapsed(q), new_state)
        elif command == "player_queues/next":
            self._set_position(data, (index + 1) % len(data["items"]), 0, "playing")
        elif command == "player_queues/previous":
            target = index if self._elapsed(q) > 5 else max(0, index - 1)
            self._set_position(data, target, 0, "playing")
        elif command == "player_queues/seek":
            self._set_position(data, index, int(args["position"]), q["state"])
        elif command == "player_queues/shuffle":
            q["shuffle_enabled"] = bool(args["shuffle_enabled"])
        elif command == "player_queues/repeat":
            if args["repeat_mode"] not in ("off", "one", "all"):
                raise ValueError("Invalid repeat mode")
            q["repeat_mode"] = args["repeat_mode"]
        elif command == "player_queues/play_index":
            target = args["index"]
            if isinstance(target, str):
                ids = [i["queue_item_id"] for i in data["items"]]
                if target not in ids:
                    raise ValueError("Unknown queue item")
                target = ids.index(target)
            self._set_position(data, int(target), 0, "playing")
        else:
            raise LookupError(f"Invalid Command: {command}")
        return None

    async def broadcast(self, qid: str) -> None:
        if qid not in self.state:
            return
        event = json.dumps({"event": "queue_updated", "object_id": qid, "data": self.state[qid]["queue"]})
        for ws in list(self.sockets):
            if not ws.closed:
                await ws.send_str(event)


mock = MockMA()


def _authorized(request: web.Request) -> bool:
    return request.headers.get("Authorization", "") == f"Bearer {TOKEN}"


async def handle_api(request: web.Request) -> web.Response:
    if not _authorized(request):
        return web.Response(status=401, text="Authentication required",
                            headers={"WWW-Authenticate": 'Bearer realm="Music Assistant"'})
    try:
        msg = json.loads(await request.read())
        result = mock.run(msg["command"], msg.get("args"), "http")
    except PermissionError as err:
        return web.Response(status=403, text=str(err))
    except LookupError as err:
        return web.Response(status=400, text=str(err))
    except (ValueError, KeyError) as err:
        return web.Response(status=400, text=str(err))
    await mock.broadcast((msg.get("args") or {}).get("queue_id", ""))
    return web.json_response(result)


async def handle_ws(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    await ws.send_json({"server_id": "mock", "server_version": "2.10.5", "schema_version": 32,
                        "min_supported_schema_version": 24, "onboard_done": True})
    authenticated = False
    try:
        async for raw in ws:
            if raw.type != WSMsgType.TEXT:
                continue
            msg = json.loads(raw.data)
            mid = msg.get("message_id")
            if msg.get("command") == "auth":
                if (msg.get("args") or {}).get("token") == TOKEN:
                    authenticated = True
                    mock.sockets.add(ws)
                    await ws.send_json({"message_id": mid, "result": {"authenticated": True}, "partial": False})
                else:
                    await ws.send_json({"message_id": mid, "error_code": 20, "details": "Invalid or expired token"})
                continue
            if not authenticated:
                await ws.send_json({"message_id": mid, "error_code": 20,
                                    "details": "Authentication required. Please send auth command first."})
                continue
            try:
                result = mock.run(msg["command"], msg.get("args"), "ws")
            except PermissionError:
                await ws.send_json({"message_id": mid, "error_code": 22, "details": NO_PERMISSION})
                continue
            except LookupError as err:
                await ws.send_json({"message_id": mid, "error_code": 1, "details": str(err)})
                continue
            except (ValueError, KeyError) as err:
                await ws.send_json({"message_id": mid, "error_code": 999, "details": str(err)})
                continue
            await ws.send_json({"message_id": mid, "result": result, "partial": False})
            await mock.broadcast((msg.get("args") or {}).get("queue_id")
                                 or (msg.get("args") or {}).get("player_id", ""))
    finally:
        mock.sockets.discard(ws)
    return ws


_covers: dict[tuple[str, int], bytes] = {}


async def handle_imageproxy(request: web.Request) -> web.Response:
    image_id = request.match_info["image_id"]
    size = int(request.query.get("size", "0") or 0)
    if size not in ALLOWED_SIZES:
        return web.Response(status=400, text="Invalid size")
    if image_id in BROKEN_COVERS:
        return web.Response(status=404)
    px = size or 512
    key = (image_id, px)
    if key not in _covers:
        seed = int(image_id[:8], 16) if len(image_id) >= 8 else 0
        hue = (seed % 360) / 360
        top = tuple(int(c * 255) for c in colorsys.hls_to_rgb(hue, 0.55, 0.55))
        bottom = tuple(int(c * 255) for c in colorsys.hls_to_rgb((hue + 0.12) % 1, 0.22, 0.5))
        img = Image.new("RGB", (px, px))
        draw = ImageDraw.Draw(img)
        for y in range(px):
            t = y / max(px - 1, 1)
            draw.line([(0, y), (px, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(top, bottom)))
        r = px * (0.18 + (seed % 7) / 40)
        cx, cy = px * (0.3 + (seed % 5) / 12), px * (0.35 + (seed % 3) / 10)
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=tuple(min(255, c + 60) for c in top))
        buf = io.BytesIO()
        img.save(buf, "PNG")
        _covers[key] = buf.getvalue()
    return web.Response(body=_covers[key], content_type="image/png",
                        headers={"Cache-Control": "max-age=3600", "Access-Control-Allow-Origin": "*"})


async def handle_log(_: web.Request) -> web.Response:
    return web.json_response(mock.log)


async def handle_reset(_: web.Request) -> web.Response:
    mock.state = initial_state()
    mock.favorites.clear()
    mock.log.clear()
    return web.json_response({"ok": True})


def make_app() -> web.Application:
    app = web.Application()
    app.router.add_post("/api", handle_api)
    app.router.add_get("/ws", handle_ws)
    app.router.add_get("/imageproxy/{image_id}", handle_imageproxy)
    app.router.add_get("/_log", handle_log)
    app.router.add_post("/_reset", handle_reset)
    return app


if __name__ == "__main__":
    print(f"Mock Music Assistant on http://127.0.0.1:{PORT}  (token: {TOKEN})")
    web.run_app(make_app(), host="0.0.0.0", port=PORT, print=None)
