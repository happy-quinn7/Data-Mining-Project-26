"""Typing-study website: an LLM chat page that records keystrokes in the same
format as the Chrome extension (collector/) and saves them to data/raw/.

Usage:
    python frontend/server.py                     # then open http://127.0.0.1:8000
    python frontend/server.py --port 8080
    python frontend/server.py --host 0.0.0.0      # reachable from other machines on the network
    python frontend/server.py --model gemma3:4b   # another local model
    python frontend/server.py --llm claude        # Claude API instead (paid, needs ANTHROPIC_API_KEY)

By default the assistant is a free model running on this computer through
Ollama (https://ollama.com): install it, then download the model once with
`ollama pull llama3.2`. Recording and saving work even when no model is
available; only the assistant's replies need one.

Endpoints:
    GET  /             the chat page (files in frontend/static/)
    GET  /api/status   model name and the session files already in data/raw/
    POST /api/chat     {"messages": [...]} -> streamed reply, one JSON object per line
    POST /api/save     {"participant_id", "session_id", "events": [...], "overwrite"}
                       -> data/raw/<participant>_<session>.csv
    POST /api/identify {"events": [...]} -> who is typing, among the participants
                       in data/processed/ (nothing is saved)
    POST /api/identify/feedback  {"predicted", "actual", "keys", "ranking"}
                       -> appended to data/identify_log.csv
"""
import argparse
import csv
import io
import json
import re
import sys
import threading
import urllib.error
import urllib.request
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = Path(__file__).resolve().parent / "static"
RAW = ROOT / "data" / "raw"
PROCESSED = ROOT / "data" / "processed"
IDENTIFY_LOG = ROOT / "data" / "identify_log.csv"

COLUMNS = ["participant_id", "session_id", "sample_id", "event",
           "key_code", "timestamp_ms", "press_id", "key"]
PARTICIPANT = re.compile(r"^P\d+$")
SESSION = re.compile(r"^S[123]$")
SESSION_FILE = re.compile(r"^P\d+_S[123]\.csv$")
TIMESTAMP = re.compile(r"^\d+(\.\d+)?$")
MAX_BODY = 50 * 1024 * 1024          # bytes; a long session is about 1 MB of JSON

OLLAMA_URL = "http://127.0.0.1:11434"
OLLAMA_MODEL = "llama3.2"            # 3B model, about 2 GB; runs on a laptop CPU
CLAUDE_MODEL = "claude-opus-5-5"
EFFORT = "low"                       # Claude only: quick, conversational replies

LLM = "ollama"                       # "ollama" or "claude"; set from the command line
MODEL = OLLAMA_MODEL

SYSTEM = (
    "You are a friendly, knowledgeable AI assistant chatting with a university "
    "student. Help with whatever they bring up, the way a general-purpose chat "
    "assistant would. The student is taking part in a typing study, so a long "
    "back-and-forth conversation is the goal: keep replies fairly short (usually "
    "under 150 words), write in plain conversational prose rather than long "
    "lists or headings, and when it fits, end with a question or suggestion that "
    "invites them to write back."
)

_client = None


def client():
    global _client
    if _client is None:
        import anthropic                 # only needed with --llm claude
        _client = anthropic.Anthropic()
    return _client


# --- saving ------------------------------------------------------------------

def check_events(events):
    """Validate the rows sent by the page; return them as CSV rows."""
    if not isinstance(events, list) or not events:
        raise ValueError("no keystrokes to save")
    rows = []
    for i, e in enumerate(events):
        try:
            row = [e[c] for c in COLUMNS]
        except (KeyError, TypeError):
            raise ValueError(f"event {i}: expected the fields {COLUMNS}")
        if e["event"] not in ("down", "up"):
            raise ValueError(f"event {i}: event must be 'down' or 'up'")
        if not TIMESTAMP.match(str(e["timestamp_ms"])):
            raise ValueError(f"event {i}: bad timestamp {e['timestamp_ms']!r}")
        if not all(isinstance(e[c], int) for c in ("sample_id", "press_id")):
            raise ValueError(f"event {i}: sample_id and press_id must be integers")
        rows.append(row)
    return rows


