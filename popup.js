const $ = (id) => document.getElementById(id);

// Load current settings into the form.
chrome.storage.local.get(
  { enabled: false, participantId: "", sessionId: "", events: [] },
  (s) => {
    $("participant").value = s.participantId;
    $("session").value = s.sessionId;
    refreshCount(s.events.length);
    refreshStatus(s.enabled);
  }
);

function refreshCount(n) { $("count").textContent = "Keystrokes: " + n; }
function refreshStatus(on) {
  $("status").textContent = on ? "Recording ON" : "Recording OFF";
  $("status").className = on ? "on" : "off";
  $("toggle").textContent = on ? "Stop recording" : "Start recording";
}

// Save pseudonym / session as you type.
$("participant").addEventListener("change", (e) =>
  chrome.storage.local.set({ participantId: e.target.value.trim() }));
$("session").addEventListener("change", (e) =>
  chrome.storage.local.set({ sessionId: e.target.value.trim() }));

// Toggle recording.
$("toggle").addEventListener("click", () => {
  chrome.storage.local.get({ enabled: false }, (s) => {
    const next = !s.enabled;
    chrome.storage.local.set({ enabled: next }, () => refreshStatus(next));
  });
});

// Live count (storage changes fire while the popup is open).
chrome.storage.onChanged.addListener((changes) => {
  if (changes.events) refreshCount(changes.events.newValue.length);
  if (changes.enabled) refreshStatus(changes.enabled.newValue);
});

// --- Export to CSV in the class format -------------------------------------
$("export").addEventListener("click", () => {
  chrome.storage.local.get({ events: [], participantId: "P00", sessionId: "S1" },
    (s) => {
      const header =
        "participant_id,session_id,sample_id,event,key_code,timestamp_ms,press_id,key";
      const lines = s.events.map((r) =>
        [
          r.participant_id, r.session_id, r.sample_id, r.event,
          r.key_code, r.timestamp_ms, r.press_id, csvField(r.key)
        ].join(",")
      );
      const csv = [header, ...lines].join("\n");
      const blob = new Blob([csv], { type: "text/csv" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = s.participantId + "_" + s.sessionId + ".csv";  // e.g. P07_S1.csv
      a.click();
      URL.revokeObjectURL(url);
    });
});

// The `key` field can be a comma, quote, or newline -> quote it safely (RFC 4180).
function csvField(v) {
  const s = String(v);
  if (/[",\n]/.test(s)) return '"' + s.replace(/"/g, '""') + '"';
  return s;
}

// --- Clear all stored data (the "deletable" requirement) -------------------
$("clear").addEventListener("click", () => {
  if (confirm("Delete all recorded keystrokes?")) {
    chrome.storage.local.set({ events: [] }, () => refreshCount(0));
  }
});
