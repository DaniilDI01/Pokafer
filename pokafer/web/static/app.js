/* Pokafer web table — клиент без сборки (vanilla JS). */
"use strict";

/* Любая ошибка скрипта становится видимой в статус-строке — не глотаем молча. */
window.addEventListener("error", (e) => {
  const s = document.getElementById("status");
  if (s) {
    s.textContent = "JS-ошибка: " + e.message;
    s.classList.add("bad");
  }
});

const SUITS = { s: "♠", h: "♥", d: "♦", c: "♣" };
const RED = new Set(["h", "d"]);

const $ = (id) => document.getElementById(id);
const els = {
  status: $("status"), log: $("log"),
  board: $("board"), pot: $("pot"), street: $("street"),
  hStack: $("hStack"), vStack: $("vStack"), hBet: $("hBet"), vBet: $("vBet"),
  hCards: $("hCards"), vCards: $("vCards"),
  seatHero: $("seatHero"), seatVillain: $("seatVillain"), combo: $("combo"),
  actionbar: $("actionbar"), newhandBar: $("newhandBar"),
  foldBtn: $("foldBtn"), checkCallBtn: $("checkCallBtn"),
  raiseBtn: $("raiseBtn"), raiseSlider: $("raiseSlider"), raiseInput: $("raiseInput"),
  hintBtn: $("hintBtn"), strategy: $("strategy"),
  newHandBtn: $("newHandBtn"),
  overlay: $("overlay"), banner: $("banner"),
};

let ws = null;
let lastView = null;

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

function setActions(view) {
  const legal = view.legal || {};
  const myTurn = view.to_act === "hero" && !view.hand_over;
  els.actionbar.classList.toggle("hidden", !myTurn);
  els.newhandBar.classList.toggle("hidden", !view.hand_over);

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
    els.raiseBtn.textContent = r.min > 0 && view.pot > 0 && r.min <= view.pot ? "Ставка" : "Рейз";
    // «ставка», если до нас на этой улице не ставили (min <= половины банка условно неточно,
    // поэтому определяем по контексту ниже)
    els.raiseBtn.dataset.min = r.min; els.raiseBtn.dataset.max = r.max;
  }
}

function renderView(v) {
  lastView = v;
  renderCards(els.hCards, v.hero.cards, 0);
  renderCards(els.vCards, v.villain.cards, v.villain.folded ? 0 : (v.villain.cards ? 0 : 2));
  if (v.villain.folded) els.vCards.innerHTML = "";

  renderCards(els.board, v.board, 0, true);
  els.pot.textContent = Math.round(v.pot);
  els.street.textContent = `${v.street} · рука #${v.hand_no} · счёт ${v.stackline}`;

  els.hStack.textContent = Math.round(v.hero.stack);
  els.vStack.textContent = Math.round(v.villain.stack);
  els.hBet.style.display = v.hero.bet > 0 ? "" : "none";
  els.hBet.textContent = v.hero.bet > 0 ? Math.round(v.hero.bet) : "";
  els.vBet.style.display = v.villain.bet > 0 ? "" : "none";
  els.vBet.textContent = v.villain.bet > 0 ? Math.round(v.villain.bet) : "";

  els.seatHero.classList.toggle("active", v.to_act === "hero");
  els.seatVillain.classList.toggle("active", v.to_act === "villain");
  els.seatVillain.style.opacity = v.villain.folded ? 0.45 : 1;

  setActions(v);
  els.status.textContent = v.hand_over ? "рука завершена" :
    (v.to_act === "hero" ? "ваш ход" : "мозг думает…");
}

function showResult(r) {
  const title = document.createElement("div");
  const detail = document.createElement("div");
  const win = r.winners.join(" и ");
  title.className = "title";
  title.innerHTML = r.kind === "showdown"
    ? `Шоудаун · <span class="win">${win}</span> +${r.amount}`
    : `<span class="win">${win}</span> забирает ${r.pot} (оппонент сбросил)`;
  const lines = [];
  for (const [name, score] of Object.entries(r.scores || {})) {
    const cards = (r.hole_cards || {})[name];
    const cardsTxt = cards ? ` [${cards.join(" ")}]` : "";
    const mark = r.winners.includes(name) ? " ←" : "";
    lines.push(`${name}${cardsTxt}: ${score}${mark}`);
  }
  detail.className = "detail";
  detail.innerHTML = lines.join("<br>");

  const nextBtn = document.createElement("button");
  nextBtn.className = "primary big";
  nextBtn.textContent = "▶ Следующая рука";
  nextBtn.style.marginTop = "16px";
  nextBtn.onclick = () => {
    els.overlay.classList.add("hidden");
    send({ type: "new_hand" });
    addLog("— новая рука —", "sys");
  };

  els.banner.innerHTML = "";
  els.banner.append(title, detail, nextBtn);
  els.overlay.classList.remove("hidden");
  addLog(`рука завершена: ${win} +${r.amount}`, "sys");
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
    else if (msg.type === "bot") addLog(`🤖 ${msg.ru}`, "bot", fmtDist(msg.dist));
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

/* ---------- обработчики ---------- */

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
  if (amt === null) return;
  const isBet = lastView && lastView.hero.bet === 0 && !facingBet(lastView);
  const verb = isBet ? "bet" : "raise";
  send({ type: "action", action: verb, amount: amt });
  addLog(`Вы: ${isBet ? "ставка" : "рейз до"} ${amt}`, "hero");
};

function facingBet(v) {
  return v.legal && v.legal.call !== null && v.legal.call !== undefined && v.legal.call > 0;
}

els.raiseSlider.oninput = () => { els.raiseInput.value = els.raiseSlider.value; };
els.raiseInput.oninput = () => { els.raiseSlider.value = els.raiseInput.value; };

document.querySelectorAll(".presets button").forEach((b) => {
  b.onclick = () => {
    const v = lastView;
    if (!v || !v.legal.raise) return;
    const { min, max } = v.legal.raise;
    const pot = v.pot;
    let amt = min;
    if (b.dataset.preset === "half") amt = Math.max(min, Math.round(pot * 0.5));
    if (b.dataset.preset === "pot") amt = Math.max(min, Math.round(pot));
    if (b.dataset.preset === "max") amt = max;
    amt = Math.min(max, amt);
    els.raiseSlider.value = amt;
    els.raiseInput.value = amt;
  };
});

els.hintBtn.onclick = () => send({ type: "hint" });
els.newHandBtn.onclick = () => {
  els.overlay.classList.add("hidden");
  send({ type: "new_hand" });
  addLog("— новая рука —", "sys");
};

els.strategy.onchange = () => send({ type: "strategy", name: els.strategy.value });

/* старт */
fetch("/v1/strategies")
  .then((r) => r.json())
  .then((list) => {
    Object.keys(list).forEach((name) => {
      const opt = document.createElement("option");
      opt.value = name; opt.textContent = `${name} — ${list[name]}`;
      els.strategy.appendChild(opt);
    });
    els.strategy.value = "heuristic";
  });
connect();