def to_csv(rows):
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")    # quotes "," and '"' keys like the extension
    w.writerow(COLUMNS)
    w.writerows(rows)
    return out.getvalue()


def save_events(participant_id, session_id, events, folder=RAW, overwrite=False):
    """Write the session as <participant>_<session>.csv in the extension's format.

    Saving the same session again (the file on disk is the start of the new
    one) just replaces it. Any other existing file is only replaced when
    overwrite=True, and is first moved to <folder>/backups/ so nothing is lost.
    """
    if not PARTICIPANT.match(str(participant_id)):
        raise ValueError("participant ID must look like P01")
    if not SESSION.match(str(session_id)):
        raise ValueError("session must be S1, S2 or S3")
    rows = check_events(events)
    if any(r[0] != participant_id or r[1] != session_id for r in rows):
        raise ValueError("every event must carry the same participant and session as the file")
    text = to_csv(rows)

    folder = Path(folder)
    path = folder / f"{participant_id}_{session_id}.csv"
    if path.exists():
        with open(path, newline="", encoding="utf-8") as f:
            earlier_save = text.startswith(f.read())
        if not earlier_save:
            if not overwrite:
                raise FileExistsError(path.name)
            backups = folder / "backups"
            backups.mkdir(exist_ok=True)
            path.replace(backups / f"{path.stem}_{datetime.now():%Y%m%d-%H%M%S}.csv")

    folder.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        f.write(text)
    return path


# --- identification ------------------------------------------------------------

_identifier, _identifier_stamp = None, None
_identifier_lock = threading.Lock()


def enrolled_files(folder=PROCESSED):
    return sorted(p for p in Path(folder).glob("*.csv") if SESSION_FILE.match(p.name))


def use_analysis_code():
    """Make analysis/ importable (identification reuses its loading and detectors)."""
    path = str(ROOT / "analysis")
    if path not in sys.path:
        sys.path.insert(0, path)


def identifier(folder=PROCESSED):
    """The Identifier enrolled on data/processed, refitted when those files change."""
    global _identifier, _identifier_stamp
    files = enrolled_files(folder)
    stamp = (str(Path(folder).resolve()), [(p.name, p.stat().st_mtime_ns) for p in files])
    with _identifier_lock:
        if stamp != _identifier_stamp:
            if not files:
                raise ValueError("Nobody is enrolled yet: record sessions, then run analysis/run.py.")
            use_analysis_code()
            from identify import Identifier
            from loading import load_all
            _identifier = Identifier().fit(load_all(folder))
            _identifier_stamp = stamp
        return _identifier


def identify(events, folder=PROCESSED):
    """Rank the enrolled participants by how closely `events` match their typing."""
    rows = check_events(events)
    ident = identifier(folder)
    import numpy as np
    import pandas as pd
    from loading import add_timings, pair_events

    df = pd.DataFrame(rows, columns=COLUMNS).astype({"timestamp_ms": float, "key": str})
    keys = add_timings(pair_events(df.sort_values("timestamp_ms", kind="stable")))
    ranking = ident.rank(keys)
    finite = np.isfinite(ranking.distance)
    return {
        "ready": bool(finite.any()),
        "keys": len(keys),
        "digraphs": int(keys.digraph.count()),
        "enrolled": ident.participants,
        "ranking": [{"participant": p, "distance": round(float(d), 4) if np.isfinite(d) else None}
                    for p, d in zip(ranking.participant, ranking.distance)],
    }


