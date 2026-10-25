"""Stocks Arcade room-based WebSocket server for Render.

Rooms and game state are held in memory, intended for a small hobby deployment
on a single Render Web Service instance. No real money or financial account is used.
"""
from __future__ import annotations

import asyncio
import copy
import math
import os
import random
import secrets
import string
import time
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

app = FastAPI(title="Stocks Arcade Multiplayer", version="1.0.0")
ROOMS: dict[str, dict[str, Any]] = {}
ROOM_CHARS = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
COLORS = ["#00c8ff", "#00ff75", "#ffd23b", "#ff315d", "#cb70ff", "#ffac42", "#58eaff", "#f77ad8"]
DIFFICULTY_VOL = {"Easy": 0.6, "Normal": 1.0, "Hard": 1.6, "Extreme": 2.4}
HEADLINES = [
    ("TECH BREAKTHROUGH", "Major innovation announced", 1.15),
    ("ECONOMIC SURGE", "Growth exceeds expectations", 1.08),
    ("MARKET CORRECTION", "Analysts warn of a downturn", 0.86),
    ("POLICY SHIFT", "New regulations shake the sector", 0.93),
    ("MEGA MERGER", "Two major firms join forces", 1.12),
    ("EARNINGS BEAT", "Quarterly profits beat forecasts", 1.13),
    ("SUPPLY CRISIS", "Production delays tighten supply", 0.89),
    ("AI GOLD RUSH", "AI investments surge", 1.19),
    ("INTEREST HIKE", "Rates rise and pressure prices", 0.84),
]


def clean_name(value: Any) -> str:
    name = " ".join(str(value or "Trader").split())[:22]
    return name or "Trader"


def safe_settings(raw: Any) -> dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}

    def bounded(key: str, default: float, low: float, high: float, integer: bool = False):
        try:
            n = float(raw.get(key, default))
            if not math.isfinite(n):
                n = default
        except (TypeError, ValueError):
            n = default
        n = max(low, min(high, n))
        return int(round(n)) if integer else float(n)

    difficulty = raw.get("difficulty", "Normal")
    if difficulty not in DIFFICULTY_VOL:
        difficulty = "Normal"
    return {
        "startPrice": bounded("startPrice", 10, 1, 500),
        "maxTurns": bounded("maxTurns", 50, 5, 200, True),
        "startCash": bounded("startCash", 200, 10, 5000),
        "startStocks": bounded("startStocks", 10, 0, 100, True),
        "difficulty": difficulty,
        "volatility": bool(raw.get("volatility", True)),
    }


def new_room_code() -> str:
    for _ in range(100):
        code = "".join(secrets.choice(ROOM_CHARS) for _ in range(6))
        if code not in ROOMS:
            return code
    raise RuntimeError("Could not allocate a room code. Please retry.")


def new_player(client_id: str, name: str, slot: int, settings: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": secrets.token_hex(5),
        "client_id": client_id,
        "name": clean_name(name),
        "color": COLORS[slot % len(COLORS)],
        "cash": float(settings["startCash"]),
        "stocks": int(settings["startStocks"]),
        "tradeHistory": [],
        "connected": True,
    }


def calculate_rsi(history: list[float]) -> float:
    if len(history) < 2:
        return 50.0
    n = min(14, len(history) - 1)
    changes = [history[i] - history[i - 1] for i in range(len(history) - n, len(history))]
    gains = sum(x for x in changes if x > 0) / n
    losses = sum(-x for x in changes if x < 0) / n
    if gains == 0 and losses == 0:
        return 50.0
    if losses <= 1e-12:
        return 100.0
    return 100.0 - (100.0 / (1.0 + gains / losses))


def market_step(game: dict[str, Any]) -> None:
    mult = DIFFICULTY_VOL.get(game["difficulty"], 1.0)
    sigma = (0.055 if game["volatility"] else 0.018) * mult
    u = max(1e-6, random.random())
    v = random.random()
    gauss = math.sqrt(-2.0 * math.log(u)) * math.cos(2.0 * math.pi * v)
    price = game["price"] * math.exp((0.001 - 0.5 * sigma * sigma) + sigma * gauss)
    game["news"] = None
    if game["volatility"] and random.random() < 0.10:
        title, desc, impact = random.choice(HEADLINES)
        game["news"] = {"title": title, "desc": desc, "impact": impact}
        price *= impact
    game["price"] = max(0.25, min(200000.0, price))
    game["history"].append(game["price"])
    if len(game["history"]) > 90:
        game["history"] = game["history"][-90:]
    game["rsi"] = calculate_rsi(game["history"])


def visible_player(p: dict[str, Any], room: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": p["id"], "name": p["name"], "color": p["color"],
        "cash": round(float(p.get("cash", room["settings"]["startCash"])), 2),
        "stocks": int(p.get("stocks", room["settings"]["startStocks"])),
        "connected": bool(p.get("connected")),
        "ai": False,
        "isHost": p["id"] == room["host_player_id"],
    }


