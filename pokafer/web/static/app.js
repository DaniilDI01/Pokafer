/* Pokafer web table — клиент без сборки (vanilla JS), мультибот-стол. */
"use strict";

window.addEventListener("error", (e) => {
  const s = document.getElementById("status");
  if (s) { s.textContent = "JS-ошибка: " + e.message; s.classList.add("bad"); }
});

const SUITS = { s: "♠", h: "♥", d: "♦", c: "♣" };
const RED = new Set(["h", "d"]);
const BOT_AVATARS = ["🤖", "👾", "🐱", "🦊", "🧠"];

const $ = (id) => document.getElementById(id);
const els = {
  status: $("status"), log: $("log"),
  board: $("board"), pot: $("pot"), street: $("street"),
  hStack: $("hStack"), hBet: $("hBet"), hCards: $("hCards"),
  seatHero: $("seatHero"), combo: $("combo"), heroBtn: $("heroBtn"),
  villains: $("villains"),
  actionbar: $("actionbar"),
  foldBtn: $("foldBtn"), checkCallBtn: $("checkCallBtn"),
  raiseBtn: $("raiseBtn"), raiseSlider: $("raiseSlider"), raiseInput: $("raiseInput"),
  hintBtn: $("hintBtn"),
  nextFloat: $("nextHandFloat"),
  resultStrip: $("resultStrip"),
  botChips: $("botChips"), addBotBtn: $("addBotBtn"),
};

let ws = null;
let lastView = null;
let botsCfg = [{ name: "Мозг", strategy: "heuristic" }];
let strategiesList = {};

/* ---------- отрисовка ---------- */

function cardEl(cardStr, small = false) {
  const div = document.createElement("div");
  if (!cardStr) {
    div.className = "card back" + (small ? " small" : "");
    return div;
  }
  const suit = SUITS[cardStr[1]] || cardStr[1];
  div.className = "card" + (small ? " small" : "") + (RED.has(cardStr[1]) ? " red" : "");
  div.innerHTML = `<span class="r">${cardStr[0]}</span><span class="s">${suit}</span>`;
  return div;
}

function renderCards(container, cards, faceDownCount, small = false) {
  container.innerHTML = "";
  if (faceDownCount) {
    for (let i = 0; i < faceDownCount; i++) container.appendChild(cardEl(null, small));
    return;
  }
  (cards || []).forEach((c) => container.appendChild(cardEl(c, small)));
}

function addLog(text, cls = "sys", dist = null) {
  const div = document.createElement("div");
  div.className = "entry " + cls;
  div.innerHTML = text + (dist ? ` <span class="dist">[${dist}]</span>` : "");
  els.log.prepend(div);
}

function fmtDist(dist, top = 3) {
  const ru = (k) => {
    const [verb, amt] = k.split(":");
    const m = { fold: "фолд", check: "чек", call: `колл(${amt || ""})`,
                bet: `ставка ${amt || ""}`, raise: `рейз до ${amt || ""}` };
    return m[verb] || k;
  };
  return Object.entries(dist || {})
    .sort((a, b) => b[1] - a[1]).slice(0, top)
    .map(([k, v]) => `${ru(k)} ${Math.round(v * 100)}%`).join(" · ");
}

/* позиции ботов вокруг стола по количеству */
const LAYOUTS = {
  1: ["pos-top"],
  2: ["pos-tl", "pos-tr"],
  3: ["pos-left", "pos-top", "pos-right"],
  4: ["pos-left", "pos-tl", "pos-tr", "pos-right"],
  5: ["pos-left", "pos-tl", "pos-top", "pos-tr", "pos-right"],
};

function renderVillains(v) {
  els.villains.innerHTML = "";
  const layout = LAYOUTS[Math.min(v.bots.length, 5)] || LAYOUTS[1];
  v.bots.forEach((b, i) => {
    const seat = document.createElement("div");
    seat.className = "seat villain " + (layout[i] || "pos-top");
    seat.dataset.seat = b.seat;
    if (v.to_act === b.seat && !v.hand_over) seat.classList.add("active");
    if (b.folded) seat.classList.add("folded");
    const reveal = Array.isArray(b.cards);
    const cardsHtml = b.folded && !reveal ? "" : "";
    seat.innerHTML = `
      <div class="avatar">${BOT_AVATARS[(i + 1) % BOT_AVATARS.length]}</div>
      <div class="plate">
        <div class="name">${b.name}${b.is_button ? ' <span class="button-mark">D</span>' : ""}</div>
        <div class="stack">${Math.round(b.stack)}</div>
        ${b.strategy ? `<div class="strat">${b.strategy}</div>` : ""}
      </div>
      <div class="bet-chip">${b.bet > 0 ? Math.round(b.bet) : ""}</div>
    `;
    const cards = document.createElement("div");
    cards.className = "cards";
    if (b.folded && !reveal) {
      // сбросил — карты не показываем
    } else if (reveal) {
      renderCards(cards, b.cards, 0);
    } else {
      renderCards(cards, null, 2);
    }
    seat.appendChild(cards);
    els.villains.appendChild(seat);
  });
}

