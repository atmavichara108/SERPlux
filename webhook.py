"""
webhook.py — FastAPI endpoint для запуска пайплайна из Google Sheets.

Запуск:
    uvicorn webhook:app --host 0.0.0.0 --port 8000

Переменные окружения:
    WEBHOOK_SECRET — токен авторизации (обязателен)
    WEBHOOK_HOST   — хост для uvicorn (по умолчанию 0.0.0.0)
    WEBHOOK_PORT   — порт для uvicorn (по умолчанию 8000)
"""

import logging
import os
import threading
from datetime import datetime, timezone

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator

import config
import storage

load_dotenv()

LABEL_MODES = {"domains", "snippets", "full"}

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

app = FastAPI(title="SERPlux Webhook", version="1.0.0")

# Глобальный флаг: не запускаем два прогона одновременно
_run_lock = threading.Lock()
_last_run: dict = {"started_at": None, "status": "idle", "message": ""}


class RunRequest(BaseModel):
    """Тело запроса от Google Apps Script."""
    regions_map: str = "regions_map.json"  # имя файла карты регионов
    with_labels: bool = True
    depth: int = 10
    client_id: str = "default"
    label_mode: str = "domains"
    force_relabel: bool = False

    @field_validator("label_mode")
    @classmethod
    def _validate_label_mode(cls, v: str) -> str:
        if v not in LABEL_MODES:
            allowed = ", ".join(sorted(LABEL_MODES))
            raise ValueError(f"label_mode должен быть одним из: {allowed}; получено '{v}'")
        return v


class ClientCreateRequest(BaseModel):
    """Тело запроса на создание клиента."""
    client_id: str
    client_name: str
    project_id: int | None = None
    sheet_id: str | None = None


class ClientUpdateRequest(BaseModel):
    """Тело запроса на обновление клиента."""
    client_name: str | None = None
    project_id: int | None = None
    sheet_id: str | None = None


def _get_secret() -> str:
    secret = os.environ.get("WEBHOOK_SECRET", "")
    if not secret:
        raise RuntimeError("WEBHOOK_SECRET не задан в окружении")
    return secret