def snapshot(room: dict[str, Any], player_id: str) -> dict[str, Any]:
    players = [visible_player(p, room) for p in room["players"]]
    state: dict[str, Any] = {
        "type": "state",
        "room": {
            "code": room["code"],
            "status": room["status"],
            "hostPlayerId": room["host_player_id"],
            "settings": room["settings"],
            "players": players,
        },
        "you": {"playerId": player_id, "isHost": player_id == room["host_player_id"]},
        "game": None,
    }
    if room["game"] is not None:
        g = room["game"]
        state["game"] = {
            "players": players,
            "price": g["price"],
            "history": g["history"],
            "turn": g["turn"],
            "turnIndex": g["turnIndex"],
            "maxTurns": g["maxTurns"],
            "difficulty": g["difficulty"],
            "volatility": g["volatility"],
            "news": g["news"],
            "rsi": g["rsi"],
            "ended": g["ended"],
            "quantity": 1,
            "startPrice": g["startPrice"],
        }
    return state


async def send_error(ws: WebSocket, message: str) -> None:
    try:
        await ws.send_json({"type": "error", "message": message})
    except Exception:
        pass


async def send_room_state(room: dict[str, Any]) -> None:
    # Build a unique per-player state so each client knows its own player ID.
    for pid, ws in list(room["connections"].items()):
        try:
            await ws.send_json(snapshot(room, pid))
        except Exception:
            # The disconnect handler will mark stale connections as away.
            continue


def find_player(room: dict[str, Any], player_id: str) -> dict[str, Any] | None:
    return next((p for p in room["players"] if p["id"] == player_id), None)


def sanitize_client_id(value: Any) -> str:
    value = str(value or "")[:80]
    # Restrict to a non-empty opaque identifier; never trust it as a path or command.
    if not value or any(not (ch.isalnum() or ch in "-_:") for ch in value):
        return secrets.token_hex(12)
    return value


def create_game(room: dict[str, Any]) -> None:
    settings = room["settings"]
    price = float(settings["startPrice"])
    history = [price * 0.96, price * 0.985, price * 0.975, price]
    history = [max(0.25, p) for p in history]
    for p in room["players"]:
        p["cash"] = float(settings["startCash"])
        p["stocks"] = int(settings["startStocks"])
        p["tradeHistory"] = []
    game = {
        "price": price, "history": history, "turn": 0, "turnIndex": 0,
        "maxTurns": int(settings["maxTurns"]), "difficulty": settings["difficulty"],
        "volatility": bool(settings["volatility"]), "news": None,
        "rsi": calculate_rsi(history), "ended": False, "startPrice": price,
    }
    room["game"] = game
    room["status"] = "in_game"
    market_step(game)
    game["turn"] = 0
    game["turnIndex"] = 0


def apply_action(room: dict[str, Any], player_id: str, action: str, quantity: Any) -> str | None:
    game = room["game"]
    if room["status"] != "in_game" or not game or game["ended"]:
        return "The match is not running."
    players = room["players"]
    if not players:
        return "This room has no players."
    current = players[game["turnIndex"]]
    if current["id"] != player_id:
        return f"It is {current['name']}'s turn."
    p = current
    action = str(action or "").lower()
    try:
        qty = int(quantity)
    except (TypeError, ValueError):
        qty = 1
    if action == "buy":
        if qty == -1:
            qty = min(200, math.floor(p["cash"] / game["price"]))
        qty = max(1, min(200, qty))
        cost = qty * game["price"]
        if cost > p["cash"] + 0.005:
            return "Not enough cash for that quantity."
        p["cash"] -= cost
        p["stocks"] += qty
        p["tradeHistory"].append({"action": "BUY", "qty": qty, "price": game["price"]})
    elif action == "sell":
        if p["stocks"] < 1:
            return "You have no shares to sell."
        if qty == -1:
            qty = p["stocks"]
        qty = max(1, min(200, qty, p["stocks"]))
        value = qty * game["price"]
        p["stocks"] -= qty
        p["cash"] += value
        p["tradeHistory"].append({"action": "SELL", "qty": qty, "price": game["price"]})
    elif action not in ("skip", "hold"):
        return "Unknown action. Choose buy, sell, or skip."

    game["turn"] += 1
    market_step(game)
    if game["turn"] >= game["maxTurns"]:
        game["ended"] = True
        room["status"] = "game_over"
    else:
        game["turnIndex"] = (game["turnIndex"] + 1) % len(players)
    return None


@app.get("/")
async def root():
    return {"service": "Stocks Arcade Multiplayer", "websocket": "/ws", "status": "ok"}


