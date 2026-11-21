// Jahresringe — Client: Host steuert und spielt Audio, Spieler tippen ihre Platzierung.
const $ = (sel) => document.querySelector(sel);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
};

let ws = null;
let me = { role: null, id: null, name: null, code: null };
let state = null;
let audio = null;
let playTimer = null;

// ---------------------------------------------------------------- Verbindung

function wsUrl() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${location.host}/ws`;
}

function connect() {
  ws = new WebSocket(wsUrl());
  ws.onopen = () => {
    const saved = JSON.parse(localStorage.getItem("jahresringe") || "null");
    if (saved && saved.code && saved.id) {
      me = { ...saved };
      send({ action: "join", code: saved.code, player_id: saved.id, name: saved.name });
    }
  };
  ws.onmessage = (ev) => handle(JSON.parse(ev.data));
  ws.onclose = () => {
    setStatus("Verbindung weg — neuer Versuch …");
    setTimeout(connect, 1500);
  };
}

function send(obj) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
}

function handle(msg) {
  if (msg.type === "state") {
    state = msg;
    me.code = msg.code;
    if (me.role === "host") renderHost();
    else renderPlayer();
  } else if (msg.type === "joined") {
    me.role = msg.role;
    me.id = msg.player_id;
    me.code = msg.code;
    if (msg.name) me.name = msg.name;
    localStorage.setItem("jahresringe", JSON.stringify(me));
    showOnly(msg.role === "host" ? "#view-host" : "#view-player");
    $("#btn-leave").classList.remove("hidden");
  } else if (msg.type === "error") {
    setStatus(msg.message, true);
    showError(msg.message);
  } else if (msg.type === "warning") {
    setStatus(msg.message, true);
  }
}

// ---------------------------------------------------------------- Start

function showOnly(sel) {
  for (const v of document.querySelectorAll(".view")) v.classList.add("hidden");
  $(sel).classList.remove("hidden");
}

function showError(text) {
  const box = $("#start-error");
  if (box && !$("#view-start").classList.contains("hidden")) {
    box.textContent = text || "";
  }
}

function setStatus(text, isError = false) {
  const box = $("#player-status");
  if (box) {
    box.textContent = text || "";
    box.classList.toggle("error", isError);
  }
  if (isError) console.warn(text);
}

$("#btn-create").onclick = () => {
  showError("");
  me.role = "host";
  send({ action: "create" });
};

$("#btn-join").onclick = () => {
  const code = $("#join-code").value.trim().toUpperCase();
  const name = $("#join-name").value.trim();
  if (!code || !name) return showError("Code und Name eintragen");
  showError("");
  me.role = "player";
  me.name = name;
  send({ action: "join", code, name });
};

$("#btn-leave").onclick = () => {
  localStorage.removeItem("jahresringe");
  location.reload();
};

for (const [id, action] of [["#btn-start", "start"], ["#btn-next", "next"], ["#btn-reveal", "reveal"],
                            ["#btn-skip", "skip"], ["#btn-reset", "reset"]]) {
  $(id).onclick = () => send({ action });
}

// ---------------------------------------------------------------- Host

function renderHost() {
  $("#host-code").textContent = state.code;
  $("#host-url").textContent = `${location.origin}/?code=${state.code}`;

  const st = state.state;
  const playing = st === "playing";
  $("#btn-start").classList.toggle("hidden", st !== "lobby");
  $("#btn-next").classList.toggle("hidden", !(st === "ready" || st === "revealed"));
  $("#btn-next").textContent = st === "ready" ? "Erste Karte abspielen" : "Nächste Karte";
  $("#btn-reveal").classList.toggle("hidden", !playing);
  $("#btn-skip").classList.toggle("hidden", !playing);
  $("#host-audio-box").classList.toggle("hidden", !(playing && state.current));

  renderPlayers($("#host-players"));
  renderResults($("#host-results"));

  const cur = state.current;
  if (cur && cur.revealed) {
    $("#host-reveal").classList.remove("hidden");
    $("#host-reveal").innerHTML =
      `<div class="year">${cur.year}</div><div class="title">${esc(cur.title)}</div>` +
      `<div class="artist">${esc(cur.artist)}</div>`;
  } else if (!cur) {
    $("#host-reveal").classList.add("hidden");
  } else {
    $("#host-reveal").classList.add("hidden");
  }

  const log = $("#host-log");
  log.innerHTML = "";
  for (const entry of state.log) {
    const li = el("li");
    li.appendChild(el("span", "lyear", entry.skipped ? "—" : entry.year));
    li.appendChild(el("span", "ltitle", `${entry.title} — ${entry.artist}`));
    if (!entry.skipped) li.appendChild(el("span", "lwho", entry.correct.join(", ") || "niemand"));
    log.appendChild(li);
  }

  if (state.winner) setStatus(`Gewonnen: ${state.winner}`);
  if (cur && cur.preview && cur.preview !== audio?.dataset.src) startAudio(cur.preview);
}

function startAudio(url) {
  if (!audio) {
    audio = $("#audio");
    audio.onended = () => clearInterval(playTimer);
    audio.ontimeupdate = () => {
      const pct = audio.duration ? (audio.currentTime / audio.duration) * 100 : 0;
      $("#audio-progress").style.width = `${pct}%`;
      $("#audio-clock").textContent = `${audio.currentTime.toFixed(1).replace(".", ",")} s`;
    };
  }
  audio.dataset.src = url;
  audio.src = url;
  audio.currentTime = 0;
  $("#audio-hint").textContent = "";
  audio.play().then(() => {
    $("#audio-hint").textContent = "30 Sekunden Ausschnitt — Play nochmal für den Anfang.";
  }).catch(() => {
    $("#audio-hint").textContent = "Autoplay blockiert: auf Play drücken.";
  });
}

$("#btn-play").onclick = () => {
  if (!audio || !audio.src) return;
  audio.currentTime = 0;
  audio.play().catch(() => {});
};

// ---------------------------------------------------------------- Spieler

function renderPlayer() {
  const mine = state.me;
  if (!mine) return;
  $("#player-name").textContent = mine.name;
  $("#player-score").textContent = `${mine.score} Karte${mine.score === 1 ? "" : "n"}`;

  if (state.state === "lobby") setStatus("Warte auf den Host …");
  else if (state.state === "ready") setStatus("Gleich geht's los — der Host spielt die erste Karte.");
  else if (state.state === "playing") {
    setStatus(mine.pending === null ? "Hör zu und tippe, wohin die Karte gehört." :
      "Getippt — kannst noch ändern, bis der Host auflöst.");
  } else if (state.state === "revealed") {
    setStatus(mine.correct_last === true ? "Richtig!" : "Leider falsch.", mine.correct_last === false);
  } else if (state.state === "finished") {
    setStatus(state.winner === mine.name ? "Du hast gewonnen!" : `Gewonnen: ${state.winner}`);
  }

  const reveal = $("#player-reveal");
  const cur = state.current;
  if (cur && cur.revealed) {
    reveal.classList.remove("hidden");
    reveal.innerHTML = `<div class="year">${cur.year}</div><div class="title">${esc(cur.title)}</div>` +
      `<div class="artist">${esc(cur.artist)}</div>`;
  } else {
    reveal.classList.add("hidden");
  }

  renderTimeline(mine);
  renderResults($("#player-results"));
  renderPlayers($("#player-players"));
}

function renderTimeline(mine) {
  const box = $("#timeline");
  box.innerHTML = "";
  const cards = mine.timeline;
  const canTap = state.state === "playing";

  for (let i = 0; i <= cards.length; i++) {
    if (canTap) {
      const slot = el("button", "slot" + (mine.pending === i ? " picked" : ""));
      slot.textContent = slotLabel(cards, i);
      slot.onclick = () => send({ action: "place", slot: i });
      box.appendChild(slot);
    }
    if (i < cards.length) {
      const c = cards[i];
      const card = el("div", "tcard");
      card.appendChild(el("div", "tcyear", String(c.year)));
      card.appendChild(el("div", "tctitle", `${c.title}`));
      card.appendChild(el("div", "tcartist", c.artist));
      box.appendChild(card);
    }
  }
}

function slotLabel(cards, i) {
  if (cards.length === 0) return "einzige Karte";
  if (i === 0) return `vor ${cards[0].year}`;
  if (i === cards.length) return `nach ${cards[cards.length - 1].year}`;
  return `zwischen ${cards[i - 1].year} und ${cards[i].year}`;
}

function renderResults(box) {
  if (!box) return;
  box.innerHTML = "";
  if (state.state !== "revealed" && state.state !== "finished") return;
  for (const r of state.results) {
    const row = el("div", "resultrow " + (r.correct ? "ok" : "bad"));
    row.appendChild(el("span", "rname", r.name));
    row.appendChild(el("span", "rmark", r.correct ? "richtig" : "falsch"));
    box.appendChild(row);
  }
}

function renderPlayers(box) {
  if (!box) return;
  box.innerHTML = "";
  for (const p of state.players) {
    const li = el("li", p.connected ? "" : "offline");
    li.appendChild(el("span", "pname", p.name));
    li.appendChild(el("span", "pscore", `${p.score}`));
    if (state.state === "playing" && p.pending) li.appendChild(el("span", "pdot", "getippt"));
    box.appendChild(li);
  }
}

function esc(text) {
  return String(text ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// ---------------------------------------------------------------- Start

const params = new URLSearchParams(location.search);
if (params.get("code")) {
  $("#join-code").value = params.get("code").toUpperCase();
  $("#join-name").focus();
}
const saved = JSON.parse(localStorage.getItem("jahresringe") || "null");
if (saved && saved.role) {
  me = { ...saved };
  showOnly(saved.role === "host" ? "#view-host" : "#view-player");
  $("#btn-leave").classList.remove("hidden");
}
connect();