function setActions(view) {
  const legal = view.legal || {};
  const myTurn = view.to_act === 1 && !view.hand_over;
  els.actionbar.classList.toggle("hidden", !myTurn);
  els.nextFloat.classList.toggle("hidden", !view.hand_over);
  if (!myTurn) return;

  els.foldBtn.disabled = !legal.fold;
  els.foldBtn.style.display = legal.fold ? "" : "none";

  if (legal.call !== null && legal.call !== undefined) {
    els.checkCallBtn.textContent = `Колл ${legal.call}`;
    els.checkCallBtn.dataset.act = "call";
  } else {
    els.checkCallBtn.textContent = "Чек";
    els.checkCallBtn.dataset.act = "check";
  }

  const r = legal.raise;
  const hasRaise = r && r.min != null;
  els.raiseBtn.style.display = hasRaise ? "" : "none";
  els.raiseSlider.style.display = hasRaise ? "" : "none";
  els.raiseInput.style.display = hasRaise ? "" : "none";
  document.querySelector(".presets").style.display = hasRaise ? "" : "none";
  if (hasRaise) {
    els.raiseSlider.min = r.min; els.raiseSlider.max = r.max;
    els.raiseSlider.value = r.min;
    els.raiseInput.min = r.min; els.raiseInput.max = r.max;
    els.raiseInput.value = r.min;
    const facing = legal.call !== null && legal.call !== undefined;
    els.raiseBtn.textContent = facing ? "Рейз" : "Ставка";
    els.raiseBtn.dataset.min = r.min; els.raiseBtn.dataset.max = r.max;
  }
}

function heroComboText(v) {
  if (!v.hero.cards || !v.board.length) return "";
  const val = (c) => ({ T: 10, J: 11, Q: 12, K: 13, A: 14 }[c[0]] || +c[0]);
  // лёгкая подсказка: пара/оверпара — без полного оценщика
  const ranks = [...v.hero.cards, ...v.board].map((c) => c[0]);
  const counts = {};
  ranks.forEach((r) => (counts[r] = (counts[r] || 0) + 1));
  const pair = Object.entries(counts).find(([r, n]) => n >= 2);
  return pair ? `у вас: ${pair[1] >= 3 ? "тройка/каре" : "пара"} ${pair[0]}` : "";
}

function renderView(v) {
  lastView = v;
  renderCards(els.hCards, v.hero.cards, 0);
  renderVillains(v);
  renderCards(els.board, v.board, 0, true);
  els.pot.textContent = Math.round(v.pot);
  els.street.textContent = `${v.street} · рука #${v.hand_no} · ${v.stackline}`;
  els.hStack.textContent = Math.round(v.hero.stack);
  els.heroBtn.textContent = v.hero.is_button ? "D" : "";
  els.hBet.style.display = v.hero.bet > 0 ? "" : "none";
  els.hBet.textContent = v.hero.bet > 0 ? Math.round(v.hero.bet) : "";
  els.seatHero.classList.toggle("active", v.to_act === 1 && !v.hand_over);
  els.combo.textContent = heroComboText(v);
  if (!v.hand_over) els.resultStrip.classList.add("hidden");
  setActions(v);
  els.status.textContent = v.hand_over ? "рука завершена — следующая сдаётся автоматически"
    : (v.to_act === 1 ? "ваш ход" : "боты думают…");
}

function showResult(r) {
  // компактная полоса вместо оверлея
  const win = r.winners.join(" и ");
  const main = r.kind === "showdown"
    ? `🏆 ${win} +${r.amount} (шоудаун)`
    : `🏆 ${win} забирает ${r.pot} (фолд)`;
  const parts = [];
  for (const [name, score] of Object.entries(r.scores || {})) {
    const cards = (r.hole_cards || {})[name];
    const w = (r.winnings || {})[name];
    parts.push(`${name}${cards ? ` [${cards.join(" ")}]` : ""}: ${score}${w ? ` → +${Math.round(w)}` : ""}`);
  }
  els.resultStrip.innerHTML = `<div class="main">${main}</div>` +
    (parts.length ? `<div class="detail">${parts.join(" · ")}</div>` : "");
  els.resultStrip.classList.remove("hidden");
  addLog(`рука завершена: ${main}`, "sys");
}

/* ---------- сеть ---------- */

