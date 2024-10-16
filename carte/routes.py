from __future__ import annotations

import secrets
import shelve
from collections.abc import Mapping
from typing import Any

import aiohttp_jinja2
from aiohttp import web

from carte import app_keys
from carte.exc import CmdError
from carte.games import BaseGame
from carte.types import GameStatus, SavedGame

routes = web.RouteTableDef()


@routes.get("/", name="index")
@aiohttp_jinja2.template("index.html")
async def index(request: web.Request) -> Mapping[str, Any]:  # noqa: ARG001
    return {}


@routes.get("/status")
@aiohttp_jinja2.template("status.html")
async def status(request: web.Request) -> Mapping[str, Any]:
    with shelve.open(request.app[app_keys.games_shelf_path]) as shelf:
        saved_games = {
            key.partition("__")[::2]: saved_game.game
            for key, saved_game in shelf.items()
            if saved_game.is_valid
        }
    return {
        "active_games": request.app[app_keys.games],
        "saved_games": saved_games,
    }


@routes.get("/{game_type}", name="game")
@aiohttp_jinja2.template("game.html")
async def game(request: web.Request) -> Mapping[str, Any]:
    game_type = request.match_info["game_type"]
    if game_type not in BaseGame.GAMES:
        raise web.HTTPNotFound
    return {"game": game_type, "card_family": BaseGame.GAMES[game_type].card_family}


@routes.get("/ws/{game_type}")
@routes.get("/ws/{game_type}/{game_id}")
async def websocket(request: web.Request) -> web.WebSocketResponse:
    try:
        session_id = request.cookies["session_id"]
    except KeyError:
        session_id = secrets.token_hex()

    game_type = request.match_info["game_type"]
    try:
        game_id = request.match_info["game_id"]
    except KeyError:
        try:
            game_id = BaseGame.WAITING_GAMES_IDS[BaseGame.GAMES[game_type]]
        except KeyError:
            game_id = secrets.token_hex()
            BaseGame.WAITING_GAMES_IDS[BaseGame.GAMES[game_type]] = game_id

    game = request.app[app_keys.games].get((game_type, game_id))
    if game is None:
        with shelve.open(request.app[app_keys.games_shelf_path]) as shelf:
            saved_game = shelf.get(f"{game_type}__{game_id}")
            if saved_game and saved_game.is_valid:
                game = saved_game.game
                del shelf[f"{game_type}__{game_id}"]
    if game is None:
        try:
            game = BaseGame.GAMES[game_type](game_id)
        except (KeyError, ValueError) as e:
            raise web.HTTPBadRequest from e
    request.app[app_keys.games][game_type, game_id] = game

    player = game.add_player(session_id)

    ws = web.WebSocketResponse(heartbeat=15)
    ws.set_cookie("session_id", session_id, max_age=24 * 60 * 60, samesite="lax")
    await ws.prepare(request)

    if player:
        player.websockets.add(ws)
    game.websockets.add(ws)
    request.app[app_keys.websockets].add(ws)
    try:
        async for msg in ws:
            try:
                await game.handle_raw_cmd(ws, player, msg)
            except CmdError as e:
                await game.handle_cmd(ws, player, "current_state")
                args = ["error", str(e)]
                if e.command is not None:
                    args.append(e.command)
                await ws.send_str("|".join(x.replace("|", "") for x in args))

    finally:
        if player:
            player.websockets.discard(ws)
            if (
                len(player.websockets) == 0
                and game._game_status is GameStatus.NOT_STARTED
            ):
                game._players.remove(player)

        game.websockets.discard(ws)
        if (
            len(game.websockets) == 0
            and game._game_status is not GameStatus.NOT_STARTED
        ):
            with shelve.open(request.app[app_keys.games_shelf_path]) as shelf:
                shelf[f"{game_type}__{game_id}"] = SavedGame(game, game.version)

        request.app[app_keys.websockets].discard(ws)

    return ws
