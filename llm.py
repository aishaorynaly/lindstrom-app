"""Gemini access for the whole app: key, model, error handling and response checks in one place.

Callers get text back or an LLMUnavailable whose message is safe to show the user,
so a missing key or a blocked request never crashes the interface.
"""
from __future__ import annotations

import json
import os
import re
import time

try:
    from google import genai
    from google.genai import errors, types
except ImportError:  # the app still runs, with rule-based text only
    genai = errors = types = None

# GEMINI_MODEL picks a model by hand. Otherwise a model that has been retired is replaced, see _replacement.
MODEL = os.environ.get("GEMINI_MODEL") or "gemini-3.8-flash"
TIMEOUT_MS = 90_000
# Gemini 2.5 counts its internal thinking against this limit, so keep it generous.
MAX_OUTPUT_TOKENS = 8192


class LLMUnavailable(Exception):
    """No usable answer. The message is the reason shown to the user."""


def api_key() -> str | None:
    return os.environ.get("GEMINI_API_KEY") or None


def _reason(name) -> str:
    return getattr(name, "name", str(name))


MAX_TOOL_CALLS = 8
BUSY = (500, 502, 503, 504)  # the service is struggling; the request itself is fine
RETRY_WAITS = (1.5, 4.0)


def rank_models(names: list[str]) -> list[str]:
    """General models, best first: newest stable Flash, the Flash alias, Flash previews, then Flash-Lite."""
    names = [n.removeprefix("models/") for n in names]
    version = lambda n: float(re.match(r"gemini-(\d+(?:\.\d+)?)", n).group(1))
    stable = [n for n in names if re.fullmatch(r"gemini-\d+(?:\.\d+)?-flash", n)]
    previews = [n for n in names if re.match(r"gemini-\d+(?:\.\d+)?-flash-(?!lite)", n)
                and not re.search(r"image|tts|audio|live|embedding", n)]
    lite = [n for n in names if re.fullmatch(r"gemini-\d+(?:\.\d+)?-flash-lite", n)]
    alias = [n for n in names if n == "gemini-flash-latest"]
    return [*sorted(stable, key=version, reverse=True), *alias, *sorted(previews, reverse=True),
            *sorted(lite, key=version, reverse=True)]


def pick_model(names: list[str]) -> str | None:
    ranked = rank_models(names)
    return ranked[0] if ranked else None


def _alternatives(client) -> list[str]:
    """Models this key can use, asked from the API. Empty if the list cannot be read."""
    try:
        return rank_models([m.name for m in client.models.list()
                            if "generateContent" in (m.supported_actions or ["generateContent"])])
    except Exception:  # the original error is the one worth reporting
        return []


def _generate(client, contents: list, config):
    """One request that survives a busy service: wait and retry, then try the other models this key can use.

    A 404 (model retired) or 429 (quota for this model) goes straight to the other models.
    """
    global MODEL
    failure = None
    for wait in (0, *RETRY_WAITS):
        time.sleep(wait)
        try:
            return client.models.generate_content(model=MODEL, contents=contents, config=config)
        except errors.APIError as exc:
            if exc.code not in BUSY and exc.code not in (404, 429):
                raise
            failure = exc
            if exc.code not in BUSY:
                break
    for model in _alternatives(client):
        if model == MODEL:
            continue
        try:
            response = client.models.generate_content(model=model, contents=contents, config=config)
        except errors.APIError as exc:
            if exc.code not in BUSY and exc.code not in (404, 429):
                raise
            continue
        MODEL = model
        return response
    raise failure


def _call(key: str, contents: list, config):
    """The request itself, with every failure turned into an LLMUnavailable that is safe to show."""
    try:
        # Hold the client for the whole call: a temporary one is closed by the garbage collector mid-request.
        with genai.Client(api_key=key) as client:
            response = _generate(client, contents, config)
    except errors.APIError as exc:
        detail = {404: f" (model {MODEL} not found for this key)", 401: " (the key was rejected)", 403: " (the key is not allowed)",
                  429: " (quota or rate limit reached)", **dict.fromkeys(BUSY, " (Gemini is busy right now, please try again)")}.get(exc.code, "")
        raise LLMUnavailable(f"Gemini API error {exc.code}{detail}") from exc
    except Exception as exc:  # transport failures differ by HTTP backend, so match on the name
        name = type(exc).__name__
        if "Timeout" in name:
            raise LLMUnavailable("Gemini request timed out") from exc
        if "Connect" in name or "Network" in name:
            raise LLMUnavailable("Could not reach the Gemini API") from exc
        raise LLMUnavailable(f"Gemini unavailable: {name}") from exc

    feedback = getattr(response, "prompt_feedback", None)
    if feedback is not None and feedback.block_reason:
        raise LLMUnavailable(f"Gemini blocked the request ({_reason(feedback.block_reason)})")
    if not response.candidates:
        raise LLMUnavailable("Gemini returned no answer")
    return response


