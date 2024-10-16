import { Card, FrenchBaseGame } from "./base.js";

class ScalaReale extends FrenchBaseGame {
  get playerIdentifiers() {
    return ["opponent", "self"];
  }

  get handSize() {
    return 14;
  }

  get cardFieldGenerators() {
    return [
      ["hand", ["player"], this.handSize],
      ["playing-area", [], 2],
    ];
  }

  get deckGenerators() {
    return [["deck", []]];
  }

  getPlayerIdentifier(playerId) {
    return Number.parseInt(playerId) === this.playerSide ? "self" : "opponent";
  }

  onGameAreaClick(event) {
    if (!("playing" in this.gameArea.dataset && event.target.matches(".card"))) {
      return;
    }

    const cmd = this.gameArea.dataset.playingStatus;
    const camelCmd = this.snakeToCamel(cmd, true);
    const func = this[`onGameAreaClickStatus${camelCmd}`];

    if (!func) {
      console.warn(`Unhandled click status onGameAreaClickStatus${camelCmd}`);
      return;
    }

    func.bind(this)(event);
  }

  onGameAreaClickStatusDraw(event) {
    if (
      !event.target.matches(
        ".card:is([data-position='deck'], [data-position='playing-area'])",
      )
    ) {
      return;
    }

    delete this.gameArea.dataset.playing;

    const cardPosition = event.target.dataset.position;
    if (cardPosition === "deck") {
      this.send("draw_card");
    } else {
      const card = event.target;
      if (
        Number.parseInt(card.dataset.fieldPosition) + 1 <
        Number.parseInt(card.dataset.fieldSize)
      ) {
        return;
      }
      this.send("draw_discarded");
    }
  }

  onGameAreaClickStatusHand(event) {
    if (
      !event.target.matches(
        ".card[data-position='hand'][data-player='self']:not([data-undiscardable])",
      )
    ) {
      return;
    }

    delete this.gameArea.dataset.playing;
    this.send("play", Card.fromObj(event.target).toString()); // TODO: convertire tutti gli invii in una cosa del genere
  }

  async cmdBegin() {
    await super.cmdBegin();

    this.decks.get("deck").instantiate(new Map([["deck", "br"]]));
  }

  async cmdInitDeck(count, back, redraw = false) {
    const deck = this.decks.get("deck");

    deck.setBack(back);
    deck.setCount(count);

    // if it's a redraw, remove the bottom card on the table
    if (redraw) {
      const table = this.cardFields.get("playing-area");
      if (table.count === 2) {
        const [underCard] = table.getCards(new Map(), 1);
        underCard.remove();
        table.refreshField();
      }
    }

    await this.awaitCardTransitions();
  }

  cmdTurnStatus(status) {
    this.gameArea.dataset.playingStatus = status;
  }

  async cmdDrawToTable(cardStr, deckBack = null) {
    const card = Card.fromString(cardStr);
    const deck = this.decks.get("deck");

    const table = this.cardFields.get("playing-area");
    const func = deck.moveTo(table, card.setParamsMap());
    if (deckBack !== null) {
      deck.setBack(deckBack);
    }

    await this.awaitCardTransitions(func);
  }

  async cmdDrawDiscarded(playerId, tableCardStr) {
    const player = this.getPlayerIdentifier(playerId);
    const tableCard = Card.fromString(tableCardStr);

    const params = new Map();
    if (!this.isPlayerSelf(playerId)) {
      params.set("suit", null);
      params.set("number", null);
    }

    const table = this.cardFields.get("playing-area");
    const func = table.moveTo(
      tableCard.setParamsMap(),
      this.cardFields.get("hand").select("player", player),
      params,
    );

    await this.awaitCardTransitions(func);
  }

  cmdDiscardPrevention(cardStr) {
    const card = Card.fromString(cardStr);
    const cardField = this.cardFields.get("hand").select("player", "self");

    const [cardObj] = cardField.getCards(card.setParamsMap(), 1);
    this.toggleCardParam(cardObj, "undiscardable");
  }

  async cmdPlayCard(...args) {
    // cleanup: free the spot for the new card, if necessary
    const table = this.cardFields.get("playing-area");
    if (table.count === 2) {
      const [underCard] = table.getCards(new Map(), 1);
      underCard.remove();
    }

    await super.cmdPlayCard(...args);

    // cleanup: remove the "undiscardable" tag, if present
    const cardField = this.cardFields.get("hand").select("player", "self");
    const cardObjs = cardField.getCards(new Map([["undiscardable", ""]]));
    for (const cardObj of cardObjs) {
      this.toggleCardParam(cardObj, "undiscardable");
    }
  }

  cmdResultsPrepare() {
    const table = document.getElementById("results-table");

    for (const [playerId, playerName] of this.players.entries()) {
      const row = table.insertRow();
      if (this.isPlayerSelf(playerId)) {
        row.classList.add("self");
      }
      const playerCell = row.insertCell();
      playerCell.textContent = playerName;

      const pointsCell = row.insertCell();
      pointsCell.classList.add("points");

      // details cell, with 5 divs inside for carte/denari/primiera/settebello/scope
      const detailsCell = row.insertCell();
      detailsCell.classList.add("details");
      for (let i = 0; i < 5; i++) {
        const div = document.createElement("div");
        detailsCell.append(div);
      }
    }
  }
}

window.g = new ScalaReale();
