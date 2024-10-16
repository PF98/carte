import { Card, ItalianBaseGame } from "./base.js";

class Briscola extends ItalianBaseGame {
  get playerIdentifiers() {
    return ["opponent", "self"];
  }

  get handSize() {
    return 3;
  }

  get cardFieldGenerators() {
    return [
      ["hand", ["player"], this.handSize],
      ["briscola", [], 1],
      ["playing-area", [], 2],
    ];
  }

  get deckGenerators() {
    return [
      ["deck", []],
      ["points", ["player"]],
    ];
  }

  getPlayerIdentifier(playerId) {
    return Number.parseInt(playerId) === this.playerSide ? "self" : "opponent";
  }

  onGameAreaClick(event) {
    if (
      "playing" in this.gameArea.dataset &&
      event.target.matches(".card[data-position='hand'][data-player='self']")
    ) {
      delete this.gameArea.dataset.playing;
      this.send("play", `${event.target.dataset.suit}:${event.target.dataset.number}`);
    }
  }

  async cmdBegin() {
    await super.cmdBegin();

    this.decks.get("deck").instantiate(
      new Map([
        ["deck", "br"],
        ["deckCount", 40],
      ]),
    );
    for (const player of ["self", "opponent"]) {
      this.decks
        .get("points")
        .select("player", player)
        .instantiate(
          new Map([
            ["deck", "tr"],
            ["deckCount", 0],
          ]),
        );
    }

    await this.awaitCardTransitions();
  }

  async cmdShowBriscola(cardStr) {
    const card = Card.fromString(cardStr);

    const deck = this.decks.get("deck");

    const func = deck.moveTo(this.cardFields.get("briscola"), card.setParamsMap());
    await this.awaitCardTransitions(func);
  }

  async cmdDrawBriscola(playerId) {
    const player = this.getPlayerIdentifier(playerId);

    const params = new Map();
    if (!this.isPlayerSelf(playerId)) {
      params.set("suit", null);
      params.set("number", null);
    }

    const cardField = this.cardFields.get("briscola");
    const func = cardField.moveTo(
      new Map(),
      this.cardFields.get("hand").select("player", player),
      params,
    );
    await this.awaitCardTransitions(func);
  }

  cmdPoints(playerId, amount) {
    const player = this.getPlayerIdentifier(playerId);

    this.decks.get("points").select("player", player).setCount(amount);
  }

  async cmdTake(playerId) {
    await this.sleep(2 * this.transitionDuration);

    const player = this.getPlayerIdentifier(playerId);

    const cardField = this.cardFields.get("playing-area");
    const func = cardField.moveTo(
      new Map(),
      this.decks.get("points").select("player", player),
      new Map(),
    );

    await this.awaitCardTransitions(func);
  }
}

new Briscola();
