// Jahresringe — Client.
// Drei Rollen: host (steuert die Runden und spielt das Audio, raet nicht mit),
// player (tippt und sieht den Punktestand), solo (beides auf einem Geraet, ohne Code).
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
let clockOffset = 0;      // Sekunden: Browseruhr minus Serveruhr

// ---------------------------------------------------------------- Zeitbonus

function fmtMult(value) {
  return `×${value.toFixed(1).replace(".", ",")}`;
}

function multiplierNow() {
  if (!state || state.state !== "playing" || !state.round_started) return null;
  const elapsed = Math.max(0, Date.now() / 1000 - clockOffset - state.round_started);
  const fast = state.fast_seconds || 20;
  const max = state.max_multiplier || 3;
  if (elapsed >= fast) return 1;
  return Math.max(1, Math.round((1 + (max - 1) * (1 - elapsed / fast)) * 10) / 10);
}

function updateMultiplier() {
  const value = multiplierNow();
  for (const [view, badge] of [["#view-host", "#host-mult"], ["#view-player", "#player-mult"],
                               ["#view-solo", "#solo-mult"]]) {
    const node = $(badge);
    if (!node) continue;
    const visible = value !== null && !$(view).classList.contains("hidden");
    node.classList.toggle("hidden", !visible);
    if (visible) {
      node.textContent = fmtMult(value);
      node.classList.toggle("low", value <= 1.2);
    }
  }
}
setInterval(updateMultiplier, 200);

// ---------------------------------------------------------------- Verbindung

