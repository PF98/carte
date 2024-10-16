import { Card, ItalianBaseGame } from "./base.js";

class Scopa extends ItalianBaseGame {
  get playerIdentifiers() {
    return ["opponent", "self"];
  }

  get handSize() {
    return 6;
  }

  get cardFieldGenerators() {
    return [
      ["hand", ["player"], this.handSize],
      ["playing-area", [], 13],
      ["points-scopa", ["player"], 18],
    ];
  }

  get deckGenerators() {
    return [
      ["deck", []],
      ["points", ["player"], 6],
    ];
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

  onGameAreaClickStatusHand(event) {
    if (!event.target.matches(".card[data-position='hand'][data-player='self']")) {
      return;
    }

    delete this.gameArea.dataset.playing;
    this.send("play", `${event.target.dataset.suit}:${event.target.dataset.number}`);
  }

  onGameAreaClickStatusCapture(event) {
    if (!event.target.matches(".card[data-position='playing-area']")) {
      return;
    }

    const cardObj = event.target;
    if (["takeable", "selected"].every((e) => !(e in cardObj.dataset))) {
      return;
    }

    delete this.gameArea.dataset.playing;
    this.send("take_choice", `${cardObj.dataset.suit}:${cardObj.dataset.number}`);
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

  async cmdAddToTable(cardStr) {
    const card = Card.fromString(cardStr);

    const deck = this.decks.get("deck");
    const func = deck.moveTo(this.cardFields.get("playing-area"), card.setParamsMap());
    await this.awaitCardTransitions(func);
  }

  cmdTurnStatus(status) {
    this.gameArea.dataset.playingStatus = status;
  }

  cmdActivateCard(playerId, activeCardStr) {
    const activeCard = Card.fromString(activeCardStr);
    const player = this.getPlayerIdentifier(playerId);

    const params = new Map();
    if (this.isPlayerSelf(playerId)) {
      activeCard.setParamsMap(params);
    }

    const cardField = this.cardFields.get("hand").select("player", player);
    const [cardObj] = cardField.getCards(params, 1);

    const activeParams = new Map([["active", ""]]);
    this.addCardParams(cardObj, activeCard.setParamsMap(activeParams));

    // no animation: to increase the fluidity, the card to be taken may be
    // clicked just after activating this one
  }

  cmdCaptureTakeableCards(...cards) {
    const cardField = this.cardFields.get("playing-area");
    for (const cardStr of cards) {
      const card = Card.fromString(cardStr);
      const [cardObj] = cardField.getCards(card.setParamsMap(), 1);

      this.toggleCardParam(cardObj, "takeable");
    }

    // no animation: same reason as cmdActivateCard(...)
  }

  cmdCaptureSelectedCards(...cards) {
    const cardField = this.cardFields.get("playing-area");
    for (const cardStr of cards) {
      const card = Card.fromString(cardStr);
      const [cardObj] = cardField.getCards(card.setParamsMap(), 1);

      this.toggleCardParam(cardObj, "selected");
    }

    // no animation: same reason as cmdActivateCard(...)
  }

  cmdPoints(playerId, amount) {
    const player = this.getPlayerIdentifier(playerId);

    this.decks.get("points").select("player", player).setCount(amount);
  }

  async cmdTake(playerId, isScopa) {
    await this.sleep(2 * this.transitionDuration);

    const player = this.getPlayerIdentifier(playerId);

    // take all the selected cards from the table and place them in the points deck
    const tableCardField = this.cardFields.get("playing-area");
    const tableFunc = tableCardField.moveTo(
      new Map([["selected", ""]]),
      this.decks.get("points").select("player", player),
      new Map(),
    );

    // move the card in hand to either the scopa count or the points deck
    const handCardField = this.cardFields.get("hand").select("player", player);
    const dest = (
      Number.parseInt(isScopa) > 0
        ? this.cardFields.get("points-scopa")
        : this.decks.get("points")
    ).select("player", player);

    const handFunc = handCardField.moveTo(
      new Map([["active", ""]]),
      dest,
      new Map(),
      1,
    );

    await this.awaitCardTransitions(() => {
      tableFunc();
      handFunc();
    });
  }

  async cmdPointsScopa(playerId, ...cards) {
    const player = this.getPlayerIdentifier(playerId);
    const deck = this.decks.get("deck");
    const cardField = this.cardFields.get("points-scopa").select("player", player);

    const funcs = [];
    for (const cardStr of cards) {
      const card = Card.fromString(cardStr);
      const func = deck.moveTo(cardField, card.setParamsMap());
      funcs.push(func);
    }
    await this.awaitCardTransitions(() => {
      for (const func of funcs) {
        if (func !== undefined) {
          func();
        }
      }
    });
  }

  async cmdTakeAll(playerId) {
    await this.sleep(2 * this.transitionDuration);

    const player = this.getPlayerIdentifier(playerId);

    const tableCardField = this.cardFields.get("playing-area");
    const tableFunc = tableCardField.moveTo(
      new Map(),
      this.decks.get("points").select("player", player),
      new Map(),
    );

    await this.awaitCardTransitions(tableFunc);
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

  cmdResultsDetail(type, ...args) {
    const resultsCells = ["cards", "denari", "primiera", "settebello", "scopa"];
    const resultsTitles = new Map([
      ["cards", "Carte"],
      ["denari", "Denari"],
      ["primiera", "Primiera"],
      ["settebello", "Settebello"],
      ["scopa", "Scope"],
    ]);

    const detId = resultsCells.indexOf(type);

    const table = document.getElementById("results-table");
    const rows = table.querySelectorAll("tr");
    for (const playerId of this.players.keys()) {
      const detailDiv = rows[playerId].querySelector(`div:nth-child(${detId + 1})`);
      detailDiv.dataset.detailType = type;
      detailDiv.append(`${resultsTitles.get(type)}: `);

      const value = Number.parseInt(args[playerId]);
      if (type === "primiera") {
        const cardValues = args
          .slice(2)
          .filter((_v, i) => (i - playerId - 1) % 3 === 0)
          .map((v) => {
            if (["fante", "cavallo", "re"].includes(v)) {
              return v[0].toUpperCase();
            }
            const n = Number.parseInt(v);
            if (n === 1) {
              return "A";
            }
            return n === 0 ? "-" : v;
          });
        const span = document.createElement("span");
        span.textContent = cardValues.join("");
        detailDiv.append(`${value} (`, span, ")");
      } else if (type === "settebello") {
        detailDiv.append(value > 0 ? "✓" : "✕");
      } else {
        detailDiv.append(value);
      }

      if (type !== "scopa") {
        if (value > Number.parseInt(args[1 - playerId])) {
          detailDiv.classList.add("winner");
        }
      } else if (value > 0) {
        detailDiv.classList.add("winner");
      }
    }
  }

  cmdResults(...results) {
    const table = document.getElementById("results-table");
    const sortedResults = Array.from(
      results.map((n) => Number.parseInt(n)).entries(),
    ).sort(([, a], [, b]) => b - a);

    const rows = Array.from(table.querySelectorAll("tr"));

    for (const [playerId, points] of sortedResults) {
      // the row gets appended to the end to guarantee the correct order, so the
      // first row should be updated every time
      const row = rows[playerId];
      const cell = row.querySelector("td.points");
      cell.textContent = points;

      table.appendChild(row);
    }
    document.getElementById("results").showPopover();
  }
}

new Scopa();
