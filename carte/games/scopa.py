from __future__ import annotations

from collections.abc import Iterator
from enum import StrEnum, auto

from carte.exc import CmdError
from carte.games.base import BaseGame, Player, cmd
from carte.types import Card, CardFamily, CardNumber, GameStatus, Sendable, Suit


class ScopaPlayingStatus(StrEnum):
    HAND = auto()
    CAPTURE = auto()


class ScopaPlayer(Player):
    def __init__(self, token: str, name: str = "") -> None:
        super().__init__(token, name)
        self.scopa_cards: list[Card] = []

    def reset(self) -> None:
        super().reset()
        self.scopa_cards.clear()


class Scopa(
    BaseGame[ScopaPlayer],
    version=1,
    card_family=CardFamily.ITALIANE,
    number_of_players=2,
    hand_size=6,
):
    def __init__(self, game_id: str) -> None:
        super().__init__(game_id)

        self._table_size = 4
        self._card_values = {
            CardNumber.ASSO: 1,
            CardNumber.DUE: 2,
            CardNumber.TRE: 3,
            CardNumber.QUATTRO: 4,
            CardNumber.CINQUE: 5,
            CardNumber.SEI: 6,
            CardNumber.SETTE: 7,
            CardNumber.FANTE: 8,
            CardNumber.CAVALLO: 9,
            CardNumber.RE: 10,
        }
        self._primiera_card_values = {
            CardNumber.SETTE: 21,
            CardNumber.SEI: 18,
            CardNumber.ASSO: 16,
            CardNumber.CINQUE: 15,
            CardNumber.QUATTRO: 14,
            CardNumber.TRE: 13,
            CardNumber.DUE: 12,
            CardNumber.RE: 10,
            CardNumber.CAVALLO: 10,
            CardNumber.FANTE: 10,
        }

        self._table: list[Card] = []
        self._playing_status: ScopaPlayingStatus

        self._last_taker_id: int

        self._active_card: Card
        self._takeable_cards: list[Card] = []
        self._selected_cards: list[Card] = []

    def _board_state(self, ws_player: ScopaPlayer | None) -> Iterator[list[Sendable]]:
        # draw cards
        for player_id, player in enumerate(self._players):
            for card in player.hand:
                if player == ws_player:
                    yield ["draw_card", player_id, card]
                else:
                    yield ["draw_card", player_id]

        # place cards on the table
        for card in self._table:
            yield ["add_to_table", card]

        # update points (i.e. number of cards taken) for both players and for the deck
        # -> capped at 6 to hide most of the information, without compromising the deck
        #    height visuals
        for player_id, player in enumerate(self._players):
            if player.points:
                yield [
                    "points",
                    player_id,
                    min(len(player.points) - len(player.scopa_cards), 6),
                ]

            if player.scopa_cards:
                yield ["points_scopa", player_id, *player.scopa_cards]

        yield ["deck_count", "deck", len(self._deck)]

        yield ["turn_status", self._playing_status]
        if self._playing_status is ScopaPlayingStatus.CAPTURE:
            yield ["activate_card", self._current_player_id, self._active_card]
            if self.current_player == ws_player:
                yield ["capture_takeable_cards", *self._takeable_cards]

            yield ["capture_selected_cards", *self._selected_cards]

        # set the correct turn
        if ws_player and self._players.index(ws_player) == self._current_player_id:
            yield ["turn"]

    def _results(self) -> Iterator[list[Sendable]]:
        yield ["results_prepare"]

        cards_winner, cards_detail = self._results_cards()
        denari_winner, denari_detail = self._results_denari()
        primiera_winner, primiera_detail = self._results_primiera()
        settebello_winner, settebello_detail = self._results_settebello()

        yield from (cards_detail, denari_detail, primiera_detail, settebello_detail)

        scopa_points = tuple(len(player.scopa_cards) for player in self._players)
        yield ["results_detail", "scopa", *scopa_points]
        results = [
            sum(t)
            for t in zip(
                scopa_points,
                cards_winner,
                denari_winner,
                primiera_winner,
                settebello_winner,
                strict=True,
            )
        ]

        yield ["results", *results]

    async def _prepare_start(self) -> None:
        self._table = []
        self._playing_status = ScopaPlayingStatus.HAND

        self._last_taker_id = 0

        await super()._prepare_start()

        await self._send(self.current_player, "turn_status", self._playing_status)

    async def _start_game(self) -> None:
        await self._draw_full_hands()

        for _ in range(self._table_size):
            card = self._deck.pop()
            self._table.append(card)

            await self._send("add_to_table", card)

    async def _draw_full_hands(self) -> None:
        for _ in range(self.hand_size):
            for i in range(self.number_of_players):
                player_id = (self._current_player_id + i) % self.number_of_players
                await self._draw_card(self._players[player_id])

    @cmd(
        current_player=True,
        game_status=GameStatus.STARTED,
        playing_status=ScopaPlayingStatus.HAND,
    )
    async def cmd_play(self, card: Card) -> None:
        # card not in hand
        if card not in self.current_player.hand:
            msg = "You don't have that card"
            raise CmdError(msg)

        # check if the card should be played or if it should just be activated
        takeable_cards = self._check_playing_card(card)

        # no cards can be taken: play it into the field
        if not takeable_cards:
            await self._send("play_card", self._current_player_id, card)
            self.current_player.hand.remove(card)
            self._table.append(card)

            finished = await self._finish_turn()
            if finished:
                return
        # some cards can be taken: change to the capture status and offer to take them
        else:
            self._active_card = card
            self._takeable_cards = takeable_cards
            self._selected_cards = []
            await self._send("activate_card", self._current_player_id, card)

            await self._send(
                self.current_player, "capture_takeable_cards", *self._takeable_cards
            )

            self._playing_status = ScopaPlayingStatus.CAPTURE

        await self._send("turn_status", self._playing_status)

        await self._send(self.current_player, "turn")

    @cmd(
        current_player=True,
        game_status=GameStatus.STARTED,
        playing_status=ScopaPlayingStatus.CAPTURE,
    )
    async def cmd_take_choice(self, card: Card) -> None:
        # card not among the takeable ones
        if card not in self._takeable_cards and card not in self._selected_cards:
            msg = "You can't swap that card"
            raise CmdError(msg)

        old_takeable_cards = set(self._takeable_cards)

        # swap the status
        if card in self._takeable_cards:
            self._takeable_cards.remove(card)
            self._selected_cards.append(card)
        else:  # card in selected_cards
            self._selected_cards.remove(card)
            self._takeable_cards.append(card)

        # check if the sum is complete
        selected_sum = sum(self._card_values[c.number] for c in self._selected_cards)

        # still send it, even if it is complete, to update the view
        # incomplete sum: recalculate the takeable cards, excuding the cards
        # that have already been taken
        new_takeable_cards = self._calculate_takeable(
            self._active_card, self._selected_cards
        )

        # changes in the takeable cards: members EITHER before OR after, not both
        delta_takeable_cards = list(old_takeable_cards ^ set(new_takeable_cards))

        self._takeable_cards = new_takeable_cards
        await self._send(
            self.current_player, "capture_takeable_cards", *delta_takeable_cards
        )
        await self._send("capture_selected_cards", card)

        if selected_sum == self._card_values[self._active_card.number]:
            # remove cards from hand/table and add them to the "points" array
            self.current_player.hand.remove(self._active_card)
            self.current_player.points.append(self._active_card)
            self._table = [c for c in self._table if c not in self._selected_cards]
            self.current_player.points.extend(self._selected_cards)

            is_scopa = not self._table
            # format: "take", player id, is scopa
            await self._send("take", self._current_player_id, int(is_scopa))

            self._last_taker_id = self._current_player_id

            # check scopa
            if is_scopa:
                self.current_player.scopa_cards.append(self._active_card)

            self._playing_status = ScopaPlayingStatus.HAND
            # self._active_card = None
            self._takeable_cards = []
            self._selected_cards = []
            await self._send("turn_status", self._playing_status)

            finished = await self._finish_turn()
            if finished:
                return

        # yield back the turn to the current player, whichever the result
        await self._send(self.current_player, "turn")

    async def _finish_turn(self) -> bool:
        self._next_player()

        # check if both players have no cards: the turn is finished
        if all(not player.hand for player in self._players):
            # the deck is still full: play the next turn
            if self._deck:
                await self._draw_full_hands()
            # the game is over
            else:
                # send the remaining cards to the last player that took the cards
                if self._table:
                    await self._send("take_all", self._last_taker_id)
                    self._players[self._last_taker_id].points.extend(self._table)
                    self._table.clear()

                await self._end_game()
                return True

        self._playing_status = ScopaPlayingStatus.HAND
        return False

    # check if the card that is played can be used to take or not
    # if it can take, then the return value is a list of the cards that may be taken
    def _check_playing_card(self, card: Card) -> list[Card]:
        # take with the exact same card
        equipollent_cards = [c for c in self._table if c.number == card.number]
        # at least one card: take it compulsorily
        if equipollent_cards:
            return equipollent_cards

        return self._calculate_takeable(card)

    def _calculate_takeable(
        self, card: Card, used: list[Card] | None = None
    ) -> list[Card]:
        if used is None:
            used = []

        card_value = self._card_values[card.number]
        card_value -= sum(self._card_values[c.number] for c in used)

        # only keep cards with smaller or equal value
        smaller_card_values = sorted(
            (
                v
                for v in (
                    self._card_values[c.number] for c in self._table if c not in used
                )
                if v <= card_value
            ),
            reverse=True,
        )
        # check which cards can combine to be captured by card_value
        valid_values = self._check_combinations(card_value, smaller_card_values)

        return [c for c in self._table if self._card_values[c.number] in valid_values]

    # return any card value that may be combined with other cards on the table
    # to be taken by the played card (card_value)
    def _check_combinations(
        self, card_value: int, values: list[int], starting_index: int = 0
    ) -> list[int]:
        out = set()
        for i, v in enumerate(values[starting_index:], start=starting_index):
            rem_value = card_value - v

            # the selected card (v) is too big for card_value
            if rem_value < 0:
                continue

            # "v" is exactly card_value: consider it takeable; skip the recursive step
            if rem_value == 0:
                out.add(v)
                continue

            # the selected card does not fit perfectly: recurse
            rec_out = self._check_combinations(rem_value, values, i + 1)
            if rec_out:
                out.add(v)
                out.update(rec_out)

        return sorted(out)

    # all of the _results_*() functions return a 2-element tuple.
    # the first element is a list containing the points that each "category" assigns to
    # each player.
    # the second element is a results_detail message that is then yielded by the results
    # iterator, detailing the information to be shown in the details section in the
    # results table.
    def _results_cards(self) -> tuple[list[int], list[Sendable]]:
        scores = [len(player.points) for player in self._players]

        details: list[Sendable] = ["results_detail", "cards", *scores]

        max_value = max(scores)
        results = [0 for _ in self._players]
        if max_value != 20:
            winner = scores.index(max_value)
            results[winner] += 1

        return results, details

    def _results_denari(self) -> tuple[list[int], list[Sendable]]:
        scores = [
            sum(1 for c in player.points if c.suit == Suit.DENARI)
            for player in self._players
        ]

        details: list[Sendable] = ["results_detail", "denari", *scores]

        max_value = max(scores)
        results = [0 for _ in self._players]
        if max_value != 5:
            winner = scores.index(max_value)
            results[winner] += 1

        return results, details

    def _results_primiera(self) -> tuple[list[int], list[Sendable]]:
        card_numbers: list[list[str]] = []
        scores = []

        for player in self._players:
            card_numbers.append([])
            scores.append(0)
            for suit in Suit.get_italian():
                suit_cards = [card for card in player.points if card.suit == suit]
                card = None
                card_score = 0
                if suit_cards:
                    card = max(
                        suit_cards,
                        key=lambda card: self._primiera_card_values[card.number],
                    )
                    card_score = self._primiera_card_values[card.number]

                scores[-1] += card_score
                card_numbers[-1].append(str(card.number) if card is not None else "0")

        primiera_cards = []
        for i, suit in enumerate(Suit.get_italian()):
            primiera_cards.append(str(suit))
            primiera_cards.extend(cn[i] for cn in card_numbers)

        details: list[Sendable] = [
            "results_detail",
            "primiera",
            *scores,
            *primiera_cards,
        ]

        sorted_scores = sorted(scores, reverse=True)
        max_value = sorted_scores[0]

        results = [0 for _ in self._players]
        if max_value != sorted_scores[1]:
            winner = scores.index(max_value)
            results[winner] += 1

        return results, details

    def _results_settebello(self) -> tuple[list[int], list[Sendable]]:
        out = [
            1 if Card(suit=Suit.DENARI, number=CardNumber.SETTE) in player.points else 0
            for player in self._players
        ]
        details: list[Sendable] = ["results_detail", "settebello", *out]

        return out, details