function wsUrl() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${location.host}/ws`;
}

function connect() {
  ws = new WebSocket(wsUrl());
  ws.onopen = () => {
    const saved = JSON.parse(localStorage.getItem("jahresringe") || "null");
    if (saved && saved.code && saved.id && saved.role !== "solo") {
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
    if (msg.now) clockOffset = Date.now() / 1000 - msg.now;   // Serverzeit statt Browseruhr
    me.code = msg.code;
    if (me.role === "host") renderHost();
    else if (me.role === "solo") renderSolo();
    else renderPlayer();
  } else if (msg.type === "joined") {
    me.role = msg.role;
    me.id = msg.player_id;
    me.code = msg.code;
    if (msg.name) me.name = msg.name;
    localStorage.setItem("jahresringe", JSON.stringify(me));
    showOnly(viewForRole(msg.role));
    $("#btn-leave").classList.remove("hidden");
  } else if (msg.type === "error") {
    setStatus(msg.message, true);
    showError(msg.message);
  } else if (msg.type === "warning") {
    setStatus(msg.message, true);
  }
}

function viewForRole(role) {
  return role === "host" ? "#view-host" : role === "solo" ? "#view-solo" : "#view-player";
}

// ---------------------------------------------------------------- Start

function showOnly(sel) {
  for (const v of document.querySelectorAll(".view")) v.classList.add("hidden");
  $(sel).classList.remove("hidden");
}

function showError(text) {
  const box = $("#start-error");
  if (box && !$("#view-start").classList.contains("hidden")) box.textContent = text || "";
}

function currentStatusBox() {
  for (const [view, box] of [["#view-host", "#host-status"], ["#view-player", "#player-status"],
                             ["#view-solo", "#solo-status"]]) {
    if (!$(view).classList.contains("hidden")) return $(box);
  }
  return null;
}

function setStatus(text, isError = false) {
  const box = currentStatusBox();
  if (box) {
    box.textContent = text || "";
    box.classList.toggle("error", isError);
  }
  if (isError) console.warn(text);
}

// ---------------------------------------------------------------- Genre-Filter

let genreOptions = [];
let pickedGenres = new Set(JSON.parse(localStorage.getItem("jahresringe-genres") || "[]"));

function saveGenres() {
  localStorage.setItem("jahresringe-genres", JSON.stringify([...pickedGenres]));
}

function renderChips() {
  for (const box of document.querySelectorAll("[data-chips]")) {
    box.innerHTML = "";
    for (const g of genreOptions) {
      const chip = el("button", "chip" + (pickedGenres.has(g.name) ? " on" : ""));
      chip.type = "button";
      chip.appendChild(document.createTextNode(g.name));
      chip.appendChild(el("span", "cnt", String(g.count)));
      chip.onclick = () => {
        if (pickedGenres.has(g.name)) pickedGenres.delete(g.name);
        else pickedGenres.add(g.name);
        saveGenres();
        renderChips();
      };
      box.appendChild(chip);
    }
    const reset = el("button", "chip clear", "zurücksetzen");
    reset.type = "button";
    reset.onclick = () => { pickedGenres.clear(); saveGenres(); renderChips(); };
    box.appendChild(reset);
  }
}

async function loadGenres() {
  try {
    const source = localStorage.getItem("jahresringe-source") || "";
    const res = await fetch(`/api/genres?playlist=${encodeURIComponent(source)}`);
    const all = await res.json();
    genreOptions = all.filter((g) => g.count >= 2);
    // Genres, die es in dieser Quelle nicht gibt, aus der Auswahl nehmen — sonst startet
    // die Runde mit einem Filter, der nichts trifft.
    const available = new Set(genreOptions.map((g) => g.name));
    let changed = false;
    for (const name of [...pickedGenres]) {
      if (!available.has(name)) {
        pickedGenres.delete(name);
        changed = true;
      }
    }
    if (changed) saveGenres();
    renderChips();
  } catch (err) {
    console.warn("Genres konnten nicht geladen werden", err);
  }
}

function chosenGenres() {
  return [...pickedGenres];
}

// ---------------------------------------------------------------- Quellen (Playlists)

let sources = { total: 0, curated: 0, items: [] };
let pickedSource = localStorage.getItem("jahresringe-source") || "";
let importTimer = null;

function sourceLabel(id) {
  if (!id) return "alle Karten";
  if (id === "kuratiert") return "kuratiert";
  const hit = (sources.items || []).find((s) => s.id === id);
  return hit ? hit.title : id;
}

function renderSources() {
  for (const box of document.querySelectorAll("[data-sources]")) {
    box.innerHTML = "";
    const options = [
      { id: "", name: "Alle Karten", count: sources.total },
      { id: "kuratiert", name: "Kuratiert", count: sources.curated },
      ...(sources.items || []).map((s) => ({ id: s.id, name: s.title,
                                             count: s.cards_in_pool })),
    ].filter((opt) => opt.count > 0);
    for (const opt of options) {
      const chip = el("button", "chip source" + (pickedSource === opt.id ? " on" : ""));
      chip.type = "button";
      chip.appendChild(document.createTextNode(opt.name));
      chip.appendChild(el("span", "cnt", String(opt.count)));
      chip.onclick = () => {
        pickedSource = opt.id;
        localStorage.setItem("jahresringe-source", pickedSource);
        renderSources();
        loadGenres();                 // Genre-Zaehler gehoeren zur gewaehlten Quelle
      };
      box.appendChild(chip);
    }
  }
}

async function loadSources() {
  try {
    const res = await fetch("/api/playlists");
    sources = await res.json();
    const known = (sources.items || []).some((s) => s.id === pickedSource);
    if (pickedSource && pickedSource !== "kuratiert" && !known) pickedSource = "";
    renderSources();
  } catch (err) {
    console.warn("Quellen konnten nicht geladen werden", err);
  }
}

function importStatus(text, cls) {
  const box = $("#import-status");
  if (!box) return;
  box.textContent = text || "";
  box.className = "hint " + (cls || "");
}

function pollImport(job) {
  clearInterval(importTimer);
  importTimer = setInterval(async () => {
    let status;
    try {
      status = await (await fetch(`/api/playlists/jobs/${job.id}`)).json();
    } catch (err) {
      return;                       // Netzhusten: beim naechsten Takt weiter
    }
    if (status.state === "running" || status.state === "queued") {
      importStatus(`${status.title || job.playlist_id}: ${status.processed}/${status.total} `
        + `geprüft, ${status.added} übernommen (${status.seconds} s)`, "progressline");
      return;
    }
    clearInterval(importTimer);
    $("#btn-import").disabled = false;
    if (status.state !== "done") {
      importStatus(`Import fehlgeschlagen: ${status.message}`);
      return;
    }
    const skipped = Object.entries(status.skipped || {}).map(([k, v]) => `${k}=${v}`).join(", ");
    importStatus(`${status.title}: ${status.added} Karten übernommen`
      + (skipped ? ` (übersprungen: ${skipped})` : ""), "progressline");
    $("#import-url").value = "";
    pickedSource = status.playlist_id;             // frisch importiert = gleich auswaehlen
    localStorage.setItem("jahresringe-source", pickedSource);
    await loadSources();
    await loadGenres();
  }, 1500);
}

$("#btn-import").onclick = async () => {
  const url = ($("#import-url").value || "").trim();
  if (!url) return importStatus("Playlist-Link oder ID einsetzen");
  importStatus("Import startet …", "progressline");
  $("#btn-import").disabled = true;
  try {
    const res = await fetch("/api/playlists", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
    if (!res.ok) {
      const problem = await res.json().catch(() => ({}));
      throw new Error(problem.detail || `HTTP ${res.status}`);
    }
    pollImport(await res.json());
  } catch (err) {
    importStatus(`Import fehlgeschlagen: ${err.message}`);
    $("#btn-import").disabled = false;
  }
};

$("#btn-create").onclick = () => {
  showError("");
  me.role = "host";
  me.name = ($("#host-name").value || "Host").trim().slice(0, 24);
  send({ action: "create", name: me.name, genres: chosenGenres(), playlist: pickedSource });
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

$("#btn-solo").onclick = () => {
  showError("");
  me.role = "solo";
  send({ action: "solo", name: "Solo", genres: chosenGenres(), playlist: pickedSource });
};

$("#btn-leave").onclick = () => {
  localStorage.removeItem("jahresringe");
  location.reload();
};

for (const [id, action] of [["#btn-start", "start"], ["#btn-next", "next"], ["#btn-reveal", "reveal"],
                            ["#btn-skip", "skip"], ["#btn-reset", "reset"]]) {
  $(id).onclick = () => send({ action });
}

$("#solo-next").onclick = () => send({ action: "next" });
$("#solo-reveal").onclick = () => send({ action: "reveal" });
$("#solo-skip").onclick = () => send({ action: "skip" });
$("#solo-restart").onclick = () => send({ action: "solo", name: "Solo", genres: chosenGenres(),
                                           playlist: pickedSource });

// ---------------------------------------------------------------- Audio

function audioBox(name) {
  const box = document.querySelector(`[data-audio="${name}"]`);
  if (!box) return null;
  if (!box.dataset.wired) {
    box.dataset.wired = "1";
    box.audio = box.querySelector("audio");
    box.bar = box.querySelector(".bar");
    box.clock = box.querySelector(".clock");
    box.hint = box.querySelector(".hint");
    box.querySelector(".play").onclick = () => {
      if (!box.audio.src) return;
      box.audio.currentTime = 0;
      box.audio.play().catch(() => {});
    };
    const sync = () => box.classList.toggle("is-playing", !box.audio.paused);
    box.audio.onplay = sync;
    box.audio.onpause = sync;
    box.audio.onended = sync;
    box.audio.ontimeupdate = () => {
      const dur = box.audio.duration || 30;
      box.bar.style.width = `${Math.min(100, (box.audio.currentTime / dur) * 100)}%`;
      box.clock.textContent = `${box.audio.currentTime.toFixed(1).replace(".", ",")} s`;
    };
  }
  return box;
}

function playPreview(name, url) {
  const box = audioBox(name);
  if (!box || box.audio.dataset.src === url) return;
  box.audio.dataset.src = url;
  box.audio.src = url;
  box.audio.currentTime = 0;
  box.hint.textContent = "";
  box.audio.play()
    .then(() => { box.hint.textContent = "30 Sekunden Ausschnitt — Play nochmal für den Anfang."; })
    .catch(() => { box.hint.textContent = "Autoplay blockiert: auf Play drücken."; });
}

// ---------------------------------------------------------------- Host

function renderHost() {
  $("#host-code").textContent = state.code;
  $("#host-url").textContent = `${location.origin}/?code=${state.code}`;
  const active = state.genres || [];
  const parts = [`Quelle: ${sourceLabel(state.playlist || "")}`];
  parts.push(active.length ? `Genres: ${active.join(", ")}` : "Genres: alle");
  $("#host-filter").textContent = parts.join(" · ");

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
  renderReveal($("#host-reveal"));

  const mine = state.me;
  if (mine) {
    $("#host-mine").textContent = `${mine.score} Karte${mine.score === 1 ? "" : "n"} · `
      + `${mine.points} Punkte`;
    renderTimeline($("#timeline-host"), mine);
    bumpScore($("#host-mine"), mine.score);
    celebrate(mine, $("#timeline-host"), $("#host-reveal"));
    if (playing) {
      setStatus(mine.pending === null ? "Du bist dran: tippe unten in deiner Timeline."
        : "Getippt — kannst noch ändern, bis du auflöst.");
    } else if (st === "revealed" && mine.correct_last !== null) {
      setStatus(mine.correct_last ? "Richtig!" : "Leider falsch.", mine.correct_last === false);
    }
  }

  const log = $("#host-log");
  log.innerHTML = "";
  for (const entry of state.log) {
    const li = el("li");
    li.appendChild(el("span", "lyear", entry.skipped ? "—" : String(entry.year)));
    li.appendChild(el("span", "ltitle", `${entry.title} — ${entry.artist}`));
    if (!entry.skipped) li.appendChild(el("span", "lwho", entry.correct.join(", ") || "niemand"));
    log.appendChild(li);
  }

  if (state.winner) setStatus(`Gewonnen: ${state.winner}`);
  if (state.current && state.current.preview && playing) playPreview("host", state.current.preview);
}

// ---------------------------------------------------------------- Spieler

function renderPlayer() {
  const mine = state.me;
  if (!mine) return;
  $("#player-name").textContent = mine.name;
  $("#player-score").textContent = `${mine.score} Karte${mine.score === 1 ? "" : "n"} · `
    + `${mine.points} Punkte`;

  const st = state.state;
  if (st === "lobby") setStatus("Warte auf den Host …");
  else if (st === "ready") setStatus("Gleich geht's los — der Host spielt die erste Karte.");
  else if (st === "playing") {
    setStatus(mine.pending === null ? "Hör zu und tippe, wohin die Karte gehört."
      : "Getippt — kannst noch ändern, bis der Host auflöst.");
  } else if (st === "revealed") {
    const delta = mine.delta ? ` +${mine.delta} Punkte (${fmtMult(mine.multiplier || 1)})` : "";
    setStatus((mine.correct_last === true ? "Richtig!" : "Leider falsch.") + delta,
              mine.correct_last === false);
  } else if (st === "finished") {
    setStatus(state.winner === mine.name ? "Du hast gewonnen!" : `Gewonnen: ${state.winner}`);
  }

  renderReveal($("#player-reveal"));
  renderTimeline($("#timeline"), mine);
  bumpScore($("#player-score"), mine.score);
  celebrate(mine, $("#timeline"), $("#player-reveal"));
  renderResults($("#player-results"));
  renderPlayers($("#player-players"));
}

// ---------------------------------------------------------------- Solo

function renderSolo() {
  const mine = state.me;
  if (!mine) return;
  const st = state.state;
  const playing = st === "playing";

  $("#solo-label").textContent = "Solo";
  $("#solo-score").textContent = `${mine.score} / ${state.target} · ${mine.points} Punkte`;
  $("#solo-next").classList.toggle("hidden", !(st === "ready" || st === "revealed"));
  $("#solo-next").textContent = state.round === 0 ? "Erste Karte abspielen" : "Nächste Karte";
  $("#solo-reveal").classList.toggle("hidden", !playing);
  $("#solo-skip").classList.toggle("hidden", !playing);
  $("#solo-audio-box").classList.toggle("hidden", !(playing && state.current));

  if (st === "lobby" || st === "ready") setStatus("Karte abspielen, dann tippen, wo sie hingehört.");
  else if (st === "playing") {
    setStatus(mine.pending === null ? "Wo gehört die Karte hin?" : "Getippt — jetzt auflösen.");
  } else if (st === "revealed") {
    const delta = mine.delta ? ` +${mine.delta} Punkte (${fmtMult(mine.multiplier || 1)})` : "";
    setStatus((mine.correct_last === true ? "Richtig!" : "Leider falsch.") + delta,
              mine.correct_last === false);
  } else if (st === "finished") {
    setStatus(`${state.target} Karten geschafft — ${mine.points} Punkte. Neues Spiel?`);
  }

  renderReveal($("#solo-revealbox"));
  renderTimeline($("#timeline-solo"), mine);
  bumpScore($("#solo-score"), mine.score);
  celebrate(mine, $("#timeline-solo"), $("#solo-revealbox"));

  const log = $("#solo-log");
  log.innerHTML = "";
  for (const entry of state.log) {
    const li = el("li");
    li.appendChild(el("span", "lyear", entry.skipped ? "—" : String(entry.year)));
    li.appendChild(el("span", "ltitle", `${entry.title} — ${entry.artist}`));
    if (!entry.skipped) li.appendChild(el("span", "lwho", entry.correct.length ? "richtig" : "falsch"));
    log.appendChild(li);
  }

  if (state.current && state.current.preview && playing) playPreview("solo", state.current.preview);
}

// ---------------------------------------------------------------- Bausteine

// ---------------------------------------------------------------- Effekte

const FX = (() => {
  const canvas = document.getElementById("fx");
  const ctx = canvas ? canvas.getContext("2d") : null;
  const COLORS = ["#ff2e93", "#ff8a3d", "#ffcf2e", "#b6ff3d", "#00e0ff", "#6b2bff", "#ffffff"];
  let particles = [];
  let raf = null;

  function resize() {
    if (!canvas) return;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    canvas.width = window.innerWidth * dpr;
    canvas.height = window.innerHeight * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  window.addEventListener("resize", resize);
  resize();

  function spawn(count, origin, spread = 1) {
    if (!ctx) return;
    for (let i = 0; i < count; i++) {
      const angle = Math.random() * Math.PI * 2;
      const speed = (3 + Math.random() * 9) * spread;
      particles.push({
        x: origin.x, y: origin.y,
        vx: Math.cos(angle) * speed,
        vy: Math.sin(angle) * speed - 4,
        size: 5 + Math.random() * 7,
        rot: Math.random() * Math.PI,
        vr: (Math.random() - 0.5) * 0.4,
        color: COLORS[(Math.random() * COLORS.length) | 0],
        life: 1,
      });
    }
    if (!raf) raf = requestAnimationFrame(loop);
  }

  function loop() {
    raf = requestAnimationFrame(loop);
    ctx.clearRect(0, 0, window.innerWidth, window.innerHeight);
    particles = particles.filter((p) => p.life > 0);
    for (const p of particles) {
      p.vy += 0.28;              // Schwerkraft
      p.vx *= 0.995;
      p.x += p.vx;
      p.y += p.vy;
      p.rot += p.vr;
      p.life -= 0.011;
      ctx.save();
      ctx.globalAlpha = Math.max(0, Math.min(1, p.life));
      ctx.translate(p.x, p.y);
      ctx.rotate(p.rot);
      ctx.fillStyle = p.color;
      ctx.fillRect(-p.size / 2, -p.size / 3, p.size, p.size * 0.66);
      ctx.restore();
    }
    if (!particles.length) {
      cancelAnimationFrame(raf);
      raf = null;
      ctx.clearRect(0, 0, window.innerWidth, window.innerHeight);
    }
  }

  function burst() {
    spawn(130, { x: window.innerWidth / 2, y: window.innerHeight * 0.42 });
  }

  function rain() {
    let shots = 0;
    const timer = setInterval(() => {
      spawn(22, { x: Math.random() * window.innerWidth, y: -20 }, 0.7);
      if (++shots > 14) clearInterval(timer);
    }, 220);
  }

  return { burst, rain };
})();

const fxDone = { key: "" };

function celebrate(mine, timelineEl, revealEl) {
  if (!mine || !state) return;
  const key = `${state.round}:${state.state}`;
  if (fxDone.key === key) return;
  fxDone.key = key;

  if (state.state === "revealed" && mine.correct_last === true) {
    FX.burst();
  } else if (state.state === "revealed" && mine.correct_last === false && timelineEl) {
    timelineEl.classList.remove("shake");
    void timelineEl.offsetWidth;
    timelineEl.classList.add("shake");
    setTimeout(() => timelineEl.classList.remove("shake"), 700);
  } else if (state.state === "finished" && state.winner === mine.name) {
    FX.rain();
  }
}

const scoreSeen = new Map();
function bumpScore(el, value) {
  if (!el) return;
  if (scoreSeen.get(el) !== undefined && scoreSeen.get(el) !== value) {
    el.classList.remove("bump");
    void el.offsetWidth;
    el.classList.add("bump");
  }
  scoreSeen.set(el, value);
}

function renderReveal(box) {
  const cur = state.current;
  if (cur && cur.revealed) {
    box.classList.remove("hidden");
    box.innerHTML = `<div class="year">${cur.year}</div><div class="title">${esc(cur.title)}</div>` +
      `<div class="artist">${esc(cur.artist)}</div>`;
  } else {
    box.classList.add("hidden");
  }
}

function renderTimeline(box, mine) {
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
      card.appendChild(el("div", "tctitle", c.title));
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
  if (state.winner) {
    const leader = state.players.find((p) => p.name === state.winner);
    const detail = leader ? ` (${leader.points} Punkte, ${leader.score} Karten)` : "";
    box.appendChild(el("div", "winner-banner", `Gewonnen: ${state.winner}${detail}`));
  }
  if (state.state !== "revealed" && state.state !== "finished") return;
  for (const r of state.results) {
    const row = el("div", "resultrow " + (r.correct ? "ok" : "bad"));
    row.appendChild(el("span", "rname", r.name));
    const mark = r.correct
      ? `+${r.delta} · ${fmtMult(r.multiplier || 1)} · ${r.points} P`
      : "falsch";
    row.appendChild(el("span", "rmark", mark));
    box.appendChild(row);
  }
}

function renderPlayers(box) {
  if (!box) return;
  box.innerHTML = "";
  for (const p of state.players) {
    const li = el("li", p.connected ? "" : "offline");
    li.appendChild(el("span", "pname", p.name));
    li.appendChild(el("span", "pscore", String(p.points)));
    li.appendChild(el("span", "pcards", `${p.score} K`));
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
if (saved && saved.role && saved.role !== "solo") {
  me = { ...saved };
  showOnly(viewForRole(saved.role));
  $("#btn-leave").classList.remove("hidden");
}
loadGenres();
loadSources();
connect();
