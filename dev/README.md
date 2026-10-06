# Development

Everything here runs locally without a real Music Assistant.

| File | Purpose |
|---|---|
| `mock_ma.py` | Mock Music Assistant: `POST /api`, WebSocket `/ws`, `/imageproxy`, with invented tracks, four players in the token user's player filter, someone else's browser player outside it and one broken cover |
| `make_config.py` | Writes `glance.generated.yml`: a page with the default, `big-cover` and read-only variants |
| `test_e2e.py` | Starts the mock and Glance, clicks through the widget in Chromium and checks every command |
| `sync_readme.py` | Copies `widget/music-assistant.yml` into `widget/README.md` |

## Requirements

- Python 3.11+ with `pip install aiohttp pillow playwright` and `python -m playwright install chromium`
- The [Glance binary](https://github.com/glanceapp/glance/releases) on your `PATH`, or its path in `GLANCE_BIN`

## Try the widget

```bash
python3 dev/mock_ma.py                    # terminal 1, port 8095, token "dev-token"
python3 dev/make_config.py
MA_URL=http://127.0.0.1:8095 MA_TOKEN=dev-token glance --config dev/glance.generated.yml   # terminal 2
```

Open <http://127.0.0.1:8080>. Edit `widget/music-assistant.yml`, run `make_config.py` again and
Glance reloads the config by itself.

## Against your own Music Assistant

Point `MA_URL` and `MA_TOKEN` at your server instead of the mock. `GET <MA_URL>/api-docs` lists every
command of your Music Assistant version.

## Tests

```bash
python3 dev/test_e2e.py                   # add --screenshots to refresh widget/preview*.png
python3 dev/sync_readme.py --check
```

The test uses ports 18095 and 18080, so it can run next to the dev setup above.
