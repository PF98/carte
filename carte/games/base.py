from __future__ import annotations

import asyncio
import random
import types
from collections.abc import Callable, Iterable, Iterator
from enum import Enum
from typing import (
    Any,
    ClassVar,
    Generic,
    TypeVar,
    get_args,
    get_origin,
    get_type_hints,
    overload,
)
from weakref import WeakSet

import aiohttp
from aiohttp import web

from carte.exc import CmdError
from carte.types import (
    Card,
    CardBack,
    CardFamily,
    CardNumber,
    CmdFunc,
    Command,
    GameStatus,
    Sendable,
    Suit,
)


def cmd(
    *, current_player: bool = False, **kwargs: Enum
) -> Callable[[CmdFunc], Command[CmdFunc]]:
    def decorator(func: CmdFunc) -> Command[CmdFunc]:
        return Command(func, current_player, kwargs)

    return decorator


class Player:
    def __init__(self, token: str, name: str = "") -> None:
        self._token = token
        self.name = name
        self.ready = True
        self.hand: list[Card] = []
        self.points: list[Card] = []
        self.websockets: WeakSet[web.WebSocketResponse] = WeakSet()

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Player):
            return NotImplemented
        return type(self) is type(other) and self._token == other._token

    def __hash__(self) -> int:
        return hash(self._token)

    def __getstate__(self) -> dict[str, Any]:
        state = self.__dict__.copy()
        del state["websockets"]
        return state

    def __setstate__(self, state: dict[str, Any]) -> None:
        self.__dict__.update(state)
        self.websockets = WeakSet()

    def reset(self) -> None:
        self.hand.clear()
        self.points.clear()


T_Player = TypeVar("T_Player", bound=Player)


