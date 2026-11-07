"""A local fake Gemini server, so tests drive the real google-genai SDK without network or a key."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest


class FakeGemini:
    def __init__(self):
        self.requests: list[dict] = []
        self.status = 200
        self.body: dict = {}
        self.json_body: dict | None = None  # sent instead of body when the request asks for JSON output
        self.queue: list[dict] = []  # answered first, one per plain request (for tool-calling conversations)
        self.missing: set[str] = set()  # model names that answer 404
        self.busy: list[str] = []  # model names that answer 503, one request each
        self.always_busy: set[str] = set()  # model names that always answer 503
        self.models: list[str] = []  # what the list-models call returns
        self.reply("OK")

    def reply(self, text: str = "OK", finish: str = "STOP") -> None:
        self.status = 200
        self.body = {"candidates": [{"content": {"role": "model", "parts": [{"text": text}]}, "finishReason": finish}]}

    def call_tool(self, name: str, args: dict) -> None:
        """The next plain request is answered with a function call, as Gemini does when it wants data."""
        self.queue.append({"candidates": [{"content": {"role": "model", "parts": [
            {"functionCall": {"name": name, "args": args}}]}, "finishReason": "STOP"}]})

    @property
    def tool_results(self) -> list[dict]:
        """Every function response the app sent back to Gemini, in the last request."""
        return [p["functionResponse"] for c in self.last["body"]["contents"] for p in c["parts"] if "functionResponse" in p]

    def reply_json(self, payload) -> None:
        """Answer requests that ask for structured JSON output (view planning, redraft)."""
        text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        self.json_body = {"candidates": [{"content": {"role": "model", "parts": [{"text": text}]},
                                          "finishReason": "STOP"}]}

    def fail(self, status: int) -> None:
        self.json_body = None
        self.status = status
        self.body = {"error": {"code": status, "message": "simulated failure", "status": "SIMULATED"}}

    @property
    def last(self) -> dict:
        return self.requests[-1]

    @property
    def system(self) -> str:
        return self.last["body"]["systemInstruction"]["parts"][0]["text"]

    @property
    def roles(self) -> list[str]:
        return [c["role"] for c in self.last["body"]["contents"]]

    @property
    def texts(self) -> list[str]:
        return [c["parts"][0]["text"] for c in self.last["body"]["contents"]]


@pytest.fixture
def fake_gemini(monkeypatch):
    fake = FakeGemini()
    monkeypatch.setattr("llm.RETRY_WAITS", (0, 0))

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status, body):
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self._send(200, {"models": [{"name": f"models/{n}", "supportedGenerationMethods": ["generateContent"]}
                                        for n in fake.models]})

        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get("content-length", 0)))
            fake.requests.append({
                "path": self.path,
                "headers": {k.lower(): v for k, v in self.headers.items()},
                "body": json.loads(raw or b"{}"),
            })
            if any(f"/{name}:" in self.path for name in fake.missing):
                return self._send(404, {"error": {"code": 404, "message": "model not found", "status": "NOT_FOUND"}})
            for name in list(fake.busy):
                if f"/{name}:" in self.path:
                    fake.busy.remove(name)
                    return self._send(503, {"error": {"code": 503, "message": "overloaded", "status": "UNAVAILABLE"}})
            if any(f"/{name}:" in self.path for name in fake.always_busy):
                return self._send(503, {"error": {"code": 503, "message": "overloaded", "status": "UNAVAILABLE"}})
            wants_json = fake.requests[-1]["body"].get("generationConfig", {}).get("responseMimeType") == "application/json"
            if wants_json and fake.json_body is not None:
                body = fake.json_body
            elif fake.queue and not wants_json:
                body = fake.queue.pop(0)
            else:
                body = fake.body
            data = json.dumps(body).encode()
            self.send_response(fake.status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("GOOGLE_GEMINI_BASE_URL", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    yield fake
    server.shutdown()
    server.server_close()
