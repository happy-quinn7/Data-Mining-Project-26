// app.js — chat page that records keystrokes typed in the message box, in the
// same format as the Chrome extension (collector/content.js), and saves them
// to data/raw/ through server.py.

const STORE = "typing-study-v1";   // localStorage key: the session survives page reloads
const TARGET = 2000;               // keystrokes per session (MIN_KEYSTROKES in tools/clean_raw.py)
const AUTOSAVE_MS = 60_000;

const $ = (id) => document.getElementById(id);
const input = $("input");

let state = load() || fresh();
let status = { model: "", problem: null, existing: [] };   // from /api/status
const activePresses = {};                   // event.code -> press_id, for linking up to down
let busy = false;                           // waiting for the assistant's reply
let saving = false;

function fresh() {
  return {
    mode: "record",    // "record": a study session; "identify": guess who is typing (never saved)
    participantId: "", sessionId: "S1",
    started: false, recording: false, startedAt: null, consentAt: null,
    completed: false,  // reached TARGET keystrokes
    finished: false,   // participant pressed "Finish session"
    pressCounter: 0,   // gives each press a unique id (= number of presses recorded)
    sampleId: 0,       // one message = one sample
    events: [],        // rows in the class CSV format
    savedRows: 0,      // events already written to data/raw
    savedAt: null,
    overwriteOk: false,
    messages: [],      // the conversation sent to the model
  };
}

// --- persistence ---------------------------------------------------------------
function load() {
  try { return JSON.parse(localStorage.getItem(STORE)); } catch { return null; }
}

let dirty = false;
function touch() { dirty = true; }
function persist() {
  dirty = false;
  if (state.mode === "identify") return;   // guess mode is never stored
  try {
    localStorage.setItem(STORE, JSON.stringify(state));
  } catch {
    $("save-msg").textContent = "Browser storage is full. Save or download now.";
  }
}
setInterval(() => { if (dirty) persist(); }, 1000);
window.addEventListener("pagehide", persist);
document.addEventListener("visibilitychange", () => { if (document.hidden) persist(); });

// --- high-resolution millisecond clock (same as the extension) ------------------
function nowMs() {
  return performance.timeOrigin + performance.now();
}

// --- recording ---------------------------------------------------------------------
function record(type, e, pressId) {
  state.events.push({
    participant_id: state.participantId,
    session_id: state.sessionId,
    sample_id: state.sampleId,
    event: type,
    key_code: e.code,                     // physical key, e.g. "KeyA"
    timestamp_ms: nowMs().toFixed(3),
    press_id: pressId,
    key: e.key,                           // character produced, e.g. "a"
  });
  touch();
}

input.addEventListener("keydown", (e) => {
  if (state.recording && !e.repeat) {     // ignore auto-repeat (held key)
    const pid = ++state.pressCounter;
    activePresses[e.code] = pid;          // remember it for the matching keyup
    record("down", e, pid);
    showProgress();
    scheduleIdentify();
  }
  // Enter (without Shift) sends the message -> end of this sample.
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    send();
  }
});

// Releases are linked to presses made in the message box, wherever focus is by then.
document.addEventListener("keyup", (e) => {
  if (!state.recording) return;
  const pid = activePresses[e.code];
  if (pid === undefined) return;          // no recorded press -> skip
  delete activePresses[e.code];
  record("up", e, pid);
});

// --- chat ----------------------------------------------------------------------------
input.addEventListener("input", () => { autosize(); updateSend(); });

$("send").addEventListener("click", () => { send(); input.focus(); });

function send() {
  const text = input.value.trim();
  if (!text || busy || !state.started) return;
  state.sampleId += 1;                    // the next keystrokes belong to a new message
  state.messages.push({ role: "user", content: text });
  input.value = "";
  autosize();
  addBubble("user", text);
  showStats();
  persist();
  ask();
}

