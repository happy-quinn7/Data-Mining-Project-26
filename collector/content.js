// records keydown/keyup in the chat box only

let enabled = false;        // on/off from popup
let participantId = "P00";  // pseudonym
let sessionId = "S1";       // session

let sampleId = 0;           // one per message
let pressCounter = 0;       // press id
const activePresses = {};   // code -> press_id


// ms timestamp
function nowMs() {
  // page load time + time since, sub-ms
  return performance.timeOrigin + performance.now();
}


// can we record this element?
function isChatInput(el) {
  if (!el) return false;
  // never passwords
  if (el.tagName === "INPUT" && el.type === "password") return false;
  // chat boxes are a textarea or contenteditable
  const editable =
    el.tagName === "TEXTAREA" ||
    (el.tagName === "INPUT" && (el.type === "text" || el.type === "search")) ||
    el.isContentEditable;
  return editable;
}


// load settings, keep in sync
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
    pressCounter = 0; sampleId = 0; queue.length = 0;   // popup clear
  }
  if (changes.enabled || changes.participantId || changes.sessionId) loadSettings();
});

// counters survive reloads
let ready = false;
chrome.storage.local.get(["pressCounter", "sampleId"], (c) => {
  pressCounter = c.pressCounter || 0;
  sampleId = c.sampleId || 0;
  ready = true;
});


// queue events, write in batches (one write per event lost ~25%)
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
      () => { flushing = false; flush(); }   // then the rest
    );
  });
}

setInterval(flush, 500);                                    // backup flush
window.addEventListener("pagehide", flush);
document.addEventListener("visibilitychange", () => { if (document.hidden) flush(); });


document.addEventListener(
  "keydown",
  (e) => {
    if (!enabled || !ready) return;
    if (e.repeat) return;                 // skip held keys
    if (!isChatInput(e.target)) return;   // chat box only

    const pid = ++pressCounter;           // new press id
    activePresses[e.code] = pid;          // for the keyup

    record({
      participant_id: participantId,
      session_id: sessionId,
      sample_id: sampleId,
      event: "down",
      key_code: e.code,                   // eg "KeyA"
      timestamp_ms: nowMs().toFixed(3),
      press_id: pid,
      key: e.key                          // eg "a"
    });

    // enter sends
    if (e.key === "Enter" && !e.shiftKey) {
      sampleId += 1;
    }
  },
  true // capture, before the page sees it
);


document.addEventListener(
  "keyup",
  (e) => {
    if (!enabled || !ready) return;
    if (!isChatInput(e.target)) return;

    const pid = activePresses[e.code];    // find the press
    if (pid === undefined) return;        // not ours
    delete activePresses[e.code];

    record({
      participant_id: participantId,
      session_id: sessionId,
      sample_id: sampleId,
      event: "up",
      key_code: e.code,
      timestamp_ms: nowMs().toFixed(3),
      press_id: pid,                      // same id as down
      key: e.key
    });
  },
  true
);


// recording indicator
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