def log_identification(feedback, path=IDENTIFY_LOG):
    """Append one "was the guess right?" answer to data/identify_log.csv."""
    predicted, actual = str(feedback.get("predicted", "")), str(feedback.get("actual", ""))
    if not PARTICIPANT.match(predicted) or not (PARTICIPANT.match(actual) or actual == "unknown"):
        raise ValueError("predicted must look like P01; actual like P01 or 'unknown'")
    keys = feedback.get("keys")
    if not isinstance(keys, int):
        raise ValueError("keys must be an integer")
    new = not Path(path).exists()
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        if new:
            w.writerow(["time", "keystrokes", "predicted", "actual", "correct", "ranking"])
        w.writerow([datetime.now().isoformat(timespec="seconds"), keys, predicted, actual,
                    predicted == actual, json.dumps(feedback.get("ranking", []))])


# --- chat --------------------------------------------------------------------

def check_messages(messages):
    if not isinstance(messages, list) or not messages:
        raise ValueError("no messages")
    for m in messages:
        if not isinstance(m, dict) or m.get("role") not in ("user", "assistant") \
                or not isinstance(m.get("content"), str) or not m["content"].strip():
            raise ValueError("each message needs a role (user/assistant) and some text")
    if messages[0]["role"] != "user" or messages[-1]["role"] != "user":
        raise ValueError("the conversation must start and end with a user message")
    return [{"role": m["role"], "content": m["content"]} for m in messages]


def stream_reply(messages):
    """Yield the reply as {"text": ...} pieces, then {"done": stop_reason}.

    Errors are yielded as {"error": ...} so the page can show them in the chat.
    """
    return stream_ollama(messages) if LLM == "ollama" else stream_claude(messages)


def ollama_problem():
    """None when Ollama is running and has MODEL downloaded, else what to do about it."""
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=3) as r:
            names = {m["name"] for m in json.load(r).get("models", [])}
    except (urllib.error.URLError, OSError, ValueError):
        return "Ollama is not running. Start the Ollama app (or run `ollama serve`)."
    if MODEL not in names and f"{MODEL}:latest" not in names:
        return f"The model {MODEL} is not downloaded yet. Run: ollama pull {MODEL}"
    return None


