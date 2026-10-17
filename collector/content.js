// content.js — records keydown/keyup in the LLM chat box only.

let enabled = false;        // master on/off (from the popup)
let participantId = "P00";  // your pseudonym
let sessionId = "S1";       // current session

let sampleId = 0;           // burst counter (one message = one sample)
let pressCounter = 0;       // gives each press a unique id
const activePresses = {};   // event.code -> press_id, for linking up to down

// --- high-resolution millisecond clock -------------------------------------
function nowMs() {
  // performance.timeOrigin is the page-load wall-clock time; performance.now()
  // is sub-millisecond elapsed time since then. Sum = precise absolute ms.
  return performance.timeOrigin + performance.now();
}

// --- is this element something we are allowed to record? -------------------
function isChatInput(el) {
  if (!el) return false;
  // NEVER record password fields.
  if (el.tagName === "INPUT" && el.type === "password") return false;
  // Record only editable text targets: the chat box is a <textarea> or a
  // contenteditable div on every major assistant.
  const editable =
    el.tagName === "TEXTAREA" ||
    (el.tagName === "INPUT" && (el.type === "text" || el.type === "search")) ||
    el.isContentEditable;
  return editable;
}

// --- load settings and keep them in sync -----------------------------------
function loadSettings() {
  chrome.storage.local.get(
    ["enabled", "participantId", "sessionId"],
    (s) => {
      enabled = !!s.enabled;
      participantId = s.participantId || "P00";
      sessionId = s.sessionId || "S1";
      updateIndicator();
    }
  );
}
loadSettings();
chrome.storage.onChanged.addListener((changes) => {
  if (changes.events && changes.events.newValue && changes.events.newValue.length === 0) {
    pressCounter = 0; sampleId = 0; queue.length = 0;   // popup "Clear data"
  }
  if (changes.enabled || changes.participantId || changes.sessionId) loadSettings();
});

// Counters survive page reloads so press_id / sample_id never restart mid-session.
let ready = false;
chrome.storage.local.get(["pressCounter", "sampleId"], (c) => {
  pressCounter = c.pressCounter || 0;
  sampleId = c.sampleId || 0;
  ready = true;
});

// --- store event rows --------------------------------------------------------
// Events go to an in-memory queue and are written in batches, one write at a time.
// (Writing each event with get -> push -> set raced under fast typing and lost
// ~25% of events.)
const queue = [];
let flushing = false;

function record(row) {
  queue.push(row);
  flush();
}

function flush() {
  if (flushing || queue.length === 0) return;
  flushing = true;
  const batch = queue.splice(0, queue.length);
  chrome.storage.local.get({ events: [] }, (data) => {
    chrome.storage.local.set(
      { events: data.events.concat(batch), pressCounter, sampleId },
      () => { flushing = false; flush(); }   // events queued meanwhile go next
    );
  });
}

setInterval(flush, 500);                                    // safety net
window.addEventListener("pagehide", flush);
document.addEventListener("visibilitychange", () => { if (document.hidden) flush(); });

// --- keydown ----------------------------------------------------------------
document.addEventListener(
  "keydown",
  (e) => {
    if (!enabled || !ready) return;
    if (e.repeat) return;                 // ignore auto-repeat (held key)
    if (!isChatInput(e.target)) return;   // chat box only, no password fields

    const pid = ++pressCounter;           // unique id for this press
    activePresses[e.code] = pid;          // remember it for the matching keyup

    record({
      participant_id: participantId,
      session_id: sessionId,
      sample_id: sampleId,
      event: "down",
      key_code: e.code,                   // physical key, e.g. "KeyA"
      timestamp_ms: nowMs().toFixed(3),
      press_id: pid,
      key: e.key                          // character produced, e.g. "a"
    });

    // Enter (without Shift) sends the message -> end of this burst/sample.
    if (e.key === "Enter" && !e.shiftKey) {
      sampleId += 1;
    }
  },
  true // capture phase: see the event before the page handles it
);

// --- keyup ------------------------------------------------------------------
document.addEventListener(
  "keyup",
  (e) => {
    if (!enabled || !ready) return;
    if (!isChatInput(e.target)) return;

    const pid = activePresses[e.code];    // link back to the press
    if (pid === undefined) return;        // no matching down -> skip
    delete activePresses[e.code];

    record({
      participant_id: participantId,
      session_id: sessionId,
      sample_id: sampleId,
      event: "up",
      key_code: e.code,
      timestamp_ms: nowMs().toFixed(3),
      press_id: pid,                      // same id as the down row
      key: e.key
    });
  },
  true
);

// --- visible "recording" indicator -----------------------------------------
let badge = null;
function updateIndicator() {
  if (enabled && !badge) {
    badge = document.createElement("div");
    badge.id = "ksd-recording-badge";
    badge.textContent = "● REC — keystroke collector on";
    document.body.appendChild(badge);
  } else if (!enabled && badge) {
    badge.remove();
    badge = null;
  }
}
