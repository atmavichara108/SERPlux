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
from typing import Any

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
_last_run: dict = {
    "started_at": None,
    "finished_at": None,
    "status": "idle",
    "message": "",
    "client_id": None,
}


class RunRequest(BaseModel):
    """Тело запроса от Google Apps Script."""
    regions_map: str = "regions_map.json"  # имя файла карты регионов
    with_labels: bool = True
    depth: int = 10
    client_id: str = "default"
    label_mode: str = "domains"
    force_relabel: bool = False
    report_only: bool = False  # если True — только построить отчёт, без сбора
    report_date: str = "latest"  # дата для отчёта (YYYY-MM-DD или "latest")

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
    searchers: list[str] | None = None
    geos: list[str] | None = None
    regions_map: str | None = None


class ClientUpdateRequest(BaseModel):
    """Тело запроса на обновление клиента."""
    client_name: str | None = None
    project_id: int | None = None
    sheet_id: str | None = None
    searchers: list[str] | None = None
    geos: list[str] | None = None
    regions_map: str | None = None


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


def _build_client_config(
    client_id: str,
    request_params: dict[str, Any],
) -> dict[str, Any]:
    """Собирает runtime-config: DEFAULT_CONFIG → параметры запроса → профиль клиента."""
    from main import DEFAULT_CONFIG

    # Параметры запроса, которые пользователь может переопределить вручную
    runtime_overrides = {
        "with_labels",
        "depth",
        "label_mode",
        "force_relabel",
    }

    config = dict(DEFAULT_CONFIG)
    config["client_id"] = client_id

    # 1. Параметры запроса, не относящиеся к профилю (например, regions_map из старого контракта)
    config.update({k: v for k, v in request_params.items() if k not in runtime_overrides})

    # 2. Профиль клиента из БД перекрывает дефолты и legacy-поля
    client = storage.get_client(client_id, storage.DB_PATH)
    if client:
        log.info("Профиль клиента найден: %s", client_id)
        if client.get("project_id") is not None:
            config["project_id"] = client["project_id"]
        if client.get("sheet_id"):
            config["sheet_id"] = client["sheet_id"]
        if client.get("searchers"):
            config["searchers"] = client["searchers"]
        if client.get("geos"):
            config["geos"] = client["geos"]
        if client.get("regions_map"):
            config["regions_map"] = client["regions_map"]
    else:
        log.warning("Профиль клиента '%s' не найден, используем fallback", client_id)

    # 3. Явные runtime-параметры запроса перекрывают профиль
    config.update({k: v for k, v in request_params.items() if k in runtime_overrides})
    return config


def _run_pipeline(
    regions_map: str,
    with_labels: bool,
    depth: int,
    client_id: str,
    label_mode: str,
    force_relabel: bool,
    report_only: bool = False,
    report_date: str = "latest",
) -> None:
    """Запускает полный пайплайн или только построение отчёта в фоновом потоке."""
    global _last_run
    _last_run["status"] = "running"
    _last_run["message"] = ""
    _last_run["client_id"] = client_id
    log.info(
        "Фоновый прогон запущен: regions_map=%s, client_id=%s, label_mode=%s, force_relabel=%s, report_only=%s",
        regions_map, client_id, label_mode, force_relabel, report_only,
    )

    try:
        if report_only:
            # Только построение отчёта, без сбора/разметки
            from reporter import build_report
            date_arg = None if report_date == "latest" else report_date
            log.info("Построение отчёта за %s (report_only=True)", date_arg or "последнюю доступную")

            client = storage.get_client(client_id, storage.DB_PATH)
            sheet_id = client.get("sheet_id") if client else None

            build_report(date=date_arg, force=True, sheet_id=sheet_id)
            _last_run["status"] = "ok"
            _last_run["message"] = "Отчёт построен успешно"
            log.info("Отчёт построен успешно")
        else:
            # Полный пайплайн: collect → save → label → export → report
            from main import run

            request_params = {
                "with_labels": with_labels,
                "depth": depth,
                "label_mode": label_mode,
                "force_relabel": force_relabel,
            }
            # regions_map из тела запроса пока сохраняем для обратной совместимости,
            # но профиль клиента может его перекрыть
            if regions_map:
                request_params["regions_map"] = regions_map

            config = _build_client_config(client_id, request_params)
            log.info("Конфиг прогона: %s", config)

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
        _last_run["finished_at"] = datetime.now(timezone.utc).isoformat()
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
    Запускает пайплайн сбора → разметки → выгрузки или только построение отчёта.

    Возвращает 202 Accepted сразу, прогон идёт в фоне.
    Повторный вызов пока идёт прогон возвращает 409 Conflict.

    При report_only=true пропускает сбор/разметку и строит только отчёт за report_date.
    """
    _verify_token(authorization)

    acquired = _run_lock.acquire(blocking=False)
    if not acquired:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Прогон уже выполняется, подождите завершения",
        )

    _last_run["started_at"] = datetime.now(timezone.utc).isoformat()
    _last_run["finished_at"] = None  # сбрасываем при старте нового прогона
    _last_run["status"] = "starting"
    _last_run["message"] = ""
    _last_run["client_id"] = body.client_id

    thread = threading.Thread(
        target=_run_pipeline,
        args=(
            body.regions_map,
            body.with_labels,
            body.depth,
            body.client_id,
            body.label_mode,
            body.force_relabel,
            body.report_only,
            body.report_date,
        ),
        daemon=True,
    )
    thread.start()

    log.info(
        "Прогон принят в очередь: regions_map=%s, client_id=%s, label_mode=%s, force_relabel=%s, report_only=%s",
        body.regions_map, body.client_id, body.label_mode, body.force_relabel, body.report_only,
    )
    return JSONResponse(
        {"accepted": True, "started_at": _last_run["started_at"], "client_id": body.client_id},
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
            searchers=body.searchers,
            geos=body.geos,
            regions_map=body.regions_map,
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
        update_fields = {
            "client_name": body.client_name,
            "project_id": body.project_id,
            "sheet_id": body.sheet_id,
            "searchers": body.searchers,
            "geos": body.geos,
            "regions_map": body.regions_map,
        }
        # Убираем None-поля чтобы не затереть существующие значения
        update_fields = {k: v for k, v in update_fields.items() if v is not None}
        storage.update_client(
            client_id=client_id,
            db_path=storage.DB_PATH,
            **update_fields,
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