def _verify_token(authorization: str | None) -> None:
    """Проверяет Bearer-токен из заголовка Authorization."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Требуется заголовок Authorization: Bearer <token>",
        )
    token = authorization.removeprefix("Bearer ").strip()
    if token != _get_secret():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Неверный токен",
        )


def _run_pipeline(
    regions_map: str,
    with_labels: bool,
    depth: int,
    client_id: str,
    label_mode: str,
    force_relabel: bool,
) -> None:
    """Запускает полный пайплайн в фоновом потоке."""
    global _last_run
    _last_run["status"] = "running"
    _last_run["message"] = ""
    log.info(
        "Фоновый прогон запущен: regions_map=%s, client_id=%s, label_mode=%s, force_relabel=%s",
        regions_map, client_id, label_mode, force_relabel,
    )

    try:
        # Импортируем здесь, чтобы не тянуть тяжёлые зависимости при старте
        from main import run, DEFAULT_CONFIG

        config = {
            **DEFAULT_CONFIG,
            "with_labels": with_labels,
            "depth": depth,
            "client_id": client_id,
            "label_mode": label_mode,
            "force_relabel": force_relabel,
        }

        # Подменяем regions_map в collector через переменную окружения
        # (collector читает os.environ["REGIONS_MAP"] если задана)
        os.environ["REGIONS_MAP"] = regions_map

        exit_code = run(config)
        if exit_code == 0:
            _last_run["status"] = "ok"
            _last_run["message"] = "Прогон завершён успешно"
            log.info("Прогон завершён успешно")
        else:
            _last_run["status"] = "error"
            _last_run["message"] = "Прогон завершился с ошибкой (exit_code=%d)" % exit_code
            log.error("Прогон завершился с ошибкой: exit_code=%d", exit_code)
    except Exception as e:
        _last_run["status"] = "error"
        _last_run["message"] = str(e)
        log.exception("Необработанное исключение в пайплайне: %s", e)
    finally:
        _run_lock.release()


@app.get("/health")
def health() -> JSONResponse:
    """Health-check для мониторинга контейнера."""
    return JSONResponse({"status": "ok", "service": "serplux-webhook"})


@app.get("/status")
def run_status(authorization: str | None = Header(default=None)) -> JSONResponse:
    """Возвращает статус последнего прогона."""
    _verify_token(authorization)
    return JSONResponse(_last_run)


@app.post("/run", status_code=status.HTTP_202_ACCEPTED)
def trigger_run(
    body: RunRequest,
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    """
    Запускает пайплайн сбора → разметки → выгрузки.

    Возвращает 202 Accepted сразу, прогон идёт в фоне.
    Повторный вызов пока идёт прогон возвращает 409 Conflict.
    """
    _verify_token(authorization)

    acquired = _run_lock.acquire(blocking=False)
    if not acquired:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Прогон уже выполняется, подождите завершения",
        )

    _last_run["started_at"] = datetime.now(timezone.utc).isoformat()
    _last_run["status"] = "starting"
    _last_run["message"] = ""

    thread = threading.Thread(
        target=_run_pipeline,
        args=(
            body.regions_map,
            body.with_labels,
            body.depth,
            body.client_id,
            body.label_mode,
            body.force_relabel,
        ),
        daemon=True,
    )
    thread.start()

    log.info(
        "Прогон принят в очередь: regions_map=%s, client_id=%s, label_mode=%s, force_relabel=%s",
        body.regions_map, body.client_id, body.label_mode, body.force_relabel,
    )
    return JSONResponse(
        {"accepted": True, "started_at": _last_run["started_at"]},
        status_code=status.HTTP_202_ACCEPTED,
    )


@app.get("/clients")
def list_clients(authorization: str | None = Header(default=None)) -> JSONResponse:
    """Возвращает список зарегистрированных клиентов."""
    _verify_token(authorization)
    clients = storage.list_clients(storage.DB_PATH)
    return JSONResponse(clients)


@app.post("/clients", status_code=status.HTTP_201_CREATED)
def create_client(
    body: ClientCreateRequest,
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    """Создаёт нового клиента. Возвращает 409, если client_id уже занят."""
    _verify_token(authorization)
    try:
        storage.create_client(
            client_id=body.client_id,
            client_name=body.client_name,
            project_id=body.project_id,
            sheet_id=body.sheet_id,
            db_path=storage.DB_PATH,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    client = storage.get_client(body.client_id, storage.DB_PATH)
    return JSONResponse(client, status_code=status.HTTP_201_CREATED)


@app.get("/clients/{client_id}")
def get_client(
    client_id: str,
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    """Возвращает профиль конкретного клиента или 404."""
    _verify_token(authorization)
    client = storage.get_client(client_id, storage.DB_PATH)
    if client is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Клиент '{client_id}' не найден",
        )
    return JSONResponse(client)


@app.put("/clients/{client_id}")
def update_client(
    client_id: str,
    body: ClientUpdateRequest,
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    """Обновляет профиль клиента. Возвращает 404, если клиент не найден."""
    _verify_token(authorization)
    try:
        storage.update_client(
            client_id=client_id,
            db_path=storage.DB_PATH,
            client_name=body.client_name,
            project_id=body.project_id,
            sheet_id=body.sheet_id,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    client = storage.get_client(client_id, storage.DB_PATH)
    return JSONResponse(client)


@app.get("/providers")
def list_providers(authorization: str | None = Header(default=None)) -> JSONResponse:
    """Возвращает список зарегистрированных провайдеров LLM (только чтение)."""
    _verify_token(authorization)
    result = []
    for pid, cfg in config.PROVIDERS.items():
        result.append({
            "id": pid,
            "enabled": cfg.get("enabled", False),
            "priority": cfg.get("priority", 999),
            "default_model": cfg.get("default_model", ""),
            "models": cfg.get("models", []),
        })
    return JSONResponse(result)


if __name__ == "__main__":
    import uvicorn

    host = os.environ.get("WEBHOOK_HOST", "0.0.0.0")
    port = int(os.environ.get("WEBHOOK_PORT", "8000"))
    uvicorn.run("webhook:app", host=host, port=port, reload=False)