class BaseGame(Generic[T_Player]):
    GAMES: ClassVar[dict[str, type[BaseGame[Any]]]] = {}
    WAITING_GAMES_IDS: ClassVar[dict[type[BaseGame[Any]], str]] = {}

    player_class: type[T_Player]
    version: int
    game_name: str
    card_family: CardFamily
    number_of_players: int
    hand_size: int

    def __init__(self, game_id: str) -> None:
        self.websockets: WeakSet[web.WebSocketResponse] = WeakSet()
        self._game_id = game_id
        self._recv_lock = asyncio.Lock()
        self._send_lock = asyncio.Lock()
        self._players: list[T_Player] = []
        self._deck: list[Card]
        self._starting_player_id = random.randrange(self.number_of_players)
        self._current_player_id: int
        self._game_status = GameStatus.NOT_STARTED

    def __init_subclass__(
        cls,
        *,
        version: int,
        game_name: str | None = None,
        card_family: CardFamily,
        number_of_players: int,
        hand_size: int,
        **kwargs: Any,
    ) -> None:
        super().__init_subclass__(**kwargs)
        cls.player_class = get_args(
            next(
                x
                for x in types.get_original_bases(cls)
                if issubclass(get_origin(x), BaseGame)
            )
        )[0]
        cls.version = version
        cls.game_name = game_name or cls.__name__
        cls.card_family = card_family
        cls.number_of_players = number_of_players
        cls.hand_size = hand_size
        cls.GAMES[cls.__name__.lower()] = cls

    def __getstate__(self) -> dict[str, Any]:
        state = self.__dict__.copy()
        del state["websockets"]
        del state["_recv_lock"]
        del state["_send_lock"]
        return state

    def __setstate__(self, state: dict[str, Any]) -> None:
        self.websockets = WeakSet()
        self._recv_lock = asyncio.Lock()
        self._send_lock = asyncio.Lock()
        self.__dict__.update(state)

    def _get_deck(self) -> list[Card]:
        if self.card_family is CardFamily.ITALIANE:
            cards = Card.get_italian_deck()
        elif self.card_family is CardFamily.FRANCESI:
            # french cards: two decks
            cards = Card.get_french_deck(2, has_joker=True)
        return cards

    def _shuffle_deck(self) -> list[Card]:
        cards = self._get_deck()
        random.shuffle(cards)
        return cards

    def add_player(self, session_id: str) -> T_Player | None:
        player = self.player_class(session_id)
        try:
            idx = self._players.index(player)
        except ValueError:
            if len(self._players) >= self.number_of_players:
                return None
            self._players.append(player)
            if (
                len(self._players) >= self.number_of_players
                and self.WAITING_GAMES_IDS.get(type(self)) == self._game_id
            ):
                del self.WAITING_GAMES_IDS[type(self)]
        else:
            player = self._players[idx]

        return player

    @property
    def current_player(self) -> T_Player:
        return self._players[self._current_player_id]

    def _board_state(self, ws_player: T_Player | None) -> Iterator[list[Sendable]]:
        raise NotImplementedError

    def _results(self) -> Iterator[list[Sendable]]:
        raise NotImplementedError

    async def _send_str(self, ws: web.WebSocketResponse, msg: str) -> None:
        try:
            await ws.send_str(msg)
        except OSError as e:
            print(e)

    @overload
    async def _send(
        self,
        maybe_player_or_ws: T_Player | web.WebSocketResponse,
        *args: Sendable,
    ) -> None: ...

    @overload
    async def _send(
        self,
        maybe_player_or_ws: Sendable,
        *args: Sendable,
        websockets: Iterable[web.WebSocketResponse] | None = ...,
    ) -> None: ...

    async def _send(
        self,
        maybe_player_or_ws: T_Player | web.WebSocketResponse | Sendable,
        *args: Sendable,
        websockets: Iterable[web.WebSocketResponse] | None = None,
    ) -> None:
        if isinstance(maybe_player_or_ws, Player):
            await self._send(*args, websockets=maybe_player_or_ws.websockets)
            return
        if isinstance(maybe_player_or_ws, web.WebSocketResponse):
            await self._send(*args, websockets=[maybe_player_or_ws])
            return

        args = (maybe_player_or_ws, *args)
        async with self._send_lock, asyncio.TaskGroup() as tg:
            msg = "|".join(str(x).replace("|", "") for x in args)
            if websockets is None:
                websockets = self.websockets
            for ws in websockets:
                if not ws.closed:
                    tg.create_task(self._send_str(ws, msg))

    async def _send_others(
        self, player_or_ws: T_Player | web.WebSocketResponse, *args: Sendable
    ) -> None:
        websockets = (
            player_or_ws.websockets
            if isinstance(player_or_ws, Player)
            else {player_or_ws}
        )
        await self._send(*args, websockets=self.websockets - websockets)

    async def _send_current_state(
        self, ws: web.WebSocketResponse, player: T_Player | None = None
    ) -> None:
        await self._send(ws, "game_id", self._game_id)

        await self._send(ws, "players", *(x.name for x in self._players))
        if player and self._game_status is not GameStatus.NOT_STARTED:
            await self._send(ws, "player_id", self._players.index(player))

        if self._game_status is not GameStatus.NOT_STARTED:
            await self._send(ws, "animations", "off")
            await self._send(ws, "begin")
            for args in self._board_state(player):
                await self._send(ws, *args)
            if self._game_status is GameStatus.ENDED:
                for args in self._results():
                    await self._send(ws, *args)
                if player and player.ready:
                    await self._send(ws, "rematch_active")
            await self._send(ws, "animations", "on")

    async def _prepare_start(self) -> None:
        self._deck = self._shuffle_deck()
        self._current_player_id = self._starting_player_id
        self._game_status = GameStatus.STARTED

        await self._send("players", *(x.name for x in self._players))
        for player_id, player in enumerate(self._players):
            await self._send(player, "player_id", player_id)

        await self._send("begin")
        await self._start_game()
        await self._send(self.current_player, "turn")

    async def _start_game(self) -> None:
        raise NotImplementedError

    async def _end_game(self) -> None:
        self._game_status = GameStatus.ENDED
        for args in self._results():
            await self._send(*args)
        for player in self._players:
            player.ready = False

    async def _draw_card(self, player: T_Player) -> None:
        card = self._deck.pop()
        player.hand.append(card)
        player_id = self._players.index(player)
        async with asyncio.TaskGroup() as tg:
            if card.back is CardBack.NONE:
                tg.create_task(self._send(player, "draw_card", player_id, card))
                tg.create_task(self._send_others(player, "draw_card", player_id))
            else:
                if self._deck:
                    back = self._deck[-1].back
                    tg.create_task(
                        self._send(player, "draw_card", player_id, card, back)
                    )
                    tg.create_task(
                        self._send_others(
                            player, "draw_card", player_id, card.back, back
                        )
                    )
                else:
                    tg.create_task(self._send(player, "draw_card", player_id, card))
                    tg.create_task(
                        self._send_others(player, "draw_card", player_id, card.back)
                    )

    def _next_player(self) -> None:
        self._current_player_id = (self._current_player_id + 1) % self.number_of_players

    async def handle_raw_cmd(
        self, ws: web.WebSocketResponse, player: T_Player | None, msg: aiohttp.WSMessage
    ) -> None:
        if msg.type != aiohttp.WSMsgType.TEXT:
            err = (
                f"Incorrect message type: "
                f"expected {aiohttp.WSMsgType.TEXT.name}, received {msg.type.name}"
            )
            raise CmdError(err)

        cmd, *args = msg.data.split("|")
        await self.handle_cmd(ws, player, cmd, *args)

    async def handle_cmd(
        self,
        ws: web.WebSocketResponse,
        player: T_Player | None,
        raw_cmd: str,
        *raw_args_tuple: str,
    ) -> None:
        try:
            cmd = getattr(self, f"cmd_{raw_cmd}")
            if not isinstance(cmd, Command):
                raise TypeError  # noqa: TRY301
        except (AttributeError, TypeError) as e:
            err = f"Invalid command {raw_cmd}"
            raise CmdError(err) from e

        # cmd.check can raise a CmdError
        cmd.check(self, ws)

        raw_args = iter(raw_args_tuple)
        params = get_type_hints(cmd.func)
        args: list[Any] = []
        for name, type_ in params.items():
            if type_ is web.WebSocketResponse:
                args.append(ws)
            elif type_ in (T_Player, self.player_class):  # type: ignore[misc]
                if player is None:
                    err = "You're not a player"
                    raise CmdError(err)
                args.append(player)
            elif type_ in (T_Player | None, self.player_class | None):  # type: ignore[misc]
                args.append(player)
            elif type_ is Card:
                card = next(raw_args)
                try:
                    data = card.split(":")
                    if len(data) == 2:
                        suit, number = data
                        args.append(Card(Suit(suit), CardNumber(number)))
                    else:
                        back, suit, number = data
                        args.append(
                            Card(Suit(suit), CardNumber(number), CardBack(back))
                        )
                except ValueError as e:
                    err = f"Invalid card: {card}"
                    raise CmdError(err) from e
            elif name != "return":
                args.append(next(raw_args))

        cmd_expected_params = len(params) - 1
        cmd_given_params = len(args)
        if cmd_expected_params != cmd_given_params:
            err = (
                f"Invalid number of parameters for command {raw_cmd}: "
                f"{cmd_expected_params} expected, {cmd_given_params} given"
            )
            raise CmdError(err)

        try:
            async with self._recv_lock:
                await cmd.func(self, *args)
        except CmdError as e:
            raise CmdError(str(e), raw_cmd) from e

    @cmd()
    async def cmd_current_state(
        self, ws: web.WebSocketResponse, player: T_Player | None
    ) -> None:
        await self._send_current_state(ws, player)

    @cmd()
    async def cmd_join(
        self, ws: web.WebSocketResponse, player: T_Player | None, name: str
    ) -> None:
        if player is None:
            await self._send_current_state(ws)
            return

        player.name = name
        player.websockets.add(ws)
        await self._send_current_state(ws, player)

        if (
            len(self._players) == self.number_of_players
            and self._game_status is GameStatus.NOT_STARTED
            and all(x.name for x in self._players)
        ):
            random.shuffle(self._players)
            await self._prepare_start()

    @cmd()
    async def cmd_name(self, player: T_Player, name: str) -> None:
        player.name = name
        await self._send("players", *(x.name for x in self._players))

    @cmd(game_status=GameStatus.ENDED)
    async def cmd_rematch(self, player: T_Player) -> None:
        player.ready = True
        if all(x.ready for x in self._players):
            self._starting_player_id = (
                self._starting_player_id + 1
            ) % self.number_of_players
            for player in self._players:
                player.reset()
            await self._prepare_start()
