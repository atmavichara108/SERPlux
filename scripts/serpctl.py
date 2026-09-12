#!/usr/bin/env python3
"""serpctl.py — детерминированный cockpit SERPlux (порт TUI-кабины, Фаза 2).

Один тул вместо ad-hoc docker/health-команд агента ([[02-Methods/tool-integration-pattern]]):
LLM думает, serpctl делает. Все команды — JSON на stdout; секреты не печатаются.

Команды:
  sandbox up|down|ps|logs      — docker compose --profile sandbox (моки+seed+serplux)
  health                       — GET /health на 127.0.0.1:<port>
  status                       — GET /status с bearer из .env (секрет не печатается)
  test                         — pytest локально (PYTHONPATH venv)
  test-docker                  — pytest в контейнере serplux-sandbox
  db backup|stats              — обёртка backup_db.sh / счётчики таблиц БД
  smoke                        — полный прогон pipeline в песочнице (scripts/smoke.sh)
  nvim <file>                  — открыть файл в tmux-окне pb-SERPlux (ручные правки)
  release <tag>                — подготовить релиз: проверить чистоту, создать тег, push
                                 (RUN GATE: требует подтверждения пользователя)
  deploy-status                — прочитать последний deployment record (docs/deployments.json)

Требует: docker (для sandbox/тестов в контейнере); для health/status — живой сервис.
Запуск: python3 scripts/serpctl.py <cmd> ...
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.request
import urllib.error
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SANDBOX_PORT = int(os.environ.get("SANDBOX_PORT", "8001"))
SANDBOX_BASE = f"http://127.0.0.1:{SANDBOX_PORT}"
COMPOSE_FILES = ["-f", "docker-compose.yml", "-f", "docker-compose.sandbox.yml", "--profile", "sandbox"]
SANDBOX_SVC = "serplux-sandbox"
DEPLOY_RECORDS = REPO / "docs" / "deployments.json"


def _out(payload: dict, exit_code: int = 0) -> int:
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return exit_code


def _run(cmd: list[str], timeout: int = 300) -> dict:
    proc = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True, timeout=timeout)
    return {"exit_code": proc.returncode, "stdout": proc.stdout[-4000:], "stderr": proc.stderr[-2000:]}


def _bearer_from_env() -> str | None:
    """WEBHOOK_SECRET из .env (не печатается никогда)."""
    env_file = REPO / ".env"
    if not env_file.exists():
        return None
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("WEBHOOK_SECRET="):
            value = line.split("=", 1)[1].strip().strip('"').strip("'")
            return value or None
    return None


# ─── sandbox ─────────────────────────────────────────────────────────────────

def cmd_sandbox(args) -> int:
    if args.action == "up":
        result = _run(["docker", "compose", *COMPOSE_FILES, "up", "-d", "--build"], timeout=600)
    elif args.action == "down":
        result = _run(["docker", "compose", *COMPOSE_FILES, "--profile", "sandbox", "down"], timeout=120)
    elif args.action == "ps":
        result = _run(["docker", "compose", *COMPOSE_FILES, "ps"])
    elif args.action == "logs":
        result = _run(["docker", "compose", *COMPOSE_FILES, "logs", "--tail", "50", SANDBOX_SVC])
    else:
        return _out({"error": "unknown_action", "action": args.action}, 2)
    return _out({"action": args.action, **result}, 0 if result["exit_code"] == 0 else 1)


# ─── health / status ─────────────────────────────────────────────────────────

def _http_get_json(url: str, token: str | None = None, timeout: int = 10) -> tuple[int, dict]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(body)
        except json.JSONDecodeError:
            return e.code, {"raw": body[:200]}


def cmd_health(args) -> int:
    try:
        code, body = _http_get_json(f"{SANDBOX_BASE}/health")
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        return _out({"ok": False, "error": f"service unreachable: {e}"}, 1)
    return _out({"ok": code == 200, "status_code": code, "body": body}, 0 if code == 200 else 1)


def cmd_status(args) -> int:
    token = _bearer_from_env()
    try:
        code, body = _http_get_json(f"{SANDBOX_BASE}/status", token=token)
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        return _out({"ok": False, "error": f"service unreachable: {e}"}, 1)
    # token никогда не печатается
    return _out({"ok": code == 200, "status_code": code, "body": body}, 0 if code == 200 else 1)


# ─── tests ───────────────────────────────────────────────────────────────────

def _pytest_env() -> dict:
    env = dict(os.environ)
    venv_sp = REPO / "venv" / "lib" / "python3.14" / "site-packages"
    if venv_sp.is_dir():
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{venv_sp}{os.pathsep}{existing}" if existing else str(venv_sp)
    return env


def cmd_test(args) -> int:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *(args.args or [])],
        cwd=str(REPO), capture_output=True, text=True, timeout=600, env=_pytest_env(),
    )
    tail = "\n".join(proc.stdout.strip().splitlines()[-3:])
    return _out({"exit_code": proc.returncode, "tail": tail}, 0 if proc.returncode == 0 else 1)


def cmd_test_docker(args) -> int:
    result = _run(["docker", "compose", *COMPOSE_FILES, "exec", "-T", SANDBOX_SVC,
                   "python", "-m", "pytest", "-q", "-p", "no:cacheprovider", "--tb=short"],
                  timeout=600)
    return _out({"command": "test-docker", **result}, 0 if result["exit_code"] == 0 else 1)


# ─── db ──────────────────────────────────────────────────────────────────────

def cmd_db(args) -> int:
    if args.action == "backup":
        result = _run(["bash", "backup_db.sh", SANDBOX_SVC], timeout=120)
        return _out({"action": "backup", **result}, 0 if result["exit_code"] == 0 else 1)

    if args.action == "stats":
        db = REPO / "sandbox" / "generated" / "serplux.db"
        if not db.exists():
            return _out({"error": "no_db", "hint": "sandbox seed не прогонялся"}, 1)
        import sqlite3
        conn = sqlite3.connect(str(db), timeout=5)
        try:
            tables = {}
            for t in ("clients", "positions", "labels", "domain_labels"):
                tables[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        finally:
            conn.close()
        return _out({"db": str(db), "tables": tables})

    return _out({"error": "unknown_action", "action": args.action}, 2)


# ─── smoke ───────────────────────────────────────────────────────────────────

def cmd_smoke(args) -> int:
    result = _run(["bash", "scripts/smoke.sh"], timeout=600)
    tail = "\n".join(result["stdout"].strip().splitlines()[-5:])
    return _out({"exit_code": result["exit_code"], "tail": tail}, 0 if result["exit_code"] == 0 else 1)


# ─── nvim (ручная правка в tmux-окне) ────────────────────────────────────────

def cmd_nvim(args) -> int:
    target = Path(args.file).resolve()
    if not target.exists():
        return _out({"error": "no_such_file", "file": str(target)}, 1)
    session = "pb-SERPlux"
    win = "files"
    exists = subprocess.run(["tmux", "has-session", "-t", session],
                            capture_output=True).returncode == 0
    if not exists:
        return _out({"error": "no_tmux_session", "hint": "Pip-Boy workspace-open SERPlux"}, 1)
    subprocess.Popen(["tmux", "send-keys", "-t", f"{session}:{win}",
                      f"nvim {target}", "Enter"])
    return _out({"ok": True, "opened": str(target), "window": f"{session}:{win}"})


# ─── release (Фаза 3) ────────────────────────────────────────────────────────

def cmd_release(args) -> int:
    tag = args.tag
    if not tag.startswith("v"):
        return _out({"error": "tag_must_start_with_v", "tag": tag}, 2)

    dirty = _run(["git", "status", "--porcelain"])
    if dirty["exit_code"] != 0:
        return _out({"error": "git_failed", **dirty}, 2)
    dirty_lines = [ln for ln in dirty["stdout"].splitlines() if ln.strip()]
    if dirty_lines:
        return _out({"error": "dirty_tree", "lines": dirty_lines}, 1)

    tag_exists = _run(["git", "tag", "-l", tag])
    if tag in tag_exists["stdout"].split():
        return _out({"error": "tag_exists", "tag": tag, "hint": "delete tag or pick next version"}, 1)

    if args.dry_run:
        return _out({"ok": True, "dry_run": True, "tag": tag, "steps": [
            "git tag -a <tag> -m 'release <tag>'",
            "git push origin <tag>  → triggers .github/workflows/release.yml",
        ]})

    # RUN GATE: реальный тег+пуш — только при явном --yes (подтверждение пользователя)
    if not args.yes:
        return _out({"error": "confirmation_required",
                     "hint": "release создаёт тег и пушит его → автодеплой в прод. Повтори с --yes"}, 2)

    for cmd in (["git", "tag", "-a", tag, "-m", f"release {tag}"],
                ["git", "push", "origin", tag]):
        result = _run(cmd, timeout=120)
        if result["exit_code"] != 0:
            return _out({"error": "git_failed", "cmd": " ".join(cmd), **result}, 1)
    return _out({"ok": True, "tag": tag, "pushed": True,
                 "next": "workflow release.yml соберёт образ и задеплоит; serpctl deploy-status"})


def cmd_deploy_status(args) -> int:
    if not DEPLOY_RECORDS.exists():
        return _out({"error": "no_records", "path": str(DEPLOY_RECORDS)}, 1)
    try:
        records = json.loads(DEPLOY_RECORDS.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return _out({"error": "bad_records", "message": str(e)}, 2)
    last = records[-1] if isinstance(records, list) and records else records
    return _out({"last": last, "total": len(records) if isinstance(records, list) else None})


BUILD_PARSER = None


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="serpctl — cockpit SERPlux (JSON-вывод)")
    p.add_argument("--sandbox-port", type=int, default=SANDBOX_PORT)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("sandbox", help="docker compose sandbox: up/down/ps/logs")
    s.add_argument("action", choices=["up", "down", "ps", "logs"])
    s.set_defaults(func=cmd_sandbox)

    sub.add_parser("health", help="GET /health песочницы").set_defaults(func=cmd_health)
    sub.add_parser("status", help="GET /status (bearer из .env)").set_defaults(func=cmd_status)

    t = sub.add_parser("test", help="pytest локально (можно передать args через --)")
    t.add_argument("args", nargs="*")
    t.set_defaults(func=cmd_test)

    sub.add_parser("test-docker", help="pytest в контейнере").set_defaults(func=cmd_test_docker)

    d = sub.add_parser("db", help="БД: backup|stats")
    d.add_argument("action", choices=["backup", "stats"])
    d.set_defaults(func=cmd_db)

    sub.add_parser("smoke", help="полный прогон pipeline в песочнице").set_defaults(func=cmd_smoke)

    n = sub.add_parser("nvim", help="открыть файл в tmux-окне pb-SERPlux (ручная правка)")
    n.add_argument("file")
    n.set_defaults(func=cmd_nvim)

    r = sub.add_parser("release", help="создать тег релиза и запушить (гейт: --yes)")
    r.add_argument("tag")
    r.add_argument("--yes", action="store_true", help="подтверждение RUN GATE (пуш тега = автодеплой)")
    r.add_argument("--dry-run", action="store_true")
    r.set_defaults(func=cmd_release)

    sub.add_parser("deploy-status", help="последний deployment record").set_defaults(func=cmd_deploy_status)

    return p


def main() -> int:
    global SANDBOX_PORT, SANDBOX_BASE
    parser = build_parser()
    args = parser.parse_args()
    SANDBOX_PORT = getattr(args, "sandbox_port", SANDBOX_PORT)
    SANDBOX_BASE = f"http://127.0.0.1:{SANDBOX_PORT}"
    try:
        return args.func(args)
    except subprocess.TimeoutExpired as e:
        return _out({"error": "timeout", "cmd": getattr(e, "cmd", None)}, 2)
    except KeyboardInterrupt:
        return _out({"error": "interrupted"}, 130)


if __name__ == "__main__":
    sys.exit(main())