@app.get("/health")
async def health():
    return {"status": "healthy", "rooms": len(ROOMS)}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    client_id = ""
    room_code: str | None = None
    player_id: str | None = None
    try:
        while True:
            try:
                raw = await websocket.receive_text()
                if len(raw) > 16_000:
                    await send_error(websocket, "Message too large.")
                    continue
                import json
                message = json.loads(raw)
            except json.JSONDecodeError:
                await send_error(websocket, "Invalid JSON message.")
                continue
            if not isinstance(message, dict):
                await send_error(websocket, "Invalid message format.")
                continue
            kind = str(message.get("type", ""))

            if kind == "ping":
                await websocket.send_json({"type": "pong", "time": time.time()})
                continue

            if kind == "create_room":
                if room_code:
                    await send_error(websocket, "You are already connected to a room.")
                    continue
                client_id = sanitize_client_id(message.get("client_id"))
                settings = safe_settings(message.get("settings"))
                code = new_room_code()
                player = new_player(client_id, message.get("player_name"), 0, settings)
                room = {
                    "code": code, "status": "lobby", "host_player_id": player["id"],
                    "settings": settings, "players": [player], "connections": {player["id"]: websocket},
                    "game": None, "created_at": time.time(),
                }
                ROOMS[code] = room
                room_code, player_id = code, player["id"]
                await send_room_state(room)
                continue

            if kind == "join_room":
                if room_code:
                    await send_error(websocket, "You are already connected to a room.")
                    continue
                requested_code = str(message.get("room_code", "")).strip().upper()
                room = ROOMS.get(requested_code)
                if not room:
                    await send_error(websocket, "Room not found. Check the code and try again.")
                    continue
                client_id = sanitize_client_id(message.get("client_id"))
                existing = next((p for p in room["players"] if p["client_id"] == client_id), None)
                if existing:
                    player = existing
                    player["connected"] = True
                    room["connections"][player["id"]] = websocket
                else:
                    if room["status"] != "lobby":
                        await send_error(websocket, "This match has already started.")
                        continue
                    if len(room["players"]) >= 6:
                        await send_error(websocket, "This room is full (6 players maximum).")
                        continue
                    player = new_player(client_id, message.get("player_name"), len(room["players"]), room["settings"])
                    room["players"].append(player)
                    room["connections"][player["id"]] = websocket
                room_code, player_id = room["code"], player["id"]
                await send_room_state(room)
                continue

            if kind == "start_game":
                room = ROOMS.get(room_code or "")
                if not room or not player_id:
                    await send_error(websocket, "Join a room first.")
                    continue
                if player_id != room["host_player_id"]:
                    await send_error(websocket, "Only the room host can start the match.")
                    continue
                if room["status"] != "lobby":
                    await send_error(websocket, "This match has already started.")
                    continue
                connected = [p for p in room["players"] if p["connected"] and p["id"] in room["connections"]]
                if len(connected) < 2:
                    await send_error(websocket, "At least 2 connected players are required.")
                    continue
                # Keep only connected lobby members at match start.
                room["players"] = connected
                create_game(room)
                await send_room_state(room)
                continue

            if kind == "action":
                room = ROOMS.get(room_code or "")
                if not room or not player_id:
                    await send_error(websocket, "Join a room first.")
                    continue
                error = apply_action(room, player_id, message.get("action", ""), message.get("quantity", 1))
                if error:
                    await send_error(websocket, error)
                else:
                    await send_room_state(room)
                continue

            if kind == "leave_room":
                room = ROOMS.get(room_code or "")
                if room and player_id:
                    p = find_player(room, player_id)
                    if p:
                        if room["status"] == "lobby":
                            room["players"] = [x for x in room["players"] if x["id"] != player_id]
                            room["connections"].pop(player_id, None)
                            if room["players"] and room["host_player_id"] == player_id:
                                room["host_player_id"] = room["players"][0]["id"]
                            elif not room["players"]:
                                ROOMS.pop(room["code"], None)
                        else:
                            p["connected"] = False
                            room["connections"].pop(player_id, None)
                    if room_code in ROOMS:
                        await send_room_state(room)
                room_code = None
                player_id = None
                await websocket.close()
                return

            await send_error(websocket, "Unknown message type.")
    except WebSocketDisconnect:
        pass
    except Exception:
        # Send a clear, non-sensitive error to the client and let the socket close.
        try:
            await send_error(websocket, "Connection error. Reconnect and try again.")
        except Exception:
            pass
    finally:
        room = ROOMS.get(room_code or "")
        if room and player_id:
            p = find_player(room, player_id)
            # If a newer socket has reclaimed this player, don't mark it disconnected.
            if room["connections"].get(player_id) is websocket:
                room["connections"].pop(player_id, None)
                if p:
                    p["connected"] = False
                if room["status"] == "lobby" and room["host_player_id"] == player_id:
                    next_host = next((x for x in room["players"] if x["connected"] and x["id"] != player_id), None)
                    if next_host:
                        room["host_player_id"] = next_host["id"]
                if room["status"] == "lobby" and not room["players"]:
                    ROOMS.pop(room["code"], None)
                elif room["code"] in ROOMS:
                    await send_room_state(room)