def stream_ollama(messages):
    body = json.dumps({
        "model": MODEL,
        "messages": [{"role": "system", "content": SYSTEM}] + messages,
        "stream": True,
        "keep_alive": "30m",                  # stay loaded between messages of a session
        "options": {"num_ctx": 8192},         # room for a long conversation
    }).encode("utf-8")
    req = urllib.request.Request(f"{OLLAMA_URL}/api/chat", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            for line in r:                    # one JSON object per line
                if not line.strip():
                    continue
                chunk = json.loads(line)
                if "error" in chunk:
                    yield {"error": f"Ollama: {chunk['error']}"}
                    return
                text = chunk.get("message", {}).get("content", "")
                if text:
                    yield {"text": text}
                if chunk.get("done"):
                    yield {"done": chunk.get("done_reason", "stop")}
                    return
    except urllib.error.HTTPError as e:
        yield {"error": ollama_problem() or f"Ollama error {e.code}: {e.read().decode(errors='replace')}"}
        return
    except (urllib.error.URLError, OSError):
        yield {"error": ollama_problem() or "Lost the connection to Ollama."}
        return
    yield {"error": "Ollama stopped before finishing the reply."}


def stream_claude(messages):
    try:
        import anthropic
    except ImportError:
        yield {"error": "The anthropic package is missing: python -m pip install anthropic"}
        return
    try:
        with client().beta.messages.stream(
            model=MODEL,
            max_tokens=16000,
            system=SYSTEM,
            messages=messages,
            output_config={"effort": EFFORT},
            cache_control={"type": "ephemeral"},     # the history is re-sent every turn
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",                     # a declined reply is retried on another model
        ) as stream:
            for text in stream.text_stream:
                yield {"text": text}
            final = stream.get_final_message()
    except TypeError as e:                           # raised when no credentials are set
        yield {"error": f"No API credentials: set ANTHROPIC_API_KEY and restart the server. ({e})"}
        return
    except anthropic.AuthenticationError:
        yield {"error": "The API key was rejected. Check ANTHROPIC_API_KEY and restart the server."}
        return
    except anthropic.RateLimitError:
        yield {"error": "Rate limited by the API. Wait a few seconds and send again."}
        return
    except anthropic.APIStatusError as e:
        yield {"error": f"API error {e.status_code}: {e.message}"}
        return
    except anthropic.APIConnectionError:
        yield {"error": "Could not reach the API. Check the internet connection."}
        return

    if final.stop_reason == "refusal":
        yield {"error": "The assistant declined to answer that. Try rephrasing or another topic."}
        return
    yield {"done": final.stop_reason}


# --- HTTP --------------------------------------------------------------------

class Handler(SimpleHTTPRequestHandler):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC), **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):
        if self.path == "/api/status":
            existing = sorted(p.name for p in RAW.glob("*.csv") if SESSION_FILE.match(p.name))
            return self.send_json(200, {
                "model": f"{MODEL} (local, Ollama)" if LLM == "ollama" else MODEL,
                "problem": ollama_problem() if LLM == "ollama" else None,
                "existing": existing,
                "enrolled": sorted({p.name.split("_")[0] for p in enrolled_files()}),
            })
        super().do_GET()

    def do_POST(self):
        try:
            body = self.read_json()
        except ValueError as e:
            return self.send_json(400, {"error": str(e)})
        if self.path == "/api/save":
            self.save(body)
        elif self.path == "/api/chat":
            self.chat(body)
        elif self.path == "/api/identify":
            try:
                self.send_json(200, identify(body.get("events")))
            except ValueError as e:
                self.send_json(400, {"error": str(e)})
        elif self.path == "/api/identify/feedback":
            try:
                log_identification(body)
            except ValueError as e:
                return self.send_json(400, {"error": str(e)})
            self.send_json(200, {"logged": str(IDENTIFY_LOG.relative_to(ROOT)).replace("\\", "/")})
        else:
            self.send_json(404, {"error": "unknown endpoint"})

    def save(self, body):
        try:
            path = save_events(body.get("participant_id"), body.get("session_id"),
                               body.get("events"), overwrite=bool(body.get("overwrite")))
        except FileExistsError as e:
            return self.send_json(409, {"error": f"{e} already exists in data/raw", "exists": True})
        except ValueError as e:
            return self.send_json(400, {"error": str(e)})
        self.send_json(200, {"saved": str(path.relative_to(ROOT)).replace("\\", "/"),
                             "rows": len(body["events"])})

    def chat(self, body):
        try:
            messages = check_messages(body.get("messages"))
        except ValueError as e:
            return self.send_json(400, {"error": str(e)})
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.end_headers()
        try:
            for piece in stream_reply(messages):
                self.wfile.write((json.dumps(piece) + "\n").encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass                                     # the page was closed mid-reply

    def read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not 0 < length <= MAX_BODY:
            raise ValueError("request body missing or too large")
        try:
            body = json.loads(self.rfile.read(length))
        except json.JSONDecodeError:
            raise ValueError("request body is not valid JSON")
        if not isinstance(body, dict):
            raise ValueError("request body must be a JSON object")
        return body

    def send_json(self, status, obj):
        data = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        if "/api/" in self.path:                     # skip the noise of static files
            super().log_message(fmt, *args)


def main():
    global LLM, MODEL
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--llm", choices=["ollama", "claude"], default="ollama",
                    help="local model through Ollama (free) or the Claude API (paid)")
    ap.add_argument("--model", help=f"default: {OLLAMA_MODEL} for ollama, {CLAUDE_MODEL} for claude")
    args = ap.parse_args()
    LLM = args.llm
    MODEL = args.model or (OLLAMA_MODEL if LLM == "ollama" else CLAUDE_MODEL)

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Typing study running at http://{args.host}:{args.port}  (Ctrl+C to stop)")
    print(f"Sessions are saved to {RAW}")
    print(f"Assistant: {MODEL} via {'Ollama' if LLM == 'ollama' else 'the Claude API'}")
    if LLM == "ollama" and (problem := ollama_problem()):
        print(f"WARNING: {problem}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
