# Typing-study website

A chat page where participants talk to an AI assistant while the website
records how they type. It replaces the Chrome extension in `collector/`: the
CSV files it writes have exactly the same columns and meaning, so
`tools/clean_raw.py` and everything in `analysis/` work on them unchanged.

The assistant is a free AI model that runs on your own computer through
**Ollama**, so there is no API key and no cost. The page has two modes:

- **Record a session**: a participant chats for about 15 minutes (2,000 key
  presses) and the session is saved to `data/raw/`.
- **Who's typing?**: someone chats without giving their ID, and the page
  guesses which recorded participant they are from their typing rhythm.

---

## 1. One-time setup

### Python packages

You need Python 3.10 or newer. From the project folder:

```powershell
python -m pip install -r requirements.txt
```

### Ollama (the free local AI model)

1. Download and install Ollama from **https://ollama.com/download**.
   On Windows it then runs in the background (llama icon in the system tray)
   and starts with the computer. On macOS, open the Ollama app once.
2. Open a **new** terminal (so it finds the `ollama` command) and download the
   model, about 2 GB:

   ```powershell
   ollama pull llama3.2
   ```

3. Check that it worked:

   ```powershell
   ollama list
   ```

   `llama3.2:latest` should be in the list.

You only do this once per computer. Without Ollama the website still records
keystrokes; only the assistant's replies are missing.

---

## 2. Run the website

From the project folder:

```powershell
python frontend/server.py
```

It prints:

```
Typing study running at http://127.0.0.1:8000  (Ctrl+C to stop)
Sessions are saved to ...\data\raw
Assistant: llama3.2 via Ollama
```

Open **http://127.0.0.1:8000** in a browser. Keep the terminal open while the
site is in use, and press **Ctrl+C** in it to stop the server.

