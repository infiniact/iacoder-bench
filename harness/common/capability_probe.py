#!/usr/bin/env python3
"""Probe what an OpenAI-compatible endpoint can actually do (ADR-0415 D1).

Run it before attributing a failure to a model capability. An unprobed
"text-only" assumption once sent a whole attribution the wrong way.

    python3 eval/common/capability_probe.py \\
        --base-url https://api.example.com/v1 --model some-model \\
        --api-key-env EXAMPLE_API_KEY --out probe.json [--large-context]

Probes: chat completions with tool calling, the Responses API with function
calling, image input (a generated 2x1 PNG, one red and one blue pixel),
streaming, reasoning-content presence, and recall at a few context sizes
(large sizes opt-in). Each probe is independent; one failing never stops the
others.

Secrets: the key is read from the environment variable NAMED by
``--api-key-env`` and never written. The report records that name, the base
URL without userinfo or query, and scrubs any string containing the key.
Standard library only.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from datetime import datetime, timezone

SCHEMA = "iacoder.capability-probe/1"
EXCERPT_CHARS = 300
TOOL_NAME = "record_city"
# The two pixels of the image probe, left to right.
PIXELS = (("red", (255, 0, 0)), ("blue", (0, 0, 255)))
DEFAULT_CONTEXT_TOKENS = (1_000, 8_000, 32_000)
LARGE_CONTEXT_TOKENS = (128_000, 512_000, 1_000_000)
CHARS_PER_TOKEN = 4
PROBES = ("chat_tools", "responses_tools", "image_input", "streaming", "reasoning", "context")


# ---- pure parts (unit-tested) ----

def make_png(pixels):
    """A one-row RGB PNG with the given (r, g, b) pixels."""
    width, height = len(pixels), 1

    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    raw = b"\x00" + b"".join(bytes(p) for p in pixels)  # filter type 0, one row
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def png_data_url(pixels=None):
    data = make_png([rgb for _, rgb in (pixels or PIXELS)])
    return "data:image/png;base64," + base64.b64encode(data).decode("ascii")


def names_colors(answer, names=None):
    """True when every colour name appears as a word in the answer."""
    text = (answer or "").lower()
    return all(re.search(rf"\b{re.escape(n)}\b", text) for n in (names or [n for n, _ in PIXELS]))


def excerpt(text, limit=EXCERPT_CHARS):
    text = text if isinstance(text, str) else json.dumps(text, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + f"...[+{len(text) - limit} chars]"


def scrub(value, secrets):
    """Replace every occurrence of a secret in every string of `value`."""
    secrets = [s for s in secrets if s]
    if isinstance(value, str):
        for s in secrets:
            value = value.replace(s, "[REDACTED]")
        return value
    if isinstance(value, list):
        return [scrub(v, secrets) for v in value]
    if isinstance(value, dict):
        return {k: scrub(v, secrets) for k, v in value.items()}
    return value


def public_url(url):
    """The URL without userinfo, query or fragment."""
    parts = urllib.parse.urlsplit(url)
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    return urllib.parse.urlunsplit((parts.scheme, host, parts.path, "", ""))


def filler_prompt(tokens, marker):
    """About `tokens` tokens of filler with `marker` stated at the start."""
    head = f"Remember this code: {marker}. Below is filler text; ignore it.\n"
    line = "The quick brown fox jumps over the lazy dog. "
    body = line * max(0, (tokens * CHARS_PER_TOKEN - len(head)) // len(line))
    return head + body + f"\nWhat was the code? Reply with the code only."


def message_text(resp):
    try:
        content = resp["choices"][0]["message"].get("content")
    except (KeyError, IndexError, TypeError, AttributeError):
        return ""
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return content or ""


def check_chat_tools(resp):
    """(passed, detail) for a chat completion that should call TOOL_NAME."""
    try:
        calls = resp["choices"][0]["message"].get("tool_calls") or []
    except (KeyError, IndexError, TypeError, AttributeError):
        return False, "no choices[0].message"
    for call in calls:
        fn = (call or {}).get("function") or {}
        if fn.get("name") == TOOL_NAME:
            try:
                args = json.loads(fn.get("arguments") or "")
            except (TypeError, json.JSONDecodeError):
                return False, "tool called with non-JSON arguments"
            return isinstance(args, dict), f"arguments: {excerpt(args)}"
    return False, f"no {TOOL_NAME} call; text: {excerpt(message_text(resp))}"


def check_responses_tools(resp):
    """(passed, detail) for a Responses API reply that should call TOOL_NAME."""
    output = (resp or {}).get("output") if isinstance(resp, dict) else None
    if not isinstance(output, list):
        return False, "no output list"
    for item in output:
        if isinstance(item, dict) and item.get("type") == "function_call" and item.get("name") == TOOL_NAME:
            try:
                json.loads(item.get("arguments") or "")
            except (TypeError, json.JSONDecodeError):
                return False, "function_call with non-JSON arguments"
            return True, f"arguments: {excerpt(item.get('arguments'))}"
    return False, f"no function_call; output types: {[i.get('type') for i in output if isinstance(i, dict)]}"


def parse_sse(lines):
    """Content chunks, whether [DONE] arrived, and any streamed reasoning."""
    chunks, done, reasoning = [], False, False
    for line in lines:
        line = line.strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            done = True
            continue
        try:
            event = json.loads(data)
            delta = event["choices"][0].get("delta") or {}
        except (json.JSONDecodeError, KeyError, IndexError, TypeError, AttributeError):
            continue
        if delta.get("reasoning_content") or delta.get("reasoning"):
            reasoning = True
        if delta.get("content"):
            chunks.append(delta["content"])
    return {"chunks": chunks, "done": done, "reasoning": reasoning}


def check_streaming(parsed):
    ok = len(parsed["chunks"]) >= 2 and parsed["done"]
    return ok, f"{len(parsed['chunks'])} content chunks, done={parsed['done']}"


def has_reasoning(resp):
    try:
        msg = resp["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        return False
    return bool(isinstance(msg, dict) and (msg.get("reasoning_content") or msg.get("reasoning")))


def build_report(base_url, model, key_env, results, large_context):
    """The report envelope. `results` maps probe name to its result dict."""
    statuses = {}
    for name, result in results.items():
        if name == "context":
            statuses[name] = {str(r["tokens"]): r["status"] for r in result}
        else:
            statuses[name] = result["status"]
    return {
        "schema": SCHEMA,
        "utc": datetime.now(timezone.utc).isoformat(),
        "base_url": public_url(base_url),
        "model": model,
        "api_key_env": key_env,
        "large_context": large_context,
        "summary": statuses,
        "probes": results,
    }


# ---- network ----

class Client:
    def __init__(self, base_url, key, timeout):
        self.base = base_url.rstrip("/")
        self.key = key
        self.timeout = timeout

    def _request(self, path, body):
        return urllib.request.Request(
            self.base + path, data=json.dumps(body).encode(), method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.key}"})

    def post(self, path, body):
        """(http_status, parsed JSON or text, latency_ms)."""
        start = time.monotonic()
        try:
            with urllib.request.urlopen(self._request(path, body), timeout=self.timeout) as r:
                status, raw = r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            status, raw = e.code, e.read().decode("utf-8", "replace")
        latency = int((time.monotonic() - start) * 1000)
        try:
            return status, json.loads(raw), latency
        except json.JSONDecodeError:
            return status, raw, latency

    def stream(self, path, body):
        """(http_status, list of SSE lines or error text, latency_ms)."""
        start = time.monotonic()
        try:
            with urllib.request.urlopen(self._request(path, body), timeout=self.timeout) as r:
                lines = [ln.decode("utf-8", "replace") for ln in r]
                status = r.status
        except urllib.error.HTTPError as e:
            status, lines = e.code, e.read().decode("utf-8", "replace")
        return status, lines, int((time.monotonic() - start) * 1000)


def _tool_schema():
    return {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}


def _result(passed, status, latency, detail):
    return {"status": "pass" if passed else "fail", "http_status": status,
            "latency_ms": latency, "detail": excerpt(detail)}


def _http_error(status, body, latency):
    return {"status": "error", "http_status": status, "latency_ms": latency, "detail": excerpt(body)}


def probe_chat_tools(client, model):
    body = {"model": model, "messages": [
        {"role": "user", "content": f"Record the city Paris using the {TOOL_NAME} tool."}],
        "tools": [{"type": "function", "function": {
            "name": TOOL_NAME, "description": "Record a city name.", "parameters": _tool_schema()}}],
        "tool_choice": "required"}
    status, resp, ms = client.post("/chat/completions", body)
    if status != 200 or not isinstance(resp, dict):
        return _http_error(status, resp, ms)
    ok, detail = check_chat_tools(resp)
    return _result(ok, status, ms, detail)


def probe_responses_tools(client, model):
    body = {"model": model, "input": f"Record the city Paris using the {TOOL_NAME} tool.",
            "tools": [{"type": "function", "name": TOOL_NAME, "description": "Record a city name.",
                       "parameters": _tool_schema()}]}
    status, resp, ms = client.post("/responses", body)
    if status != 200 or not isinstance(resp, dict):
        return _http_error(status, resp, ms)
    ok, detail = check_responses_tools(resp)
    return _result(ok, status, ms, detail)


def probe_image_input(client, model):
    body = {"model": model, "messages": [{"role": "user", "content": [
        {"type": "text", "text": "This image has two pixels. Name the colour of each, left to right, "
                                 "in plain colour words."},
        {"type": "image_url", "image_url": {"url": png_data_url()}}]}]}
    status, resp, ms = client.post("/chat/completions", body)
    if status != 200 or not isinstance(resp, dict):
        return _http_error(status, resp, ms)
    answer = message_text(resp)
    return _result(names_colors(answer), status, ms, f"answer: {answer}")


def probe_streaming(client, model):
    body = {"model": model, "stream": True, "messages": [
        {"role": "user", "content": "Count from one to ten in words, separated by spaces."}]}
    status, lines, ms = client.stream("/chat/completions", body)
    if status != 200 or isinstance(lines, str):
        return _http_error(status, lines, ms)
    parsed = parse_sse(lines)
    ok, detail = check_streaming(parsed)
    result = _result(ok, status, ms, detail)
    result["streamed_reasoning"] = parsed["reasoning"]
    return result


def probe_reasoning(client, model):
    body = {"model": model, "messages": [
        {"role": "user", "content": "What is 17 * 23? Think, then answer with the number."}]}
    status, resp, ms = client.post("/chat/completions", body)
    if status != 200 or not isinstance(resp, dict):
        return _http_error(status, resp, ms)
    present = has_reasoning(resp)
    usage = (resp.get("usage") or {}).get("completion_tokens_details") or {}
    result = _result(present, status, ms, f"answer: {message_text(resp)}")
    result["reasoning_tokens"] = usage.get("reasoning_tokens")
    return result


def probe_context(client, model, tokens):
    marker = f"ZX{tokens}Q"
    body = {"model": model, "messages": [{"role": "user", "content": filler_prompt(tokens, marker)}]}
    status, resp, ms = client.post("/chat/completions", body)
    if status != 200 or not isinstance(resp, dict):
        result = _http_error(status, resp, ms)
    else:
        answer = message_text(resp)
        result = _result(marker in answer, status, ms, f"answer: {answer}")
        result["prompt_tokens"] = (resp.get("usage") or {}).get("prompt_tokens")
    result["tokens"] = tokens
    return result


def run_probes(client, model, large_context, only=None):
    selected = only or PROBES
    single = {"chat_tools": probe_chat_tools, "responses_tools": probe_responses_tools,
              "image_input": probe_image_input, "streaming": probe_streaming,
              "reasoning": probe_reasoning}
    results = {}
    for name in selected:
        if name == "context":
            sizes = DEFAULT_CONTEXT_TOKENS + (LARGE_CONTEXT_TOKENS if large_context else ())
            results[name] = [_guard(lambda t=t: probe_context(client, model, t), tokens=t) for t in sizes]
        else:
            results[name] = _guard(lambda f=single[name]: f(client, model))
    return results


def _guard(fn, **extra):
    """One probe's exception becomes its `error` status, never the run's."""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 - every failure is a recorded result
        return {"status": "error", "http_status": None, "latency_ms": None,
                "detail": excerpt(f"{type(exc).__name__}: {exc}"), **extra}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--base-url", required=True, help="OpenAI-compatible base, e.g. https://host/v1")
    ap.add_argument("--model", required=True)
    ap.add_argument("--api-key-env", required=True, help="NAME of the env var holding the key")
    ap.add_argument("--out", required=True, help="report JSON path")
    ap.add_argument("--large-context", action="store_true",
                    help=f"also probe {', '.join(map(str, LARGE_CONTEXT_TOKENS))} tokens (costly)")
    ap.add_argument("--only", nargs="+", choices=PROBES, help="run only these probes")
    ap.add_argument("--timeout", type=float, default=300.0, help="seconds per request")
    args = ap.parse_args(argv)
    key = os.environ.get(args.api_key_env)
    if not key:
        print(f"capability_probe: env var {args.api_key_env} is not set", file=sys.stderr)
        return 2
    results = run_probes(Client(args.base_url, key, args.timeout), args.model,
                         args.large_context, args.only)
    report = scrub(build_report(args.base_url, args.model, args.api_key_env, results,
                                args.large_context), [key])
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    print(json.dumps(report["summary"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