_SCHEMA_KEYS = ("description", "enum", "required", "minimum", "maximum")


def _schema(node):
    """A plain JSON schema as Gemini's own Schema (upper-case types), the form its function calling is built on."""
    if not node:
        return None
    out = {key: node[key] for key in _SCHEMA_KEYS if key in node}
    if "type" in node:
        out["type"] = str(node["type"]).upper()
    if "properties" in node:
        out["properties"] = {name: _schema(child) for name, child in node["properties"].items()}
    if "items" in node:
        out["items"] = _schema(node["items"])
    return types.Schema.model_validate(out)


def generate_parts(system: str, contents: list[dict], declarations: list[dict] | None = None) -> list[dict]:
    """One Gemini turn where the CALLER runs the tools (the browser, for the portal).

    contents are Gemini contents as JSON-safe dicts (text, function_call and function_response parts). The
    model's reply comes back as the same kind of dicts, so the caller can append it and continue the loop.
    declarations are [{name, description, parameters_json_schema}].
    """
    key = api_key()
    if not key:
        raise LLMUnavailable("No Gemini API key configured")
    if genai is None:
        raise LLMUnavailable("google-genai package not installed")
    try:
        content_objects = [types.Content.model_validate_json(json.dumps(c)) for c in contents]
    except ValueError as exc:
        raise LLMUnavailable("The conversation sent to Gemini was not valid") from exc
    functions = [types.FunctionDeclaration(name=d["name"], description=d.get("description"),
                                           parameters=_schema(d.get("parameters_json_schema")))
                 for d in declarations or []]
    config = types.GenerateContentConfig(
        system_instruction=system,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        http_options=types.HttpOptions(timeout=TIMEOUT_MS, api_version="v1beta"),
        tools=[types.Tool(function_declarations=functions)] if functions else None,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )
    response = _call(key, content_objects, config)
    candidate = response.candidates[0]
    if _reason(candidate.finish_reason) == "MAX_TOKENS":
        raise LLMUnavailable("Gemini answer was cut off at the token limit")
    parts = [part.model_dump(mode="json", exclude_none=True) for part in (candidate.content.parts or [])]
    # Internal reasoning is not for the interface; function calls and answers are.
    parts = [part for part in parts if not part.get("thought")]
    if not parts:
        raise LLMUnavailable(f"Gemini returned no text ({_reason(candidate.finish_reason)})")
    return parts


def generate(system: str, messages: list[dict], schema: dict | None = None, tools: list | None = None) -> str:
    """One Gemini call. messages is [{role: user|assistant, content}], oldest first.

    With a schema, the reply is JSON that follows it. With tools (Python callables), Gemini may call them and
    the SDK runs them, up to MAX_TOOL_CALLS times, before the final text comes back.
    """
    key = api_key()
    if not key:
        raise LLMUnavailable("No Gemini API key configured")
    if genai is None:
        raise LLMUnavailable("google-genai package not installed")

    options = {}
    if schema:
        options = {"response_mime_type": "application/json", "response_json_schema": schema}
    if tools:
        options["tools"] = tools
        options["automatic_function_calling"] = types.AutomaticFunctionCallingConfig(
            maximum_remote_calls=MAX_TOOL_CALLS)
    config = types.GenerateContentConfig(
        system_instruction=system,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        # v1beta is pinned because it has every feature used here (JSON schema output) on the AI Studio key.
        http_options=types.HttpOptions(timeout=TIMEOUT_MS, api_version="v1beta"),
        **options,
    )
    contents = [
        types.Content(role="model" if m["role"] == "assistant" else "user", parts=[types.Part(text=m["content"])])
        for m in messages
    ]

    response = _call(key, contents, config)
    if tools and response.function_calls:
        raise LLMUnavailable("Gemini needed more than %d data queries" % MAX_TOOL_CALLS)

    finish = response.candidates[0].finish_reason
    if _reason(finish) == "MAX_TOKENS":
        raise LLMUnavailable("Gemini answer was cut off at the token limit")
    text = (response.text or "").strip()
    if not text:
        detail = _reason(finish) if finish is not None else "no text"
        raise LLMUnavailable(f"Gemini returned no text ({detail})")
    return text
