# API notes

What the widget relies on, checked in the source code of Music Assistant **2.10.5** and `main` at
`5fb0acd` (2026-10-06, 2.11 beta), and Glance **v0.8.6**. Re-check these when either project
releases a new version.

## Music Assistant

### HTTP API: `POST /api`

- Body: `{"message_id": "...", "command": "...", "args": {...}}`, header `Authorization: Bearer <token>`.
- The response is the raw command result (a JSON array or object), not wrapped. Errors are plain text
  with status 400, 401, 403 or 500.
- **No CORS.** The webserver only answers CORS preflight requests for `/info` and `/auth/login`
  (`controllers/webserver/controller.py`, `setup()`), so a browser on another origin cannot call
  `/api`. The widget only uses it from the Glance server.

### WebSocket API: `GET /ws`

- On connect the server sends its `ServerInfoMessage` (no `message_id`).
- Authenticate with `{"message_id": "auth", "command": "auth", "args": {"token": "<token>"}}`;
  the reply is `{"message_id": "auth", "result": {"authenticated": true, "user": {...}}}`.
- Commands use the same names and arguments as the HTTP API. Replies:
  - success: `{"message_id": ..., "result": ..., "partial": false}`
  - error: `{"message_id": ..., "error_code": <int>, "details": "<text>"}`
- After authentication the connection receives every event, for example
  `{"event": "queue_updated", "object_id": "<queue_id>", "data": {...}}`. Event types used later
  for live updates: `player_updated`, `queue_updated`, `queue_items_updated`, `queue_time_updated`.
- **No Origin check** in `controllers/webserver/websocket_client.py`, and browsers do not apply
  CORS to WebSockets. This is why the controls work from the Glance page. If Music Assistant ever
  starts checking the Origin header, the controls need a different path (for example asking
  upstream for CORS on `/api`).

### Commands used

| Command | Arguments | Scope |
|---|---|---|
| `player_queues/all` | none | `queues.read` |
| `player_queues/items` | `queue_id`, `limit` (default 500), `offset` | `queues.read` |
| `player_queues/play_pause` | `queue_id` | `queues.control` |
| `player_queues/previous`, `player_queues/next` | `queue_id` | `queues.control` |
| `player_queues/seek` | `queue_id`, `position` (seconds, int) | `queues.control` |
| `player_queues/shuffle` | `queue_id`, `shuffle_enabled` | `queues.control` |
| `player_queues/repeat` | `queue_id`, `repeat_mode` (`off`, `one`, `all`) | `queues.control` |
| `player_queues/play_index` | `queue_id`, `index` (int, or a `queue_item_id` string) | `queues.control` |
| `players/cmd/volume_up`, `players/cmd/volume_down` | `player_id` | `players.control` |
| `players/cmd/volume_set` | `player_id`, `volume_level` | `players.control` |
| `players/add_currently_playing_to_favorites` | `player_id` | `library.write` (not for guests) |

`queue_id` equals the `player_id` unless the player is grouped.

### Users, roles and scopes

- Built-in roles: `admin` (everything), `user`, `guest`, `service`
  (`controllers/webserver/helpers/auth_middleware.py`, `ROLE_SCOPES`).
- `guest` holds `library.read`, `players.read`, `players.control`, `queues.read`, `queues.control`,
  `providers.read`, `config.players.read`: exactly what the widget needs, so the token in the page
  cannot change settings, users or providers.
- A user can be restricted to a list of players (`player_filter`). Control commands check it
  (`_check_player_permission` in `controllers/player_queues/controller.py`), but
  `player_queues/all` still returns every queue. The widget's `players` option hides the others.
- Custom roles with chosen scopes (`auth/role/create`) exist on `main` (2.11), not in 2.10.5. A
  custom role "guest + `library.write`" would allow a favourite button without the full user role.

### Images

- Every `MediaItemImage` in an API response carries a `proxy_id`. The image is served at
  `<server>/imageproxy/<proxy_id>?size=<n>`, where `n` is one of `0, 80, 160, 256, 512, 1024`.
- The image proxy needs no token and sends `Access-Control-Allow-Origin: *`, so `<img>` tags in
  the browser work directly.

### Fields used

- `PlayerQueue`: `queue_id`, `display_name`, `available`, `state` (`idle`, `paused`, `playing`),
  `items` (count), `current_index`, `elapsed_time`, `elapsed_time_last_updated` (unix time),
  `shuffle_enabled`, `repeat_mode`, `current_item`.
- `QueueItem`: `queue_item_id`, `name`, `duration`, `image.proxy_id`, `media_item.name`,
  `media_item.artists[].name`, `media_item.album.name`,
  `streamdetails.stream_metadata.{title, artist, album}` (radio).
- The current position is `elapsed_time` plus the time since `elapsed_time_last_updated` while
  playing.

Example responses (from the mock server) are in [`fixtures/`](fixtures/).

## Glance (v0.8.6)

- Custom API templates use Go `html/template`. Helpers used: `newRequest`, `withHeader`,
  `withStringBody` (makes the request a POST), `getResponse`, `add`/`sub`/`mul`/`div` (int or
  float64, never int64), `toInt`, `toFloat`, `mod`, `now`, `parseTime "unix"`, `replaceAll`,
  `trimSuffix`, `.Options.StringOr/IntOr/BoolOr`. `.JSON.Array ""` iterates a top-level array.
- Widget cache: the next update is scheduled `cache` after an update finishes; `cache: 0` falls
  back to the widget default (1 hour for custom-api). With `cache: 1s` a refresh has to wait more
  than a second after the previous one, so the widget waits 1.5 s after a command.
- A non-2xx response with a valid JSON body is not treated as an error; a non-JSON error body
  (like Music Assistant's) is shown as `401 Unauthorized` and similar.
- Page content is loaded from `{pageData.baseURL}/api/pages/{pageData.slug}/content/` and inserted
  with `innerHTML`: `<script>` tags do not run, inline event handlers do. The widget defines its
  helper in the `onload` handler of a 1×1 image and re-renders itself by fetching that endpoint and
  replacing only its own element.
- Do not use `loading="lazy"` on images: Glance hides lazy images until its own setup code marks
  them, which does not run again for content replaced later.
- `$include` replaces the whole line including the leading `- `, so an included widget file starts
  with `- type: ...`.
- No Content-Security-Policy is set, so inline handlers are allowed.
