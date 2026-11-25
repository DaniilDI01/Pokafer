/* Pokafer web table — клиент без сборки (vanilla JS), мультибот-стол. */
"use strict";

window.addEventListener("error", (e) => {
  const s = document.getElementById("status");
  if (s) { s.textContent = "JS-ошибка: " + e.message; s.classList.add("bad"); }
});

const BOT_AVATARS = ["🤖", "👾", "🐱", "🦊", "🧠"];

/* Сессия именуется клиентом: обрыв связи или F5 возвращает сервер к тому же
   столу (а не к новому с одним ботом и стартовыми стеками). */
const SID_KEY = "pokafer.sid";
function sessionId() {
  const fromUrl = new URLSearchParams(location.search).get("sid");
  if (fromUrl) return fromUrl;
  try {
    return sessionStorage.getItem(SID_KEY)
      || (sessionStorage.setItem(SID_KEY, crypto.randomUUID()), sessionStorage.getItem(SID_KEY));
  } catch { return crypto.randomUUID(); }
}
const sid = sessionId();

const $ = (id) => document.getElementById(id);
const els = {
  status: $("status"), log: $("log"),
  table: document.querySelector(".table"), middle: document.querySelector(".middle"),
  board: $("board"), pot: $("pot"), street: $("street"),
  hAmount: $("hAmount"), callAmount: $("callAmount"), potAmount: $("potAmount"),
  callLabel: $("checkCallBtn").querySelector(".label"), hBet: $("hBet"), scoreline: $("scoreline"), hCards: $("hCards"),
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
let botActions = {};        // name -> последнее действие («колл 6», «чек», …)
let prevHandNo = null;      // сброс действий на новой руке/улице
let prevStreet = null;

/* ---------- отрисовка ---------- */

/* Суммы на столе — точно, как в румах: целая — без дробной части, иначе до
   двух знаков. Глаза читают то, что видно: округление теряло бы четверти
   блайнда в стеках, банке и ставках. */
function fmt(x) {
  return String(Math.round(x * 100) / 100);
}

/* Карта — спрайт из листа 13x4 «ранг x масть» (pokafer/web/static/cards):
   лица карт не рисуются шрифтом, рендер одинаков в headless и на экране. */
const RANKS = "23456789TJQKA";
const SUIT_ORDER = "shdc";

function cardEl(cardStr, small = false) {
  const div = document.createElement("div");
  if (!cardStr) {
    div.className = "card back";
    return div;
  }
  const col = RANKS.indexOf(cardStr[0]);
  const row = SUIT_ORDER.indexOf(cardStr[1]);
  if (col < 0 || row < 0) throw new Error(`неизвестная карта ${cardStr}`);
  const [w, h] = small ? [46, 66] : [58, 82];
  div.className = "card" + (small ? " small" : "");
  div.dataset.card = cardStr;
  div.style.backgroundPosition = `${-col * w}px ${-row * h}px`;
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

/* Борд — пять ПОСТОЯННЫХ слотов: карты не перецентрируются с каждой улицей
   (полу­пиксельные сдвиги ломают попиксельное распознавание глаз). */
const BOARD_SLOTS = 5;

function renderBoard(cards) {
  els.board.innerHTML = "";
  for (let i = 0; i < BOARD_SLOTS; i++) {
    const slot = document.createElement("div");
    slot.className = "slot";
    if (cards && cards[i]) slot.appendChild(cardEl(cards[i], true));
    els.board.appendChild(slot);
  }
}

/* Целые позиции: CSS-центрирование даёт полу­пиксели (чётность ширины
   меняется вместе со скроллбаром/окном), а глаза сравнивают пиксель-в-пиксель.
   Середина стола ставится скриптом в целое число px, все «центрированные»
   блоки отсчитываются от неё. */
function snapLayout() {
  els.table.style.setProperty("--cx", `${Math.round(els.middle.clientWidth / 2)}px`);
}
window.addEventListener("resize", snapLayout);

function addLog(text, cls = "sys", dist = null) {
  const div = document.createElement("div");
  div.className = "entry " + cls;
  div.innerHTML = text + (dist ? ` <span class="dist">[${dist}]</span>` : "");
  els.log.appendChild(div);           // записи сверху вниз, по ходу раздачи
  els.log.scrollTop = els.log.scrollHeight;
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
    const action = botActions[b.name] || (b.folded ? "фолд" : "");
    // строка действия резервируется всегда: табличка не меняет высоту
    seat.innerHTML = `
      <div class="head">
        <div class="avatar">${BOT_AVATARS[(i + 1) % BOT_AVATARS.length]}</div>
        <div class="plate">
          <div class="name">${b.name}${b.is_button ? ' <span class="button-mark">D</span>' : ""}</div>
          <div class="stack"><span class="coin">💰</span><span class="amt">${fmt(b.stack)}</span></div>
          ${b.strategy ? `<div class="strat">${b.strategy}</div>` : ""}
          <div class="last-action"${action ? "" : " hidden"}>${action}</div>
        </div>
      </div>
      <div class="cards"></div>
      <div class="bet-chip"></div>
    `;
    // бокс карт фиксирован (рассчитан на вскрытые карты): рубашки и открытые
    // карты стоят на одних местах при любом состоянии места
    const cards = seat.querySelector(".cards");
    if (reveal) renderCards(cards, b.cards, 0);
    else if (!b.folded) renderCards(cards, null, 2);  // сбросил — карты не показываем
    seat.querySelector(".bet-chip").textContent = b.bet > 0 ? fmt(b.bet) : "";
    els.villains.appendChild(seat);
  });
}

