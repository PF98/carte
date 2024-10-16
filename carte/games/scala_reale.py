from __future__ import annotations

from collections.abc import Iterator
from enum import StrEnum, auto
from typing import Any

from carte.exc import CmdError
from carte.games.base import BaseGame, Player, cmd
from carte.types import Card, CardFamily, CardNumber, GameStatus


class ScalaRealePlayingStatus(StrEnum):
    DRAW = auto()
    HAND = auto()
    WIN = auto()


class ScalaReale(
    BaseGame[Player],
    version=1,
    card_family=CardFamily.FRANCESI,
    number_of_players=2,
    hand_size=13,
):
    def __init__(self, game_id: str) -> None:
        super().__init__(game_id)

        self._table: list[Card] = []
        self._table_drawn_card: Card | None = None

    async def _prepare_start(self) -> None:
        self._table = []
        self._playing_status = ScalaRealePlayingStatus.DRAW
        self._table_drawn_card = None

        await super()._prepare_start()

        await self._send(self.current_player, "turn_status", self._playing_status)

    def _board_state(self, ws_player: Player | None) -> Iterator[list[Any]]:
        for player_id, player in enumerate(self._players):
            for card in player.hand:
                if player == ws_player:
                    yield ["draw_card", player_id, card]
                else:
                    yield ["draw_card", player_id, card.back]

        for card in self._table:
            yield ["draw_to_table", card]

        if self._deck:
            yield ["init_deck", len(self._deck), self._deck[-1].back]
        else:
            yield ["init_deck", 0]

        yield ["turn_status", self._playing_status]

        if self._game_status is GameStatus.ENDED:
            yield from self._show_winner_cards()
        else:
            if ws_player and self._players.index(ws_player) == self._current_player_id:
                if self._playing_status is ScalaRealePlayingStatus.HAND:
                    if self._table_drawn_card is not None:
                        yield ["discard_prevention", self._table_drawn_card]
                yield ["turn"]

    async def _start_game(self) -> None:
        await self._send("init_deck", len(self._deck), self._deck[-1].back)

        for _ in range(self.hand_size):
            for i in range(self.number_of_players):
                player_id = (self._current_player_id + i) % self.number_of_players
                await self._draw_card(self._players[player_id])

        await self._draw_to_table()

    def _results(self) -> Iterator[list[Any]]:
        results = [
            1 if player is self.current_player else 0 for player in self._players
        ]
        yield ["results", *results]

    def _append_to_table(self, card: Card) -> None:
        self._table.append(card)
        if len(self._table) > 2:
            self._table.pop(0)

    async def _reset_deck(self) -> None:
        self._table[:] = self._table[-1:]
        excluded = set()
        excluded.add(self._table[-1])
        for player in self._players:
            excluded.update(player.hand)
        self._deck = [card for card in self._shuffle_deck() if card not in excluded]
        await self._send("init_deck", len(self._deck), self._deck[-1].back, True)

    async def _draw_to_table(self) -> None:
        card = self._deck.pop()
        self._append_to_table(card)
        await self._send("draw_to_table", card, self._deck[-1].back)

    @cmd(
        current_player=True,
        game_status=GameStatus.STARTED,
        playing_status=ScalaRealePlayingStatus.DRAW,
    )
    async def cmd_draw_card(self) -> None:
        await self._draw_card(self.current_player)

        self._playing_status = ScalaRealePlayingStatus.HAND

        await self._send("turn_status", self._playing_status)
        await self._send(self.current_player, "turn")

    @cmd(
        current_player=True,
        game_status=GameStatus.STARTED,
        playing_status=ScalaRealePlayingStatus.DRAW,
    )
    async def cmd_draw_discarded(self) -> None:
        card = self._table.pop()
        self.current_player.hand.append(card)
        self._table_drawn_card = card

        if self._table:
            await self._send("draw_discarded", self._current_player_id, card)
        else:
            await self._send("draw_discarded", self._current_player_id, card)

        self._playing_status = ScalaRealePlayingStatus.HAND
        await self._send("turn_status", self._playing_status)

        await self._send(self.current_player, "discard_prevention", card)

        await self._send(self.current_player, "turn")

    @cmd(
        current_player=True,
        game_status=GameStatus.STARTED,
        playing_status=ScalaRealePlayingStatus.HAND,
    )
    async def cmd_play(self, card: Card) -> None:
        if self._table_drawn_card == card:
            msg = "You can't play that card"
            raise CmdError(msg)

        try:
            self.current_player.hand.remove(card)
        except ValueError as e:
            msg = "You don't have that card"
            raise CmdError(msg) from e

        self._append_to_table(card)

        self._table_drawn_card = None

        if self._check_win():
            self._playing_status = ScalaRealePlayingStatus.WIN
            await self._send("turn_status", self._playing_status)

            for args in self._show_winner_cards():
                await self._send(*args)

            await self._send("play_card", self._current_player_id, card)

            await self._end_game()
            return

        await self._send("play_card", self._current_player_id, card)

        self._playing_status = ScalaRealePlayingStatus.DRAW
        self._next_player()

        if not self._deck:
            await self._reset_deck()

        await self._send("turn_status", self._playing_status)
        await self._send(self.current_player, "turn")

    def _show_winner_cards(self) -> Iterator[list[Any]]:
        hand = self.current_player.hand
        jokers = [card for card in hand if card.number is CardNumber.JOKER]
        card_numbers = [card.number for card in hand]

        cards = []

        for number in CardNumber.get_french():
            try:
                i = card_numbers.index(number)
                cards.append(hand[i])
            except ValueError:
                cards.append(jokers.pop(0))

        yield ["show_winner_cards", self._current_player_id, *cards]

    def _check_win(self) -> bool:
        hand = [
            card
            for card in self.current_player.hand
            if card.number is not CardNumber.JOKER
        ]

        suits = {card.suit for card in hand}
        if len(suits) > 1:
            return False

        return len(hand) == len({card.number for card in hand})