If it also prints a `WARNING`, see [Troubleshooting](#6-troubleshooting).

---

## 3. Record a session

1. On the welcome screen, stay on the **Record a session** tab.
2. Enter the participant ID (`P04`) and the session (`S1`, `S2` or `S3`),
   tick the consent box and press **Start**.
   - Use the next free session for that participant. If a dialog says the file
     already exists, you have probably picked the wrong session.
3. The participant chats with the assistant. The bar at the top fills up to
   2,000 key presses (about 15 minutes); a green banner then says the session
   is complete.
4. The participant presses **Finish session**. The session is saved to
   `data/raw/P04_S1.csv`.
5. Press **Back to the start** for the next person.

### The ⚙ researcher menu (top right)

The menu is meant for whoever runs the study, not the participant. It shows the
exact keystroke count and lets you:

- **Pause / Resume recording**: nothing is recorded while paused.
- **Save to data/raw**: save now (the page also saves automatically every minute).
- **Download CSV**: the same file through the browser, as a backup.
- **New session…**: clear the page for the next participant.

### After each session

Update the processed data and the results:

```powershell
python analysis/run.py
```

This cleans `data/raw/` into `data/processed/`, then prints the data quality
and how well the detectors tell participants apart. The figures for the report
are in `analysis/results.ipynb` (open it and *Run All*). The new participant is
also added to **Who's typing?** mode automatically.

---

## 4. "Who's typing?" mode

1. On the welcome screen, open the **Who's typing?** tab and press **Start guessing**.
2. Chat as normal. The panel on the right shows **Keep typing…** until there is
   enough to compare (about 60–100 key presses), then the best guess, how much
   closer it is than the runner-up, and every participant ranked by similarity.
   The guess updates about once a second while you type, and gets more reliable
   the more you type (aim for 200+ key presses).
3. Answer **Was it right?** by clicking your real ID (or **Not listed**). The
   answer is added to `data/identify_log.csv`, which gives a live accuracy for
   the report.
4. **Exit guess mode** returns to the welcome screen.

Nothing typed in this mode is saved. Only people who have sessions in
`data/processed/` can be guessed (the tab lists them), and at least two are needed.

How it works: the typing so far is compared with each participant's
200-keystroke windows, using the letter-pair timings and the KNN detector from
`analysis/` (see `analysis/identify.py`). The closest participant is the guess.

---

## 5. Where the data goes

| What | Where |
|---|---|
| Recorded sessions | `data/raw/<participant>_<session>.csv`, saved every minute and when the session is finished |
| An older file that got replaced | `data/raw/backups/` (the page asks first, and never deletes it) |
| Copy in the browser | the page keeps the current session in the browser, so a refresh or crash loses nothing |
| Cleaned sessions | `data/processed/`, written by `python analysis/run.py` |
| Guess-mode answers | `data/identify_log.csv` |

Recording rules (the same as the extension):

| Column | Value |
|---|---|
| `press_id` | +1 for every recorded press; the release carries the same id |
| `sample_id` | +1 each time a message is sent (one message = one sample) |
| `timestamp_ms` | `performance.timeOrigin + performance.now()`, 3 decimals |
| `key_code` / `key` | `event.code` (physical key) / `event.key` (character) |

Only keys pressed in the message box are recorded, auto-repeat of a held key is
ignored, and nothing is recorded while recording is paused. The `key` column
contains every character typed, so the files hold the participants' messages.

---

## 6. Troubleshooting

| Problem | Fix |
|---|---|
| `WARNING: Ollama is not running` | Start the Ollama app (Windows: Start menu → Ollama), or run `ollama serve` in another terminal. No need to restart the server. |
| `WARNING: The model llama3.2 is not downloaded yet` | Run `ollama pull llama3.2`. |
| `'ollama' is not recognized` | Open a new terminal after installing Ollama. |
| The first reply takes 10–30 seconds | Normal: the model is loading into memory. Later replies start within a second or two. |
| The page shows "All done" or an old screen, even after refreshing | The page remembers the last session on purpose. Press **Back to the start**, or ⚙ → **New session…**. |
| The page looks out of date after an update | Press **Ctrl+F5** to reload it fully. |
| `Address already in use` when starting the server | Another server is still running: stop it with Ctrl+C in its terminal, or use `--port 8080`. |
| "P04_S1.csv already exists" when starting a session | That participant already has this session: choose the next session number. |
| Guess mode says the server is not reachable, and the terminal shows `ModuleNotFoundError` | Run `python -m pip install -r requirements.txt`, then restart the server. |

---

## 7. Options

```powershell
python frontend/server.py --port 8080         # another port
python frontend/server.py --host 0.0.0.0      # let other laptops on the same Wi-Fi connect
python frontend/server.py --model gemma3:4b   # another Ollama model (ollama pull gemma3:4b first)
```

- **Other laptops:** with `--host 0.0.0.0`, participants open
  `http://<your-IP>:8000` (your IPv4 address from `ipconfig`). Windows may ask
  to allow Python through the firewall. Sessions are saved on the computer
  running the server.
- **Bigger models** write better replies but answer more slowly on a laptop
  without a graphics card.
- **Claude instead of Ollama** (paid, needs an API key and
  `python -m pip install anthropic`):

  ```powershell
  $env:ANTHROPIC_API_KEY="sk-ant-..."
  python frontend/server.py --llm claude
  ```

The default models and the assistant's instructions (`SYSTEM`) are at the top
of `server.py`.

---

## Files

- `server.py`: serves the page, streams the assistant's replies (Ollama or
  Claude), saves sessions, and answers guess-mode requests.
- `static/index.html`, `static/style.css`: the page.
- `static/app.js`: keystroke recording, chat, saving, guess mode.
- `../analysis/identify.py`: the identification model used by guess mode.
- `../analysis/run.py`, `../analysis/results.ipynb`: results after each session.
- Tests: `tests/test_frontend.py`, `tests/test_identify.py` (run `python -m pytest`).
