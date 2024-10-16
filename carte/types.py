from __future__ import annotations

import itertools
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum, StrEnum, auto
from typing import TYPE_CHECKING, Any, Generic, Literal, TypeVar, cast

from aiohttp import web

from carte.exc import CmdError

if TYPE_CHECKING:
    from carte.games.base import BaseGame, Player


class GameStatus(Enum):
    NOT_STARTED = auto()
    STARTED = auto()
    ENDED = auto()


class CardFamily(StrEnum):
    ITALIANE = auto()
    FRANCESI = auto()


class Suit(StrEnum):
    BASTONI = auto()
    COPPE = auto()
    DENARI = auto()
    SPADE = auto()

    CUORI = auto()
    QUADRI = auto()
    FIORI = auto()
    PICCHE = auto()

    ROSSO = auto()
    NERO = auto()

    @classmethod
    def get_italian(cls) -> list[Suit]:
        return [cls.BASTONI, cls.COPPE, cls.DENARI, cls.SPADE]

    @classmethod
    def get_french(cls) -> list[Suit]:
        return [cls.CUORI, cls.QUADRI, cls.FIORI, cls.PICCHE]

    @classmethod
    def get_french_joker(cls) -> list[Suit]:
        return [cls.ROSSO, cls.NERO]


class CardNumber(StrEnum):
    ASSO = "1"
    DUE = "2"
    TRE = "3"
    QUATTRO = "4"
    CINQUE = "5"
    SEI = "6"
    SETTE = "7"
    OTTO = "8"
    NOVE = "9"
    DIECI = "10"

    FANTE = auto()
    CAVALLO = auto()
    RE = auto()

    JACK = auto()
    DONNA = auto()
    # RE = auto()

    JOKER = auto()

    @classmethod
    def get_numbers(cls, n: int) -> list[CardNumber]:
        return list(CardNumber)[:n]  # type: ignore[return-value]

    @classmethod
    def get_italian(cls) -> list[CardNumber]:
        return [*cls.get_numbers(7), cls.FANTE, cls.CAVALLO, cls.RE]

    @classmethod
    def get_french(cls) -> list[CardNumber]:
        return [*cls.get_numbers(10), cls.JACK, cls.DONNA, cls.RE]


class CardBack(StrEnum):
    BLU = auto()
    ROSSO = auto()

    NONE = auto()


@dataclass(frozen=True)
class Card:
    suit: Suit
    number: CardNumber
    back: CardBack = CardBack.NONE

    def __str__(self) -> str:
        return f"{self.suit}:{self.number}" + (
            f":{self.back}" if self.back is not CardBack.NONE else ""
        )

    @classmethod
    def get_italian_deck(cls) -> list[Card]:
        numbers = CardNumber.get_italian()
        suits = Suit.get_italian()

        return [
            Card(suit, number) for suit, number in itertools.product(suits, numbers)
        ]

    @classmethod
    def get_french_deck(
        cls, count: Literal[1, 2] = 1, has_joker: bool = False
    ) -> list[Card]:
        numbers = CardNumber.get_french()
        suits = Suit.get_french()

        cards: list[Card] = []
        backs = cast(list[CardBack], list(CardBack))[:count]
        for back in backs:
            cards.extend(
                Card(suit, number, back)
                for suit, number in itertools.product(suits, numbers)
            )

            if has_joker:
                cards.extend(
                    Card(s, CardNumber.JOKER, back) for s in Suit.get_french_joker()
                )

        return cards


CmdFunc = TypeVar("CmdFunc", bound=Callable[..., Awaitable[None]])


@dataclass
class Command(Generic[CmdFunc]):  # type: ignore[misc]
    func: CmdFunc
    current_player: bool
    other_arguments: dict[str, Enum]

    def check(self, game: BaseGame[Any], ws: web.WebSocketResponse) -> None:
        for name, value in self.other_arguments.items():
            attr = getattr(game, f"_{name}")
            if attr is not value:
                err = f"Invalid {name.replace('_', ' ')}"
                raise CmdError(err)

        if self.current_player and ws not in game.current_player.websockets:
            err = "It's not your turn"
            raise CmdError(err)


@dataclass
class SavedGame:
    game: BaseGame[Player]
    version: int
    last_saved: datetime = field(default_factory=lambda: datetime.now(UTC), init=False)

    @property
    def is_valid(self) -> bool:
        if self.last_saved + timedelta(days=7) < datetime.now(UTC):
            return False
        if self.version != type(self.game).version:
            return False
        return True


Sendable = str | int | Card
