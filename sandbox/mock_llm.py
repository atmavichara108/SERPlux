#!/usr/bin/env python3
"""mock_llm.py — детерминированный OpenAI-compatible LLM мок для песочницы.

Реализует POST /v1/chat/completions (форма, которую вызывает labeler._call_provider):
  request:  {model, messages: [{role, content}], temperature}
  response: {choices: [{message: {content}}]}

Политика меток (детерминированная, без LLM):
  - домен в fixture sandbox/fixtures/labels.json ("positives"/"negatives"/"neutral") → соответствующая метка
  - иначе "neutral" по умолчанию.
Содержимое prompt'а парсится минимально: ищем домен в тексте prompt'а.

Запуск:
    python3 sandbox/mock_llm.py [--port 8012] [--fixtures sandbox/fixtures/labels.json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

FIXTURES_PATH = Path(__file__).resolve().parent / "fixtures" / "labels.json"

# Форма меток — как их ожидает labeler (positive/negative/neutral)
VALID_LABELS = ("positive", "negative", "neutral")
DEFAULT_LABEL = "neutral"

_DOMAIN_RE = re.compile(r"https?://([a-zA-Z0-9.-]+)(/|\s|$|[^a-zA-Z0-9.-])")


def load_fixtures(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"mock_llm: не удалось прочитать fixture {path}: {e}", file=sys.stderr)
        raise SystemExit(2)


def classify(prompt: str, fixtures: dict) -> str:
    """Детерминированная классификация по доменам в prompt'е."""
    labels = {
        "positive": set(fixtures.get("positives", [])),
        "negative": set(fixtures.get("negatives", [])),
        "neutral": set(fixtures.get("neutral", [])),
    }
    for match in _DOMAIN_RE.finditer(prompt or ""):
        domain = match.group(1).lower().removeprefix("www.")
        for label, domains in labels.items():
            if domain in domains:
                return label
    return DEFAULT_LABEL


class Handler(BaseHTTPRequestHandler):
    fixtures: dict = {}

    def log_message(self, fmt, *args):
        pass  # тишина: serpctl агрегирует логи сам

    def do_POST(self):  # noqa: N802
        if self.path.rstrip("/") != "/v1/chat/completions":
            body = json.dumps({"error": {"message": f"unknown path {self.path}"}}).encode()
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {}

        messages = payload.get("messages") or []
        prompt = "\n".join(m.get("content", "") for m in messages if isinstance(m, dict))
        label = classify(prompt, self.fixtures)

        response = {
            "id": "chatcmpl-sandbox-mock",
            "object": "chat.completion",
            "model": payload.get("model", "mock"),
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": label},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }
        body = json.dumps(response, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> int:
    parser = argparse.ArgumentParser(description="Mock LLM (OpenAI-compatible, sandbox)")
    parser.add_argument("--port", type=int, default=8012)
    parser.add_argument("--fixtures", default=str(FIXTURES_PATH))
    args = parser.parse_args()

    Handler.fixtures = load_fixtures(Path(args.fixtures))
    server = HTTPServer(("127.0.0.1", args.port), Handler)
    print(f"mock_llm: listening on 127.0.0.1:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
