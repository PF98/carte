from __future__ import annotations

from collections.abc import Iterator

from carte.exc import CmdError
from carte.games.base import BaseGame, Player, cmd
from carte.types import Card, CardFamily, CardNumber, GameStatus, Sendable


class Briscola(
    BaseGame[Player],
    version=1,
    card_family=CardFamily.ITALIANE,
    number_of_players=2,
    hand_size=3,
):
    def __init__(self, game_id: str) -> None:
        super().__init__(game_id)

        self._card_points = {
            CardNumber.DUE: 0,
            CardNumber.QUATTRO: 0,
            CardNumber.CINQUE: 0,
            CardNumber.SEI: 0,
            CardNumber.SETTE: 0,
            CardNumber.FANTE: 2,
            CardNumber.CAVALLO: 3,
            CardNumber.RE: 4,
            CardNumber.TRE: 10,
            CardNumber.ASSO: 11,
        }

        self._briscola: Card
        self._briscola_drawn = False
        self._played_cards: dict[Player, Card] = {}

    def _board_state(self, ws_player: Player | None) -> Iterator[list[Sendable]]:
        for player_id, player in enumerate(self._players):
            for card in player.hand:
                if player == ws_player:
                    yield ["draw_card", player_id, card]
                else:
                    yield ["draw_card", player_id]

        if not self._briscola_drawn:
            yield ["show_briscola", self._briscola]

        for player, card in self._played_cards.items():
            player_id = self._players.index(player)
            if player == ws_player:
                yield ["draw_card", player_id, card]
            else:
                yield ["draw_card", player_id]
            yield ["play_card", player_id, card]

        for player_id, player in enumerate(self._players):
            if player.points:
                yield ["points", player_id, len(player.points)]

        yield ["deck_count", "deck", len(self._deck)]

        if ws_player and self._players.index(ws_player) == self._current_player_id:
            yield ["turn"]

    def _results(self) -> Iterator[list[Sendable]]:
        results = []
        for results_player in self._players:
            points = 0
            for card in results_player.points:
                points += self._card_points[card.number]
            results.append(points)
        yield ["results", *results]

    async def _start_game(self) -> None:
        for _ in range(self.hand_size):
            for i in range(self.number_of_players):
                player_id = (self._current_player_id + i) % self.number_of_players
                await self._draw_card(self._players[player_id])

        await self._show_briscola()

    async def _show_briscola(self) -> None:
        self._briscola = self._deck.pop()
        self._briscola_drawn = False
        await self._send("show_briscola", self._briscola)

    @cmd(current_player=True, game_status=GameStatus.STARTED)
    async def cmd_play(self, card: Card) -> None:
        try:
            self.current_player.hand.remove(card)
        except ValueError as e:
            msg = "You don't have that card"
            raise CmdError(msg) from e

        self._played_cards[self.current_player] = card
        await self._send("play_card", self._current_player_id, card)

        if len(self._played_cards) == self.number_of_players:
            winning_card: Card | None = None
            winning_player: Player
            card_order = list(self._card_points)
            for player, card in self._played_cards.items():
                if (
                    winning_card is None
                    or (
                        card.suit == winning_card.suit
                        and card_order.index(card.number)
                        > card_order.index(winning_card.number)
                    )
                    or (
                        card.suit == self._briscola.suit
                        and winning_card.suit != self._briscola.suit
                    )
                ):
                    winning_player = player
                    winning_card = card

            winning_player.points.extend(self._played_cards.values())
            self._played_cards.clear()
            self._current_player_id = self._players.index(winning_player)
            await self._send("take", self._current_player_id)

            if self._deck:
                for i in range(self.number_of_players):
                    player = self._players[
                        (self._current_player_id + i) % self.number_of_players
                    ]
                    if self._deck:
                        await self._draw_card(player)
                    else:
                        self._briscola_drawn = True
                        player.hand.append(self._briscola)
                        await self._send("draw_briscola", self._players.index(player))

            elif all(not player.hand for player in self._players):
                await self._end_game()
                return

        else:
            self._next_player()

        await self._send(self.current_player, "turn")
