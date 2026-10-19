import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "frontend"), str(ROOT / "analysis"), str(ROOT / "tools")]

import clean_raw
import server
from loading import load_session


def events(text, pid="P01", sid="S1", hold=90, gap=140, start=1_700_000_000_000.0):
    """What the page sends for `text` typed at a steady rhythm."""
    rows, t = [], start
    for i, ch in enumerate(text, start=1):
        code = {" ": "Space", ",": "Comma", '"': "Quote"}.get(ch, f"Key{ch.upper()}")
        for event, ts in (("down", t), ("up", t + hold)):
            rows.append({"participant_id": pid, "session_id": sid, "sample_id": 0,
                         "event": event, "key_code": code, "timestamp_ms": f"{ts:.3f}",
                         "press_id": i, "key": ch})
        t += gap
    return sorted(rows, key=lambda r: float(r["timestamp_ms"]))


def test_saved_file_matches_class_format(tmp_path):
    path = server.save_events("P01", "S1", events("le chat, le \"mur\""), folder=tmp_path)
    assert path.name == "P01_S1.csv"
    df = pd.read_csv(path, keep_default_na=False)
    assert list(df.columns) == clean_raw.COLUMNS
    assert {",", '"'} <= set(df.key)


def test_saved_file_goes_through_cleaning_and_loading(tmp_path):
    path = server.save_events("P01", "S1", events("le chat est sur le mur " * 5), folder=tmp_path)
    df, _ = clean_raw.clean(path)
    assert len(df) == len(pd.read_csv(path))
    k = load_session(path)
    assert np.allclose(k.hold.dropna(), 90)
    assert np.allclose(k.latency.dropna(), 140)


def test_save_refuses_bad_ids(tmp_path):
    for pid, sid in [("Po1", "S1"), ("P01", "S4"), ("../P01", "S1")]:
        with pytest.raises(ValueError):
            server.save_events(pid, sid, events("abc", pid=pid, sid=sid), folder=tmp_path)
    with pytest.raises(ValueError):
        server.save_events("P01", "S1", events("abc", pid="P02"), folder=tmp_path)
    with pytest.raises(ValueError):
        server.save_events("P01", "S1", [], folder=tmp_path)


def test_save_again_replaces_own_file(tmp_path):
    server.save_events("P01", "S1", events("abc"), folder=tmp_path)
    server.save_events("P01", "S1", events("abcdef"), folder=tmp_path)
    assert (pd.read_csv(tmp_path / "P01_S1.csv").event == "down").sum() == 6
    assert not (tmp_path / "backups").exists()


def test_other_recording_is_kept_unless_overwrite(tmp_path):
    server.save_events("P01", "S1", events("abc"), folder=tmp_path)
    with pytest.raises(FileExistsError):
        server.save_events("P01", "S1", events("xyz"), folder=tmp_path)
    server.save_events("P01", "S1", events("xyz"), folder=tmp_path, overwrite=True)
    backups = list((tmp_path / "backups").glob("P01_S1_*.csv"))
    assert len(backups) == 1
    assert set(pd.read_csv(backups[0]).key) == {"a", "b", "c"}


def test_check_messages():
    ok = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"},
          {"role": "user", "content": "how are you?"}]
    assert server.check_messages(ok) == ok
    for bad in [[], [{"role": "assistant", "content": "hi"}], [{"role": "user", "content": " "}],
                [{"role": "system", "content": "x"}], ok[:2]]:
        with pytest.raises(ValueError):
            server.check_messages(bad)


class FakeStream:
    def __init__(self, pieces, stop_reason):
        self.text_stream = iter(pieces)
        self.stop_reason = stop_reason

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return SimpleNamespace(stop_reason=self.stop_reason)


def fake_client(monkeypatch, stream):
    pytest.importorskip("anthropic")
    calls = []

    def make_stream(**kw):
        calls.append(kw)
        if isinstance(stream, Exception):
            raise stream
        return stream

    fake = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(stream=make_stream)))
    monkeypatch.setattr(server, "_client", fake)
    monkeypatch.setattr(server, "LLM", "claude")
    monkeypatch.setattr(server, "MODEL", server.CLAUDE_MODEL)
    return calls


def test_claude_reply(monkeypatch):
    calls = fake_client(monkeypatch, FakeStream(["Hel", "lo"], "end_turn"))
    out = list(server.stream_reply([{"role": "user", "content": "hi"}]))
    assert out == [{"text": "Hel"}, {"text": "lo"}, {"done": "end_turn"}]
    assert calls[0]["model"] == server.CLAUDE_MODEL


def test_claude_reports_problems(monkeypatch):
    fake_client(monkeypatch, FakeStream(["partial"], "refusal"))
    assert "error" in list(server.stream_reply([{"role": "user", "content": "hi"}]))[-1]
    fake_client(monkeypatch, TypeError("Could not resolve authentication method"))
    out = list(server.stream_reply([{"role": "user", "content": "hi"}]))
    assert out == [{"error": out[0]["error"]}] and "ANTHROPIC_API_KEY" in out[0]["error"]


class FakeOllama(BaseHTTPRequestHandler):
    """Answers like Ollama: /api/tags lists models, /api/chat streams JSON lines."""
    models = ["llama3.2:latest"]
    requests = []

    def do_GET(self):
        body = json.dumps({"models": [{"name": m} for m in self.models]}).encode()
        self.send_response(200)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.requests.append(req)
        if req["model"] + ":latest" not in self.models:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(json.dumps({"error": f"model '{req['model']}' not found"}).encode())
            return
        self.send_response(200)
        self.end_headers()
        for piece in ["Hi", " there"]:
            self.wfile.write((json.dumps({"message": {"content": piece}, "done": False}) + "\n").encode())
        self.wfile.write((json.dumps({"message": {"content": ""}, "done": True,
                                      "done_reason": "stop"}) + "\n").encode())

    def log_message(self, *args):
        pass


@pytest.fixture
def ollama(monkeypatch):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), FakeOllama)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    monkeypatch.setattr(server, "OLLAMA_URL", f"http://127.0.0.1:{httpd.server_port}")
    monkeypatch.setattr(server, "LLM", "ollama")
    monkeypatch.setattr(server, "MODEL", "llama3.2")
    FakeOllama.requests.clear()
    yield FakeOllama
    httpd.shutdown()


def test_ollama_reply(ollama):
    assert server.ollama_problem() is None
    out = list(server.stream_reply([{"role": "user", "content": "hello"}]))
    assert out == [{"text": "Hi"}, {"text": " there"}, {"done": "stop"}]
    sent = ollama.requests[0]
    assert sent["model"] == "llama3.2"
    assert sent["messages"][0] == {"role": "system", "content": server.SYSTEM}
    assert sent["messages"][1:] == [{"role": "user", "content": "hello"}]


def test_ollama_model_not_downloaded(ollama, monkeypatch):
    monkeypatch.setattr(server, "MODEL", "gemma3:4b")
    assert "ollama pull gemma3:4b" in server.ollama_problem()
    out = list(server.stream_reply([{"role": "user", "content": "hello"}]))
    assert out == [{"error": "The model gemma3:4b is not downloaded yet. Run: ollama pull gemma3:4b"}]


def test_ollama_not_running(monkeypatch):
    monkeypatch.setattr(server, "OLLAMA_URL", "http://127.0.0.1:9")    # nothing listens there
    monkeypatch.setattr(server, "LLM", "ollama")
    out = list(server.stream_reply([{"role": "user", "content": "hello"}]))
    assert len(out) == 1 and "Ollama is not running" in out[0]["error"]
