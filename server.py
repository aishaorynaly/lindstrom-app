"""Serve the KAM portal and proxy Gemini calls, so the API key never reaches the browser.

    python server.py                 # http://127.0.0.1:8765, key read from GEMINI_API_KEY
    python server.py --port 9000

The portal itself is the static folder `portal/`. It runs without this server too (open index.html), in
offline-rules mode; the server adds the Gemini assistant.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import threading
from urllib.parse import urlsplit
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import llm

ROOT = Path(__file__).parent / "portal"
STATIC = {"index.html": "text/html; charset=utf-8", "kam.css": "text/css; charset=utf-8",
          "kam.js": "text/javascript; charset=utf-8", "kam_ai.js": "text/javascript; charset=utf-8",
          "extra_data.js": "text/javascript; charset=utf-8", "review.html": "text/html; charset=utf-8",
          "review.css": "text/css; charset=utf-8", "review_ai.js": "text/javascript; charset=utf-8"}
MAX_BODY = 1_000_000
NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_data: dict = {}
_data_lock = threading.Lock()


class BadRequest(Exception):
    pass


def validate(payload) -> tuple[str, list[dict], list[dict]]:
    """Shape checks on what the browser sends. Content correctness is Gemini's business."""
    if not isinstance(payload, dict):
        raise BadRequest("The request must be a JSON object.")
    system, contents, tools = payload.get("system", ""), payload.get("contents"), payload.get("tools") or []
    if not isinstance(system, str) or len(system) > 100_000:
        raise BadRequest("system must be a string of at most 100,000 characters.")
    if not isinstance(contents, list) or not contents or len(contents) > 40 or not all(isinstance(c, dict) for c in contents):
        raise BadRequest("contents must be a list of 1 to 40 objects.")
    declarations: list[dict] = []
    if not isinstance(tools, list):
        raise BadRequest("tools must be a list.")
    for tool in tools:
        for d in (tool.get("function_declarations") or []) if isinstance(tool, dict) else []:
            if not isinstance(d, dict) or not NAME.match(str(d.get("name", ""))):
                raise BadRequest("Every function declaration needs a valid name.")
            declarations.append(d)
    if len(declarations) > 20:
        raise BadRequest("At most 20 function declarations are allowed.")
    return system, contents, declarations


def workbook():
    """The workbook, loaded once on first use. Imported here so the server starts at once."""
    import loader

    with _data_lock:
        if "full" not in _data:
            _data["full"] = loader.load_workbook()
        return _data["full"]


def client_data(customer_id: str):
    import loader

    full = workbook()
    if customer_id not in set(full.customers["customer_id"]):
        raise BadRequest("Unknown customer.")
    with _data_lock:
        return _data.setdefault(customer_id, loader.for_customer(full, customer_id))


def answer_question(payload) -> dict:
    """One question from the Insights panel, answered by Gemini through the data tools on the whole workbook."""
    import config
    import engine
    import narrative
    from engine import Scenario, Scope
    from tools import DataTools

    if not isinstance(payload, dict):
        raise BadRequest("The request must be a JSON object.")
    question, facts, history = payload.get("question"), payload.get("facts", ""), payload.get("history") or []
    if not isinstance(question, str) or not question.strip() or len(question) > 2000:
        raise BadRequest("question must be text of at most 2,000 characters.")
    if not isinstance(facts, str) or len(facts) > 40_000:
        raise BadRequest("facts must be text of at most 40,000 characters.")
    if not isinstance(history, list) or len(history) > 16 or not all(
            isinstance(h, dict) and h.get("role") in ("user", "assistant") and isinstance(h.get("content"), str)
            for h in history):
        raise BadRequest("history is not valid.")
    ds = client_data(str(payload.get("customer_id")))
    months = [payload.get("start") or ds.months[0], payload.get("end") or ds.months[-1]]
    site_ids = tuple(str(s) for s in payload.get("site_ids") or ())
    baseline = str(payload.get("baseline") or config.DEFAULT_BASELINE)
    if not all(m in ds.months for m in months) or baseline not in set(ds.baselines["baseline_id"]):
        raise BadRequest("The period or baseline is not in the data.")
    scope = Scope(str(payload["customer_id"]), site_ids=site_ids, start=min(months), end=max(months))
    scenario = Scenario(baseline)
    tools = DataTools(ds, scope, scenario, config.STANDARD_ROLE)
    ctx = engine.build_context(ds, scope, scenario, config.STANDARD_ROLE)
    result = narrative.chat_answer(ctx, history, question, tools=tools, extra_facts=facts, direct=True)
    canvas = None
    if result.text and payload.get("canvas"):  # tool results first: they are what answered this question
        canvas = narrative.filled_canvas(
            narrative.focus_canvas(question, result.text, f"{narrative.chat_facts(ctx)}\n{facts}\n" + "\n".join(tools.outputs)),
            tools.outputs)
    return {"text": result.text, "source": result.source, "untraced": result.untraced, "queries": result.queries,
            "canvas": canvas}


