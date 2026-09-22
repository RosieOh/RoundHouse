"""OpenAI-compatible mock LLM provider with latency and fault injection.

Standard library only, so it runs on a stock python image mounted from a
ConfigMap. Behaviour is driven by env vars, which makes `kubectl set env` the
chaos switch:

  MOCK_PROVIDER        name echoed in replies and the `system_fingerprint`
  MOCK_LATENCY_MS      base time to first byte
  MOCK_JITTER_MS       uniform jitter added to MOCK_LATENCY_MS
  MOCK_OUTPUT_TOKENS   completion length in (whitespace) tokens
  MOCK_TOKEN_DELAY_MS  delay between streamed tokens
  MOCK_ERROR_RATE      0.0-1.0 share of requests answered with MOCK_ERROR_STATUS
  MOCK_ERROR_STATUS    HTTP status of injected failures (500, 429, 503, ...)
  MOCK_API_KEY         when set, requests must send `Authorization: Bearer <key>`
"""

import json
import os
import random
import signal
import sys
import threading
import time
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PROVIDER = os.environ.get("MOCK_PROVIDER", "mock")
LATENCY_MS = int(os.environ.get("MOCK_LATENCY_MS", "200"))
JITTER_MS = int(os.environ.get("MOCK_JITTER_MS", "50"))
OUTPUT_TOKENS = int(os.environ.get("MOCK_OUTPUT_TOKENS", "64"))
TOKEN_DELAY_MS = int(os.environ.get("MOCK_TOKEN_DELAY_MS", "10"))
ERROR_RATE = float(os.environ.get("MOCK_ERROR_RATE", "0"))
ERROR_STATUS = int(os.environ.get("MOCK_ERROR_STATUS", "500"))
API_KEY = os.environ.get("MOCK_API_KEY", "")
PORT = int(os.environ.get("PORT", "8080"))

FILLER = "the quick brown fox jumps over the lazy dog".split()


def count_tokens(text: str) -> int:
    return max(1, len(text.split()))


def prompt_text(messages: list) -> str:
    parts = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            parts.extend(p.get("text", "") for p in content if isinstance(p, dict))
    return " ".join(parts)


def completion_words(prompt: str) -> list:
    head = f"[{PROVIDER}] reply to: {prompt[:60]}".split()
    return (head + FILLER * (OUTPUT_TOKENS // len(FILLER) + 1))[:OUTPUT_TOKENS]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = f"mock-llm/{PROVIDER}"

    def log_message(self, fmt, *args):
        pass

    def _log(self, status: int, started: float) -> None:
        line = {
            "provider": PROVIDER,
            "method": self.command,
            "path": self.path,
            "status": status,
            "ms": round((time.monotonic() - started) * 1000, 1),
        }
        sys.stdout.write(json.dumps(line) + "\n")
        sys.stdout.flush()

    def _send_json(self, status: int, body: dict, headers: dict | None = None) -> None:
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(payload)

    def _send_error(self, status: int, message: str) -> None:
        error_type = "rate_limit_error" if status == 429 else "server_error"
        headers = {"Retry-After": "1"} if status == 429 else None
        self._send_json(status, {"error": {"message": message, "type": error_type, "code": status}}, headers)

    def _authorized(self) -> bool:
        return not API_KEY or self.headers.get("Authorization", "") == f"Bearer {API_KEY}"

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self):
        started = time.monotonic()
        if self.path in ("/healthz", "/readyz"):
            self._send_json(200, {"status": "ok", "provider": PROVIDER})
        elif self.path == "/v1/models":
            self._send_json(200, {"object": "list", "data": [{"id": "mock-gpt", "object": "model", "owned_by": PROVIDER}]})
        else:
            self._send_error(404, f"no route for GET {self.path}")
        self._log(self._status_sent(), started)

    def do_POST(self):
        started = time.monotonic()
        if not self._authorized():
            self._send_error(401, "invalid api key")
        elif self.path == "/v1/chat/completions":
            self._chat(self._read_body())
        elif self.path == "/v1/embeddings":
            self._embeddings(self._read_body())
        else:
            self._send_error(404, f"no route for POST {self.path}")
        self._log(self._status_sent(), started)

    def _status_sent(self) -> int:
        return getattr(self, "_last_status", 200)

    def send_response(self, code, message=None):
        self._last_status = code
        super().send_response(code, message)

    def _simulate_latency(self) -> None:
        time.sleep(max(0, LATENCY_MS + random.uniform(-JITTER_MS, JITTER_MS)) / 1000)

    def _chat(self, body: dict) -> None:
        self._simulate_latency()
        if random.random() < ERROR_RATE:
            self._send_error(ERROR_STATUS, f"injected failure from {PROVIDER}")
            return
        prompt = prompt_text(body.get("messages", []))
        words = completion_words(prompt)
        usage = {
            "prompt_tokens": count_tokens(prompt),
            "completion_tokens": len(words),
            "total_tokens": count_tokens(prompt) + len(words),
        }
        base = {
            "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
            "created": int(time.time()),
            "model": body.get("model", "mock-gpt"),
            "system_fingerprint": f"fp_{PROVIDER}",
        }
        if body.get("stream"):
            include_usage = bool((body.get("stream_options") or {}).get("include_usage"))
            self._stream(base, words, usage if include_usage else None)
            return
        message = {"role": "assistant", "content": " ".join(words)}
        choice = {"index": 0, "message": message, "finish_reason": "stop"}
        self._send_json(200, {**base, "object": "chat.completion", "choices": [choice], "usage": usage})

    def _stream(self, base: dict, words: list, usage: dict | None) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        def event(payload: dict) -> None:
            data = f"data: {json.dumps(payload)}\n\n".encode()
            self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
            self.wfile.flush()

        chunk = {**base, "object": "chat.completion.chunk"}
        event({**chunk, "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}]})
        for position, word in enumerate(words):
            time.sleep(TOKEN_DELAY_MS / 1000)
            text = word if position == 0 else f" {word}"
            event({**chunk, "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}]})
        event({**chunk, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
        if usage:
            event({**chunk, "choices": [], "usage": usage})
        done = b"data: [DONE]\n\n"
        self.wfile.write(f"{len(done):x}\r\n".encode() + done + b"\r\n0\r\n\r\n")
        self.wfile.flush()

    def _embeddings(self, body: dict) -> None:
        self._simulate_latency()
        if random.random() < ERROR_RATE:
            self._send_error(ERROR_STATUS, f"injected failure from {PROVIDER}")
            return
        inputs = body.get("input", "")
        items = inputs if isinstance(inputs, list) else [inputs]
        data = [
            {"object": "embedding", "index": i, "embedding": [random.uniform(-1, 1) for _ in range(8)]}
            for i, _ in enumerate(items)
        ]
        tokens = sum(count_tokens(str(item)) for item in items)
        usage = {"prompt_tokens": tokens, "total_tokens": tokens}
        self._send_json(200, {"object": "list", "data": data, "model": body.get("model", "mock-embed"), "usage": usage})


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.daemon_threads = True

    def stop(*_):
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    sys.stdout.write(json.dumps({"provider": PROVIDER, "port": PORT, "event": "listening"}) + "\n")
    sys.stdout.flush()
    server.serve_forever()


if __name__ == "__main__":
    main()