async function ask() {
  busy = true;
  updateSend();
  const bubble = addBubble("assistant", "");
  bubble.classList.add("pending");
  let reply = "";
  let error = null;

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages: state.messages }),
    });
    if (!res.ok) {
      error = (await res.json().catch(() => ({}))).error || `Server error ${res.status}`;
    } else {
      // The reply streams in as one JSON object per line: {"text"}, then {"done"} or {"error"}.
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        let nl;
        while ((nl = buf.indexOf("\n")) >= 0) {
          const line = buf.slice(0, nl);
          buf = buf.slice(nl + 1);
          if (!line) continue;
          const msg = JSON.parse(line);
          if (msg.text) {
            reply += msg.text;
            bubble.classList.remove("pending");
            bubble.innerHTML = formatReply(reply);
            scrollDown();
          }
          if (msg.error) error = msg.error;
        }
      }
    }
  } catch {
    error = "Could not reach the server. Is frontend/server.py still running?";
  }

  if (!error && !reply) error = "The assistant sent an empty reply.";
  if (error) {
    bubble.remove();                      // a partial reply is discarded
    addNotice(error, true);
  } else {
    state.messages.push({ role: "assistant", content: reply });
  }
  busy = false;
  persist();
  updateSend();
}

// --- welcome screen ------------------------------------------------------------------
for (const id of ["participant", "consent"]) $(id).addEventListener("input", updateStart);
function updateStart() {
  $("start").disabled = !$("consent").checked || !$("participant").value.trim();
}

$("setup").addEventListener("submit", (e) => {
  e.preventDefault();
  const digits = $("participant").value.trim().replace(/^p/i, "");
  if (!/^\d{1,4}$/.test(digits)) {
    $("setup-msg").textContent = "Use a participant ID like P01.";
    return;
  }
  const pid = "P" + digits.padStart(2, "0");
  const sid = $("session").value;
  const name = `${pid}_${sid}.csv`;
  let overwriteOk = false;
  if (status.existing.includes(name)) {
    if (!confirm(`data/raw/${name} already exists.\n\nSaving this session will replace it ` +
                 `(the old file is kept in data/raw/backups/). Continue?`)) return;
    overwriteOk = true;
  }
  state = Object.assign(fresh(), {
    participantId: pid, sessionId: sid, started: true, recording: true,
    startedAt: Date.now(), consentAt: new Date().toISOString(), overwriteOk,
  });
  persist();
  clearTranscript();
  render();
  input.focus();
});

// --- end of session --------------------------------------------------------------------
$("finish").addEventListener("click", async () => {
  if (await save()) {
    state.finished = true;
    state.recording = false;
    persist();
    render();
  } else {
    addNotice("The session could not be saved. Please tell the researcher before closing this tab.");
  }
});

