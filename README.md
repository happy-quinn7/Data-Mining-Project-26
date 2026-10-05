# Keystroke Dynamics Collector — Chrome extension (Part 1)

INSA Lyon Data-Mining project "Keystroke Dynamics: who is typing?" — the collector (Session 1).
Records your OWN keystroke timings in the chat box of an LLM website, exports to the class CSV format.

## Files
- manifest.json   extension config + which sites to record (Manifest V3)
- content.js      captures keydown/keyup in the chat box only
- indicator.css   the red "REC" badge
- popup.html      toolbar UI
- popup.js        on/off, live count, CSV export, clear

## Load it
1. Open chrome://extensions
2. Turn on Developer mode (top-right)
3. Click "Load unpacked" and select this folder
4. Open an LLM site, open the popup, set your pseudonym (e.g. P07) + session (S1),
   click "Start recording". A red REC badge should appear.

## CSV format (do not change the columns)
participant_id,session_id,sample_id,event,key_code,timestamp_ms,press_id,key
Export auto-names the file P07_S1.csv.

## Self-test (protocol 3.2)
At the start of S1, type .tie5Roanl ten times, Export CSV, then run:
    python verify_csv.py P07_S1.csv
Check every press has a matching release and hold times are positive.

## Privacy (subject 3.1 / 7)
Only your own consented typing. Records only when switched on, only on the sites
listed in manifest.json, with a visible badge. Password fields are never recorded.
Data stays local (chrome.storage.local); no network requests. Clear data when done.
