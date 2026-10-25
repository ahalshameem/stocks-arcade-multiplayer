# Stocks Arcade — Online Multiplayer (Render)

This bundle adds room-code internet multiplayer to the Stocks Arcade mobile game. The MIT App Inventor app still uses a WebViewer for the HTML/JavaScript game; the online multiplayer backend is a separate Python WebSocket service.

## Features

- Create a six-character room code or join an existing room.
- Up to 6 human players per room.
- Lobby shows connected players; the host starts the match when at least 2 players are connected.
- Shared market price, chart history, news events, cash, shares, and turn order.
- Server-authoritative trades: only the current player can buy, sell, or skip.
- Host settings are taken from the host app's saved game options.
- WebSocket keepalive while the app remains connected.
- `/` and `/health` HTTP endpoints for basic service checks.

## Deploy on Render

### Option A — Blueprint (recommended)

1. Extract this ZIP.
2. Put `server.py`, `requirements.txt`, and `render.yaml` at the root of a GitHub repository and push the files to GitHub.
3. In Render, choose **New → Blueprint**, connect the repository, and apply the Blueprint. The included `render.yaml` creates a Python web service.
4. Wait for the service to finish deploying. Open `https://YOUR-SERVICE.onrender.com/health`; a healthy deployment returns JSON similar to `{"status":"healthy","rooms":0}`.
5. Copy the actual service base URL from Render, for example `https://your-service.onrender.com`.

### Option B — Create a Web Service manually

- Runtime/language: **Python 3**
- Build command: `pip install -r requirements.txt`
- Start command: `uvicorn server:app --host 0.0.0.0 --port $PORT`
- Health check path: `/health`

Use a single instance. This starter stores active rooms in process memory and does not use Redis or a database.

## Use it in the app

1. Import `StocksArcadeMobile_OnlineRooms.aia` into MIT App Inventor and build an APK.
2. On each device, open **Online Rooms**.
3. Enter the deployed Render base URL in **Your Render server URL**. The app converts `https://` to secure `wss://` and connects to `/ws` automatically.
4. One player taps **Create Room** and shares the six-character room code.
5. The other player(s) enter that code and tap **Join Room**.
6. The host taps **Start Match** once at least two players are connected.

All prices and account values are simulated. No broker, real money, or financial account is involved.

## Limits and important notes

- **Active rooms live in memory only.** A deploy, restart, process replacement, or spin-down loses rooms and current matches. Recreate the room if that happens.
- Render Free web services spin down after 15 minutes without inbound HTTP or WebSocket messages. The app sends a small WebSocket keepalive every 25 seconds while the room screen/game is connected. The first connection after a quiet period can take around a minute to wake up.
- Keep the service at one instance. Multiple instances would have separate in-memory room dictionaries and could not synchronize room state.
- Players can reconnect to a match using the same room code and same device/browser storage, but this lightweight prototype does not persist finished scores on a shared server.
- Online multiplayer uses human players only; the local offline mode still includes CPU opponents.
- Test the rebuilt APK on real Android devices. WebViewer/WebSocket behavior can vary by Android system WebView version and device battery/network settings.

## Local test

Run from this directory:

```bash
python -m pip install -r requirements.txt
uvicorn server:app --host 127.0.0.1 --port 8000
```

Then test `http://127.0.0.1:8000/health` and use `ws://127.0.0.1:8000/ws` from a browser/client. The app itself will generally use `wss://` with the public Render URL.

A two-client smoke test is included as `test_multiplayer_smoke.py`.