// --- researcher menu --------------------------------------------------------------------
$("menu-btn").addEventListener("click", () => setMenu($("menu").hidden));
document.addEventListener("click", (e) => {
  if (!$("menu").hidden && !e.target.closest("#menu, #menu-btn")) setMenu(false);
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape") setMenu(false); });

function setMenu(open) {
  if ($("menu").hidden === !open) return;
  $("menu").hidden = !open;
  $("menu-btn").setAttribute("aria-expanded", open);
  if (open) showStats();
  else if (!$("chat").hidden) input.focus();
}

$("toggle").addEventListener("click", () => {
  state.recording = !state.recording;
  persist();
  render();
});

$("save").addEventListener("click", () => save());

$("download").addEventListener("click", () => {
  const header = "participant_id,session_id,sample_id,event,key_code,timestamp_ms,press_id,key";
  const lines = state.events.map((r) => [
    r.participant_id, r.session_id, r.sample_id, r.event,
    r.key_code, r.timestamp_ms, r.press_id, csvField(r.key),
  ].join(","));
  const blob = new Blob([[header, ...lines].join("\n") + "\n"], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${state.participantId}_${state.sessionId}.csv`;   // e.g. P07_S1.csv
  a.click();
  URL.revokeObjectURL(url);
});

$("reset").addEventListener("click", () => newSession(true));
$("restart").addEventListener("click", () => newSession(state.events.length > state.savedRows));

function newSession(ask) {
  const unsaved = state.events.length - state.savedRows;
  const question = unsaved > 0
    ? `${unsaved} recorded events have not been saved and will be deleted.\n\nStart a new session anyway?`
    : "Start a new session? The chat will be cleared.";
  if (ask && !confirm(question)) return;
  const next = { S1: "S2", S2: "S3", S3: "S3" }[state.sessionId];
  const pid = state.participantId;
  state = fresh();
  persist();
  clearTranscript();
  setMenu(false);
  $("participant").value = pid;
  $("session").value = next;
  $("consent").checked = false;
  updateStart();
  render();
  refreshStatus();
}

// Write the session to data/raw/<participant>_<session>.csv. A later save of the
// same session simply replaces the file; a different recording with that name is
// only replaced after confirmation (the server keeps a backup). Returns true on success.
async function save(auto = false) {
  if (saving) return false;
  if (!state.events.length) {
    if (!auto) $("save-msg").textContent = "Nothing recorded yet.";
    return false;
  }
  saving = true;
  const rows = state.events.length;
  if (!auto) $("save-msg").textContent = "Saving…";
  let ok = false;
  try {
    const res = await fetch("/api/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        participant_id: state.participantId, session_id: state.sessionId,
        events: state.events, overwrite: state.overwriteOk,
      }),
    });
    const out = await res.json().catch(() => ({ error: `Server error ${res.status}` }));
    if (res.status === 409) {
      if (!auto && confirm(`${out.error}.\n\nReplace it? The old file is moved to data/raw/backups/.`)) {
        state.overwriteOk = true;
        saving = false;
        return save();
      }
      $("save-msg").textContent = `Not saved: ${out.error}.`;
    } else if (!res.ok) {
      $("save-msg").textContent = `Not saved: ${out.error}`;
    } else {
      state.savedRows = rows;
      state.savedAt = Date.now();
      state.overwriteOk = false;          // the file on disk is now this session's own
      $("save-msg").textContent = `${auto ? "Autosaved" : "Saved"} ${out.saved} at ${clock(state.savedAt)}`;
      persist();
      ok = true;
    }
  } catch {
    $("save-msg").textContent = "Not saved: the server is not reachable. Use Download CSV.";
  }
  saving = false;
  showSaved();
  return ok;
}

setInterval(() => {
  if (state.mode === "record" && state.started && !state.finished &&
      state.events.length > state.savedRows) save(true);
}, AUTOSAVE_MS);

// --- guess mode: who is typing? --------------------------------------------------------
for (const [tab, pane] of [["tab-record", "setup"], ["tab-identify", "identify-intro"]]) {
  $(tab).addEventListener("click", () => showTab(tab));
}
function showTab(active) {
  for (const [tab, pane] of [["tab-record", "setup"], ["tab-identify", "identify-intro"]]) {
    $(tab).setAttribute("aria-selected", tab === active);
    $(pane).hidden = tab !== active;
  }
}

$("start-identify").addEventListener("click", () => {
  state = Object.assign(fresh(), { mode: "identify", started: true, recording: true, startedAt: Date.now() });
  guess = { lastOut: null, feedbackGiven: false };
  clearTranscript();
  resetGuessPanel();
  render();
  input.focus();
});

$("exit-identify").addEventListener("click", () => {
  clearTimeout(idTimer);
  idTimer = null;
  state = load() || fresh();              // back to the stored study session, untouched
  clearTranscript();
  for (const m of state.messages) addBubble(m.role, m.content);
  render();
  refreshStatus();
});

let guess = { lastOut: null, feedbackGiven: false };
let idTimer = null;
let idBusy = false;

// Ask for a new guess at most every 0.8 s while the participant types.
function scheduleIdentify() {
  if (state.mode !== "identify" || idTimer) return;
  idTimer = setTimeout(runIdentify, 800);
}

async function runIdentify() {
  idTimer = null;
  if (state.mode !== "identify") return;
  if (idBusy) return scheduleIdentify();
  idBusy = true;
  const sent = state.events.length;
  let out = null;
  let error = null;
  try {
    const res = await fetch("/api/identify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ events: state.events }),
    });
    out = await res.json().catch(() => ({ error: `Server error ${res.status}` }));
    if (!res.ok) error = out.error;
  } catch {
    error = "The server is not reachable.";
  }
  idBusy = false;
  if (state.mode !== "identify") return;
  $("guess-error").hidden = !error;
  $("guess-error").textContent = error || "";
  if (!error) showGuess(out);
  if (state.events.length !== sent) scheduleIdentify();
}

function showGuess(out) {
  guess.lastOut = out;
  $("guess-wait").hidden = out.ready;
  $("guess-result").hidden = !out.ready;
  $("guess-feedback").hidden = !out.ready || guess.feedbackGiven;
  if (!out.ready) {
    $("guess-wait-msg").textContent = `${out.keys} key presses so far. A guess needs a few ` +
      "repeated letter pairs, usually 60 to 100 key presses.";
    return;
  }
  const ranked = out.ranking.filter((r) => r.distance !== null);
  const [best, second] = ranked;
  $("guess-name").textContent = best.participant;
  $("guess-margin").textContent = second
    ? `${Math.round((100 * (second.distance - best.distance)) / second.distance)}% closer than ${second.participant}`
    : "";
  $("guess-basis").textContent = out.keys < 200
    ? `Early guess from ${out.keys} key presses: it gets more reliable as you type.`
    : `Based on ${out.keys} key presses. Lower distance means more alike.`;

  $("guess-ranking").replaceChildren(...out.ranking.map((r) => {
    const li = document.createElement("li");
    li.classList.toggle("best", r === best);
    const bar = r.distance === null ? 0 : (100 * best.distance) / r.distance;
    li.innerHTML = `<span class="who-name"></span>
      <span class="bar"><span style="width:${bar.toFixed(1)}%"></span></span>
      <span class="dist"></span>`;
    li.querySelector(".who-name").textContent = r.participant;
    li.querySelector(".dist").textContent = r.distance === null ? "n/a" : r.distance.toFixed(2);
    return li;
  }));

  if (!$("guess-choices").children.length) {
    for (const p of [...out.enrolled, "unknown"]) {
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = p === "unknown" ? "Not listed" : p;
      b.addEventListener("click", () => sendFeedback(p));
      $("guess-choices").appendChild(b);
    }
  }
}

async function sendFeedback(actual) {
  const out = guess.lastOut;
  const best = out.ranking.find((r) => r.distance !== null);
  try {
    const res = await fetch("/api/identify/feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ predicted: best.participant, actual, keys: out.keys, ranking: out.ranking }),
    });
    if (!res.ok) throw new Error((await res.json()).error);
    guess.feedbackGiven = true;
    $("guess-feedback").hidden = true;
    $("guess-basis").textContent = actual === best.participant
      ? "Right! Thanks, that's been logged."
      : `Thanks: logged as ${actual === "unknown" ? "someone not listed" : actual}.`;
  } catch (e) {
    $("feedback-msg").textContent = `Could not log the answer: ${e.message}`;
  }
  input.focus();
}

function resetGuessPanel() {
  $("guess-wait").hidden = false;
  $("guess-result").hidden = true;
  $("guess-feedback").hidden = true;
  $("guess-error").hidden = true;
  $("guess-choices").replaceChildren();
  $("feedback-msg").textContent = "";
  $("guess-wait-msg").textContent = "A guess needs a few repeated letter pairs, usually 60 to 100 key presses.";
}

// --- rendering --------------------------------------------------------------------------
function render() {
  const screen = !state.started ? "welcome" : state.finished ? "done" : "chat";
  const identifying = state.mode === "identify";
  for (const id of ["welcome", "chat", "done"]) $(id).hidden = id !== screen;
  document.body.classList.toggle("identify", identifying);
  $("guess").hidden = !identifying;
  $("mode-badge").hidden = !identifying;
  $("progress").hidden = screen !== "chat" || identifying;
  $("menu-btn").hidden = !state.started || identifying;
  $("paused").hidden = screen !== "chat" || state.recording;
  if (!state.started || identifying) setMenu(false);

  if (state.started) {
    $("who").textContent = `${state.participantId} · ${state.sessionId}`;
    $("rec").textContent = state.recording ? "REC" : state.finished ? "Finished" : "Paused";
    $("rec").classList.toggle("off", !state.recording);
    $("toggle").textContent = state.recording ? "Pause recording" : "Resume recording";
    $("toggle").disabled = state.finished;
  } else if (!$("participant").value) {
    $("participant").value = state.participantId;
  }
  if (screen === "done") {
    const min = Math.max(1, Math.round((Date.now() - state.startedAt) / 60000));
    const msgs = state.messages.filter((m) => m.role === "user").length;
    const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
    $("done-summary").textContent = `${plural(msgs, "message")} · about ${plural(min, "minute")}`;
  }
  input.placeholder = state.recording ? "Type your message…" : "Recording paused: typing is not recorded";
  showProgress();
  showStats();
  showSaved();
  updateSend();
}

// The participant sees a percentage, not a running count of keys (watching a
// number climb changes how people type). The exact count is in the researcher menu.
function showProgress() {
  const pct = Math.min(100, Math.floor((100 * state.pressCounter) / TARGET));
  $("bar-fill").style.width = pct + "%";
  $("pct").textContent = pct >= 100 ? "Done" : pct + "%";
  $("progress").classList.toggle("done", pct >= 100);
  document.querySelector(".track").setAttribute("aria-valuenow", pct);
  $("count").textContent = state.pressCounter.toLocaleString();

  if (state.mode === "record" && state.started && !state.completed && state.pressCounter >= TARGET) {
    state.completed = true;
    persist();
    save(true);
  }
  $("complete").hidden = !state.completed || state.finished;
}

function showStats() {
  $("messages-sent").textContent = state.messages.filter((m) => m.role === "user").length;
  const min = state.startedAt ? Math.floor((Date.now() - state.startedAt) / 60000) : 0;
  $("elapsed").textContent = `${min} min`;
}
setInterval(showStats, 30_000);

function showSaved() {
  const upToDate = state.savedAt && state.savedRows > 0;
  $("saved").hidden = !upToDate || !state.started;
  if (upToDate) $("saved").title = `Saved to data/raw at ${clock(state.savedAt)}`;
}

function clock(t) {
  return new Date(t).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function updateSend() {
  $("send").disabled = !state.started || busy || !input.value.trim();
}

function autosize() {
  input.style.height = "auto";
  input.style.height = Math.min(input.scrollHeight, 200) + "px";
}

function addBubble(role, text) {
  $("empty").hidden = true;
  const div = document.createElement("div");
  div.className = `bubble ${role}`;
  if (role === "user") div.textContent = text;
  else div.innerHTML = formatReply(text);
  $("transcript").appendChild(div);
  scrollDown();
  return div;
}

function addNotice(text, retry) {
  const div = document.createElement("div");
  div.className = "notice";
  div.textContent = text;
  if (retry) {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = "Try again";
    b.addEventListener("click", () => { div.remove(); ask(); });
    div.appendChild(b);
  }
  $("transcript").appendChild(div);
  scrollDown();
}

function clearTranscript() {
  for (const el of [...$("transcript").children]) if (el.id !== "empty") el.remove();
  $("empty").hidden = false;
}

function scrollDown() {
  const t = $("transcript");
  t.scrollTop = t.scrollHeight;
}

// Escape the reply, then render the bits of Markdown chat replies commonly use.
function formatReply(text) {
  return text
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/`([^`\n]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
}

// The `key` field can be a comma, quote, or newline -> quote it safely (RFC 4180).
function csvField(v) {
  const s = String(v);
  if (/[",\n]/.test(s)) return '"' + s.replace(/"/g, '""') + '"';
  return s;
}

async function refreshStatus() {
  let text;
  try {
    status = await (await fetch("/api/status")).json();
    text = `Assistant: ${status.model}` + (status.problem ? `. Not ready: ${status.problem}` : "");
  } catch {
    status.problem = "server not reachable";
    text = "Server not reachable: start frontend/server.py.";
  }
  for (const el of document.querySelectorAll(".model")) {
    el.textContent = text;
    el.classList.toggle("warn", !!status.problem);
  }
  const enrolled = status.enrolled || [];
  $("enrolled-list").textContent = enrolled.length ? enrolled.join(", ") : "nobody yet";
  $("start-identify").disabled = enrolled.length < 2;   // a guess needs at least two people to choose from
}

// --- start-up: restore a session in progress --------------------------------------------
for (const m of state.messages) addBubble(m.role, m.content);
const last = state.messages[state.messages.length - 1];
if (last && last.role === "user" && !state.finished) addNotice("The last reply was interrupted.", true);
render();
updateStart();
refreshStatus();
if (state.started && !state.finished) input.focus();