function send(obj) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
}

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws/table`);
  ws.onopen = () => { els.status.textContent = "подключено"; els.status.classList.remove("bad"); };
  ws.onerror = () => {
    els.status.textContent = "ошибка WebSocket (см. консоль)";
    els.status.classList.add("bad");
  };
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === "view") renderView(msg);
    else if (msg.type === "bot") addLog(`${msg.name}: ${msg.ru}`, "bot", fmtDist(msg.dist));
    else if (msg.type === "result") showResult(msg);
    else if (msg.type === "hint") addLog(`💡 ${msg.ru}`, "hero", fmtDist(msg.dist));
    else if (msg.type === "error") { addLog(`⚠ ${msg.detail}`, "sys"); els.status.textContent = "ошибка ввода"; }
    else if (msg.type === "log") addLog(msg.text, "sys");
  };
  ws.onclose = () => {
    els.status.textContent = "соединение потеряно, переподключение…";
    els.status.classList.add("bad");
    setTimeout(connect, 1500);
  };
}

/* ---------- действия героя ---------- */

els.foldBtn.onclick = () => { send({ type: "action", action: "fold" }); addLog("Вы: фолд", "hero"); };
els.checkCallBtn.onclick = () => {
  const act = els.checkCallBtn.dataset.act;
  send({ type: "action", action: act });
  addLog(act === "call" ? `Вы: ${els.checkCallBtn.textContent.toLowerCase()}` : "Вы: чек", "hero");
};

function currentRaise() {
  const v = Number(els.raiseInput.value);
  if (!Number.isFinite(v)) return null;
  const min = Number(els.raiseBtn.dataset.min), max = Number(els.raiseBtn.dataset.max);
  return Math.min(max, Math.max(min, Math.round(v * 100) / 100));
}

els.raiseBtn.onclick = () => {
  const amt = currentRaise();
  if (amt === null || !lastView) return;
  const facing = lastView.legal && lastView.legal.call !== null && lastView.legal.call !== undefined;
  const verb = facing ? "raise" : "bet";
  send({ type: "action", action: verb, amount: amt });
  addLog(`Вы: ${facing ? "рейз до" : "ставка"} ${amt}`, "hero");
};

els.raiseSlider.oninput = () => { els.raiseInput.value = els.raiseSlider.value; };
els.raiseInput.oninput = () => { els.raiseSlider.value = els.raiseInput.value; };

document.querySelectorAll(".presets button").forEach((b) => {
  b.onclick = () => {
    const v = lastView;
    if (!v || !v.legal.raise) return;
    const { min, max } = v.legal.raise;
    let amt = min;
    if (b.dataset.preset === "half") amt = Math.max(min, Math.round(v.pot * 0.5));
    if (b.dataset.preset === "pot") amt = Math.max(min, Math.round(v.pot));
    if (b.dataset.preset === "max") amt = max;
    amt = Math.min(max, amt);
    els.raiseSlider.value = amt;
    els.raiseInput.value = amt;
  };
});

els.hintBtn.onclick = () => send({ type: "hint" });
els.nextFloat.onclick = () => {
  send({ type: "new_hand" });
  addLog("— новая рука —", "sys");
};

/* ---------- настройка ботов ---------- */

function renderBotChips() {
  els.botChips.innerHTML = "";
  botsCfg.forEach((b, i) => {
    const chip = document.createElement("div");
    chip.className = "bot-chip";
    const sel = document.createElement("select");
    Object.keys(strategiesList).forEach((name) => {
      const opt = document.createElement("option");
      opt.value = name; opt.textContent = name;
      sel.appendChild(opt);
    });
    sel.value = b.strategy;
    sel.onchange = () => { botsCfg[i].strategy = sel.value; sendConfigure(); };
    const rm = document.createElement("button");
    rm.className = "chip-x"; rm.textContent = "×"; rm.title = "убрать бота";
    rm.onclick = () => { if (botsCfg.length > 1) { botsCfg.splice(i, 1); sendConfigure(); } };
    chip.append(sel, rm);
    els.botChips.appendChild(chip);
  });
  els.addBotBtn.disabled = botsCfg.length >= 5;
}

function sendConfigure() {
  send({ type: "configure", bots: botsCfg.map((b, i) => ({
    name: `Мозг ${i + 1}`, strategy: b.strategy,
  })) });
  renderBotChips();
}

els.addBotBtn.onclick = () => {
  if (botsCfg.length >= 5) return;
  botsCfg.push({ name: `Мозг ${botsCfg.length + 1}`, strategy: "heuristic" });
  sendConfigure();
};

/* старт */
fetch("/v1/strategies")
  .then((r) => r.json())
  .then((list) => {
    strategiesList = list;
    renderBotChips();
  });
connect();
