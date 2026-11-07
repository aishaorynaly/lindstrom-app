"""The portal server: static allow-list, health, and the Gemini proxy with caller-run tools."""
import base64
import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import server
from server import make_server

TOOLS = [{"function_declarations": [{
    "name": "get_metric", "description": "Get one figure.",
    "parameters_json_schema": {"type": "object", "properties": {"metric": {"type": "string", "enum": ["water_saving"]}},
                               "required": ["metric"]}}]}]
CONTENTS = [{"role": "user", "parts": [{"text": "How much water did we save?"}]}]


@pytest.fixture
def portal():
    server = make_server("127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


def request(url, body=None, method=None, raw=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(url, data=data, method=method or ("POST" if data is not None else "GET"),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return response.status, response.read(), response.headers
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), exc.headers


def post(base, body):
    status, data, _ = request(base + "/api/gemini", body)
    return status, json.loads(data)


def test_serves_the_portal_files(portal):
    for path, marker in (("/", b"KAM portal"), ("/index.html", b"evidence"), ("/kam.js", b"evidenceAreas"),
                         ("/kam_ai.js", b"function_declarations"), ("/kam.css", b".evgrid"), ("/extra_data.js", b"EXTRA")):
        status, body, headers = request(portal + path)
        assert status == 200 and marker in body, path
        assert headers["X-Content-Type-Options"] == "nosniff" and headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("path", ["/reference_original.html", "/server.py", "/../server.py", "/llm.py", "/data.xlsx",
                                  "/portal/../server.py", "/..%2fserver.py", "/.env", "/api/unknown"])
def test_nothing_outside_the_allow_list_is_served(portal, path):
    status, body, _ = request(portal + path)
    assert status == 404 and b"def " not in body and b"import " not in body


def test_health_reports_the_key_without_revealing_it(portal, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert json.loads(request(portal + "/api/health")[1]) == {"ai": False, "model": "gemini-3.8-flash"}
    monkeypatch.setenv("GEMINI_API_KEY", "secret-key-value")
    status, body, _ = request(portal + "/api/health")
    assert json.loads(body)["ai"] is True and b"secret-key-value" not in body


def test_proxy_without_a_key_is_a_clear_503(portal, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    status, body = post(portal, {"system": "s", "contents": CONTENTS})
    assert status == 503 and body == {"error": "No Gemini API key configured"}


def test_proxy_returns_the_models_text(portal, fake_gemini):
    fake_gemini.reply("Water saved is 1.41 M L [Estimated].")
    status, body = post(portal, {"system": "You are a test.", "contents": CONTENTS, "tools": TOOLS})
    assert status == 200 and body["parts"] == [{"text": "Water saved is 1.41 M L [Estimated]."}]
    sent = fake_gemini.last["body"]
    assert fake_gemini.last["path"] == "/v1beta/models/gemini-3.8-flash:generateContent"
    assert fake_gemini.last["headers"]["x-goog-api-key"] == "test-key"
    assert sent["systemInstruction"]["parts"][0]["text"] == "You are a test."
    declared = sent["tools"][0]["functionDeclarations"][0]
    assert declared["name"] == "get_metric" and declared["parameters"]["required"] == ["metric"]
    assert declared["parameters"]["properties"]["metric"] == {"type": "STRING", "enum": ["water_saving"]}


def test_proxy_hands_function_calls_back_to_the_caller(portal, fake_gemini):
    fake_gemini.call_tool("get_metric", {"metric": "water_saving"})
    status, body = post(portal, {"system": "s", "contents": CONTENTS, "tools": TOOLS})
    assert status == 200
    assert body["parts"][0]["function_call"] == {"name": "get_metric", "args": {"metric": "water_saving"}}
    assert len(fake_gemini.requests) == 1  # the proxy did not run the tool itself


def test_proxy_continues_a_tool_conversation(portal, fake_gemini):
    fake_gemini.reply("Done.")
    contents = CONTENTS + [
        {"role": "model", "parts": [{"function_call": {"name": "get_metric", "args": {"metric": "water_saving"}}}]},
        {"role": "user", "parts": [{"function_response": {"name": "get_metric", "response": {"value": "1.41 M L"}}}]}]
    status, body = post(portal, {"system": "s", "contents": contents, "tools": TOOLS})
    assert status == 200 and body["parts"][0]["text"] == "Done."
    assert fake_gemini.roles == ["user", "model", "user"]
    assert fake_gemini.tool_results[0]["name"] == "get_metric" and fake_gemini.tool_results[0]["response"] == {"value": "1.41 M L"}


@pytest.mark.parametrize("body, fragment", [
    ({"contents": []}, "contents"),
    ({"contents": "hello"}, "contents"),
    ({"contents": CONTENTS, "system": 5}, "system"),
    ({"contents": CONTENTS, "tools": "x"}, "tools"),
    ({"contents": CONTENTS, "tools": [{"function_declarations": [{"name": "bad name!"}]}]}, "name"),
    ({"contents": CONTENTS, "tools": [{"function_declarations": [{"name": "t%d" % i} for i in range(25)]}]}, "20"),
    ([1, 2, 3], "object"),
])
def test_malformed_requests_are_rejected(portal, body, fragment):
    status, answer = post(portal, body)
    assert status == 400 and fragment in answer["error"]


def test_invalid_json_and_oversize_bodies(portal):
    assert request(portal + "/api/gemini", raw=b"{not json")[0] == 400
    assert request(portal + "/api/gemini", raw=b"x" * 1_100_000)[0] == 413
    assert request(portal + "/api/other", body={"a": 1})[0] == 404


@pytest.mark.parametrize("failure, status", [(401, 502), (429, 502), (500, 502)])
def test_upstream_errors_become_readable_502s(portal, fake_gemini, failure, status):
    fake_gemini.fail(failure)
    code, body = post(portal, {"system": "s", "contents": CONTENTS})
    assert code == status and body["error"].startswith(f"Gemini API error {failure}")


def test_a_blocked_prompt_is_reported(portal, fake_gemini):
    fake_gemini.body = {"promptFeedback": {"blockReason": "SAFETY"}}
    code, body = post(portal, {"system": "s", "contents": CONTENTS})
    assert code == 502 and "blocked the request (SAFETY)" in body["error"]


def test_other_sites_cannot_use_the_proxy(portal, fake_gemini):
    body = json.dumps({"system": "s", "contents": CONTENTS}).encode()
    # a cross-site "simple" request cannot carry application/json, so it is refused before any parsing
    req = urllib.request.Request(portal + "/api/gemini", data=body, method="POST", headers={"Content-Type": "text/plain"})
    with pytest.raises(urllib.error.HTTPError) as refused:
        urllib.request.urlopen(req, timeout=10)
    assert refused.value.code == 415 and fake_gemini.requests == []
    assert request(portal + "/api/gemini", method="OPTIONS")[0] in (404, 501)  # no CORS preflight is answered


@pytest.mark.parametrize("host", ["evil.example", "evil.example:8765", "127.0.0.1.evil.example"])
def test_a_spoofed_host_header_is_refused(portal, fake_gemini, host):
    for method, path, data in (("GET", "/", None), ("GET", "/api/health", None), ("POST", "/api/gemini", json.dumps({"contents": CONTENTS}).encode())):
        req = urllib.request.Request(portal + path, data=data, method=method, headers={"Host": host, "Content-Type": "application/json"})
        with pytest.raises(urllib.error.HTTPError) as refused:
            urllib.request.urlopen(req, timeout=10)
        assert refused.value.code == 403, (method, path)
    assert fake_gemini.requests == []


# --- Password ---------------------------------------------------------------------------------------------------
def signed_in(url, password):
    token = base64.b64encode(f"kam:{password}".encode()).decode()
    req = urllib.request.Request(url, headers={"Authorization": f"Basic {token}"})
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code


@pytest.fixture
def locked(portal, monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "correct horse")
    monkeypatch.setattr(server, "FAILED_SIGN_IN_DELAY", 0)
    return portal


def test_without_a_password_set_nothing_changes(portal, monkeypatch):
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    assert request(portal + "/")[0] == 200


def test_the_page_and_the_api_need_the_password(locked):
    status, _, headers = request(locked + "/")
    assert status == 401 and "Basic" in headers["WWW-Authenticate"]
    assert request(locked + "/api/gemini", body={"system": "s", "contents": CONTENTS})[0] == 401
    assert request(locked + "/api/ask", body={"question": "x"})[0] == 401


def test_the_right_password_opens_it_and_a_wrong_one_does_not(locked):
    assert signed_in(locked + "/", "correct horse") == 200
    assert signed_in(locked + "/", "wrong") == 401


def test_a_broken_sign_in_header_is_refused(locked):
    req = urllib.request.Request(locked + "/", headers={"Authorization": "Basic not*base64"})
    with pytest.raises(urllib.error.HTTPError) as caught:
        urllib.request.urlopen(req, timeout=20)
    assert caught.value.code == 401


def test_health_stays_open_for_the_host(locked):
    assert request(locked + "/api/health")[0] == 200


def test_a_public_host_needs_a_password(monkeypatch):
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    monkeypatch.setattr(sys, "argv", ["server.py", "--host", "0.0.0.0", "--port", "0"])
    with pytest.raises(SystemExit, match="APP_PASSWORD"):
        server.main()