function setActions(view) {
  const legal = view.legal || {};
  const myTurn = view.to_act === 1 && !view.hand_over;
  const hasRaise = myTurn && legal.raise && legal.raise.min != null;

  // все кнопки всегда на месте: неактивные — серые и некликабельные
  els.nextFloat.disabled = !view.hand_over;
  els.foldBtn.disabled = !myTurn || !legal.fold;
  els.checkCallBtn.disabled = !myTurn;
  els.raiseBtn.disabled = !hasRaise;
  els.raiseSlider.disabled = !hasRaise;
  els.raiseInput.disabled = !hasRaise;
  document.querySelectorAll(".presets button").forEach(
    (b) => (b.disabled = !hasRaise)
  );
  if (myTurn) {
    if (legal.call !== null && legal.call !== undefined) {
      callAmountText = fmt(legal.call);   // «Колл» и сумма — отдельные поля:
      els.callAmount.textContent = callAmountText;  // число начинается в фиксированной точке
      els.callLabel.textContent = "Колл";
      els.checkCallBtn.dataset.act = "call";
    } else {
      callAmountText = "";
      els.callAmount.textContent = "";
      els.callLabel.textContent = "Чек";
      els.checkCallBtn.dataset.act = "check";
    }
  }
  if (hasRaise) {
    const r = legal.raise;
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

let pending = false;  // действие отправлено — кнопки закрыты до ответного вида
let callAmountText = "";  // сумма в кнопке «Колл N»; пусто — «Чек»

function lockActions() {
  [els.nextFloat, els.foldBtn, els.checkCallBtn, els.raiseBtn,
   els.raiseSlider, els.raiseInput].forEach((el) => { el.disabled = true; });
  document.querySelectorAll(".presets button").forEach((b) => { b.disabled = true; });
}

function sendAction(obj) {
  // сервер отвечает видом СРАЗУ после действия героя (не после «раздумий»
  // ботов), но двойной клик и клик по устаревшему виду не должны уходить
  // вторым действием
  if (pending || !send(obj)) return false;
  pending = true;
  lockActions();
  return true;
}

function renderView(v) {
  pending = false;  // любой вид — ответ сервера: кнопки снова в его ведении
  // новая рука или новая улица — сбрасываем подписи действий ботов
  if (v.hand_no !== prevHandNo) {
    botActions = {};
    prevHandNo = v.hand_no;
    prevStreet = v.street;
  } else if (v.street !== prevStreet) {
    botActions = {};
    prevStreet = v.street;
  }
  lastView = v;
  renderCards(els.hCards, v.hero.cards, 0);
  renderVillains(v);
  renderBoard(v.board);
  els.potAmount.textContent = fmt(v.pot);
  els.street.textContent = `${v.street} · рука #${v.hand_no}`;
  els.scoreline.textContent = v.stackline;
  els.hAmount.textContent = fmt(v.hero.stack);
  els.heroBtn.textContent = v.hero.is_button ? "D" : "";
  els.hBet.style.display = v.hero.bet > 0 ? "" : "none";
  els.hBet.textContent = v.hero.bet > 0 ? fmt(v.hero.bet) : "";
  els.seatHero.classList.toggle("active", v.to_act === 1 && !v.hand_over);
  els.seatHero.classList.toggle("folded", !!v.hero.folded);
  els.combo.textContent = heroComboText(v);
  if (!v.hand_over) els.resultStrip.classList.add("hidden");
  setActions(v);
  els.status.textContent = v.hand_over ? "рука завершена — жмите «Следующая рука»"
    : (v.to_act === 1 ? "ваш ход" : "боты думают…");
}

function showResult(r) {
  // компактная полоса вместо оверлея
  const win = r.winners.join(" и ");
  const main = r.kind === "showdown"
    ? `🏆 ${win} +${r.amount} (шоудаун)`
    : `🏆 ${win} забирает ${r.pot} (фолд)`;
  els.resultStrip.innerHTML = `<div class="main">${main}</div>`;
  els.resultStrip.classList.remove("hidden");
  addLog(`рука завершена: ${main}`, "sys");
  // разбор вскрытия (карты и комбинации всех участников) — в журнале: на
  // столе только итог, полоса итога не растёт и не зависит от числа игроков
  for (const [name, score] of Object.entries(r.scores || {})) {
    const cards = (r.hole_cards || {})[name];
    const w = (r.winnings || {})[name];
    addLog(`${name}${cards ? ` [${cards.join(" ")}]` : ""}: ${score}` +
      (w ? ` → +${Math.round(w * 100) / 100}` : ""), "sys");
  }
}

/* ---------- сеть ---------- */

function send(obj) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(obj));
    return true;
  }
  // обрыв связи: клик не должен пропадать молча
  els.status.textContent = "⚠ нет соединения — действие не отправлено, повторите через секунду";
  els.status.classList.add("bad");
  return false;
}

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws/table?sid=${encodeURIComponent(sid)}`);
  ws.onopen = () => { els.status.textContent = "подключено"; els.status.classList.remove("bad"); };
  ws.onerror = () => {
    els.status.textContent = "ошибка WebSocket (см. консоль)";
    els.status.classList.add("bad");
  };
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === "view") renderView(msg);
    else if (msg.type === "bot") {
      botActions[msg.name] = msg.ru;  // подпись под ботом: действие + сумма
      addLog(`${msg.name}: ${msg.ru}`, "bot", fmtDist(msg.dist));
    }
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

els.foldBtn.onclick = () => {
  if (sendAction({ type: "action", action: "fold" })) addLog("Вы: фолд", "hero");
};
els.checkCallBtn.onclick = () => {
  const act = els.checkCallBtn.dataset.act;
  if (sendAction({ type: "action", action: act }))
    addLog(act === "call" ? `Вы: колл ${callAmountText}` : "Вы: чек", "hero");
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
  if (sendAction({ type: "action", action: verb, amount: amt }))
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
  if (!lastView || !lastView.hand_over) return;
  if (sendAction({ type: "new_hand" })) addLog("— новая рука —", "sys");
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
snapLayout();
fetch("/v1/strategies")
  .then((r) => r.json())
  .then((list) => {
    strategiesList = list;
    renderBotChips();
  });
connect();
