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
  - error: `{"message_id": ..., "error_code": <int>, "details": "<text>"}`. `details` is translated
    by error type (`translation_key`), so the specific English message is replaced: a player outside
    the player filter arrives as error 22, "You do not have permission to perform this action."
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
| `players/all` | none (only `player_id` is used) | `players.read` |
| `player_queues/items` | `queue_id`, `limit` (default 500), `offset` | `queues.read` |
| `player_queues/play_pause` | `queue_id` | `queues.control` |
| `player_queues/previous`, `player_queues/next` | `queue_id` | `queues.control` |
| `player_queues/seek` | `queue_id`, `position` (seconds, int) | `queues.control` |
| `player_queues/shuffle` | `queue_id`, `shuffle_enabled` | `queues.control` |
| `player_queues/repeat` | `queue_id`, `repeat_mode` (`off`, `one`, `all`) | `queues.control` |
| `player_queues/play_index` | `queue_id`, `index` (int, or a `queue_item_id` string) | `queues.control` |
| `players/cmd/volume_up`, `players/cmd/volume_down` | `player_id` | `players.control` |
| `players/cmd/volume_set` | `player_id`, `volume_level` | `players.control` |
| `music/item_by_uri` | `uri` | `library.read` |
| `music/favorites/add_item` | `item` (a URI) | `library.write` (not for guests) |
| `music/favorites/remove_item` | `media_type`, `library_item_id` | `library.write` (not for guests) |

`queue_id` equals the `player_id` unless the player is grouped.

Favourites: a queue item keeps the `favorite` flag its media item had when it was queued, and
`music/favorites/*` do not update it. `music/item_by_uri` with `current_item.media_item.uri` returns
the library item when there is one (`MediaControllerBase.get` prefers it), with the current flag;
about 30 to 50 ms on a real server. A favourite is always a library item (`provider` is `library`),
so its `item_id` is the `library_item_id` for removing it; `add_item` adds a provider item to the
library first. `players/add_currently_playing_to_favorites` exists too, but for radio it searches
the track from the stream title, which the widget could not show as a state.

### Users, roles and scopes

- Built-in roles: `admin` (everything), `user`, `guest`, `service`
  (`controllers/webserver/helpers/auth_middleware.py`, `ROLE_SCOPES`).
- `guest` holds `library.read`, `players.read`, `players.control`, `queues.read`, `queues.control`,
  `providers.read`, `config.players.read`: exactly what the widget needs, but guests are temporary
  (party, quiz and dashboard sessions). Since 2.10.0 `auth/token/create` refuses long-lived tokens
  for them and their other tokens expire after one day (`TOKEN_GUEST_EXPIRATION` in
  `controllers/webserver/auth.py`), so the widget cannot use a guest.
- `user` adds `library.write`, `config.providers.read`, `config.core.read`, `system.read` and
  `users.invite` (only casts Music Assistant dashboards, `dashboard/show`). It cannot change
  settings, users or providers. This is the role for the widget's token.
- Long-lived tokens expire after 365 days (`TOKEN_LONG_LIVED_EXPIRATION`) and do not renew. The
  2.10.5 frontend says 10 years; the server decides.
- A user can be restricted to a list of players (`player_filter`). Control commands check it
  (`_check_player_permission` in `controllers/player_queues/controller.py`) and `players/all`
  returns only those players (`all_players` in `controllers/players/controller.py`), but
  `player_queues/all` still returns every queue, including other people's browser players. The
  widget shows only queues whose id is in `players/all`; if that request fails it shows them all.
  A private client player (a browser or app session) is always usable by the connection that
  announced it, which never applies to the widget.
- Custom roles with chosen scopes (`auth/role/create`) exist on `main` (2.11), not in 2.10.5. The
  guest token block checks the role id `guest` only, so a custom role with the guest scopes (plus
  `library.write` for the favourite button) would give the widget a smaller token than `user`.

### Images

- Every `MediaItemImage` in an API response carries a `proxy_id`. The image is served at
  `<server>/imageproxy/<proxy_id>?size=<n>`, where `n` is one of `0, 80, 160, 256, 512, 1024`.
- The image proxy needs no token and sends `Access-Control-Allow-Origin: *`, so `<img>` tags in
  the browser work directly.
- It answers 404 when it cannot fetch the original, for example a radio logo URL that now
  redirects (seen with a radiobrowser station). The widget then falls back to the note icon.

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