class Handler(BaseHTTPRequestHandler):
    server_version = "KAMPortal/1.0"

    def _send(self, status: int, body: bytes, content_type: str = "application/json; charset=utf-8") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: dict) -> None:
        self._send(status, json.dumps(payload).encode())

    def _host_allowed(self) -> bool:
        """On a loopback server only loopback names are accepted, which stops DNS-rebinding pages from using it."""
        if not getattr(self.server, "loopback_only", True):
            return True
        host = urlsplit("//" + (self.headers.get("Host") or "")).hostname
        return host in ("127.0.0.1", "localhost", "::1")

    def do_GET(self) -> None:
        if not self._host_allowed():
            return self._json(403, {"error": "Unexpected Host header."})
        path = self.path.split("?", 1)[0]
        if path == "/api/health":
            return self._json(200, {"ai": bool(llm.api_key()), "model": llm.MODEL})
        if path == "/favicon.ico":  # browsers ask for it unprompted; there is none
            return self._send(204, b"", "image/x-icon")
        name = "review.html" if path in ("/", "") else path.lstrip("/")
        if name in STATIC:  # an allow-list, so nothing else on disk can be requested
            return self._send(200, (ROOT / name).read_bytes(), STATIC[name])
        self._json(404, {"error": "Not found"})

    def do_POST(self) -> None:
        if not self._host_allowed():
            return self._json(403, {"error": "Unexpected Host header."})
        path = self.path.split("?", 1)[0]
        if path not in ("/api/gemini", "/api/ask"):
            return self._json(404, {"error": "Not found"})
        # Only application/json: a page on another site cannot send it without a CORS preflight, which is not answered.
        if not (self.headers.get("Content-Type") or "").lower().startswith("application/json"):
            return self._json(415, {"error": "Content-Type must be application/json."})
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            return self._json(400, {"error": "Bad Content-Length."})
        if length > MAX_BODY:
            return self._json(413, {"error": "The request is too large."})
        if path == "/api/ask":
            try:
                return self._json(200, answer_question(json.loads(self.rfile.read(length) or b"null")))
            except (ValueError, BadRequest) as exc:
                return self._json(400, {"error": str(exc) if isinstance(exc, BadRequest) else "The request is not valid JSON."})
        try:
            system, contents, declarations = validate(json.loads(self.rfile.read(length) or b"null"))
        except (ValueError, BadRequest) as exc:
            return self._json(400, {"error": str(exc) if isinstance(exc, BadRequest) else "The request is not valid JSON."})
        try:
            self._json(200, {"parts": llm.generate_parts(system, contents, declarations)})
        except llm.LLMUnavailable as exc:
            self._json(503 if "API key" in str(exc) else 502, {"error": str(exc)})

    def log_message(self, fmt: str, *args) -> None:  # quiet: requests can carry meeting content
        pass


class PortalServer(ThreadingHTTPServer):
    # On Windows address reuse lets a second server share a port, and the older one keeps answering.
    allow_reuse_address = os.name != "nt"


def make_server(host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    server = PortalServer((host, port), Handler)
    server.loopback_only = host in ("127.0.0.1", "localhost", "::1")
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="127.0.0.1", help="use 0.0.0.0 only on a network you trust")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    try:
        server = make_server(args.host, args.port)
    except OSError:
        raise SystemExit(f"Port {args.port} is already in use by another server. Stop that server, or start this one "
                         f"with --port {args.port + 1}.") from None
    print(f"KAM portal: http://{args.host}:{args.port}")
    print(f"Gemini assistant: {'ON (' + llm.MODEL + ')' if llm.api_key() else 'OFF, set GEMINI_API_KEY to enable it'}")
    threading.Thread(target=workbook, daemon=True).start()  # so the first question does not wait for the workbook
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
