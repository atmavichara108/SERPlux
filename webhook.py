"""
webhook.py — FastAPI endpoint для запуска пайплайна из Google Sheets.

Запуск:
    uvicorn webhook:app --host 0.0.0.0 --port 8000

Переменные окружения:
    WEBHOOK_SECRET — токен авторизации (обязателен)
    WEBHOOK_HOST   — хост для uvicorn (по умолчанию 0.0.0.0)
    WEBHOOK_PORT   — порт для uvicorn (по умолчанию 8000)
"""

import os
import re
import threading
from datetime import datetime, timezone
from typing import Any

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator

import config
import storage
import topvisor

load_dotenv()

LABEL_MODES = {"domains", "snippets", "full"}
DEPTH_VALUES = {10, 20, 50, 100}
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

log = config.setup_logging(__name__)

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
    date: str = "today"  # дата сбора (today или YYYY-MM-DD)
    force_rebuild_report: bool = False  # принудительная перестройка отчёта
    provider_chain: str | None = None  # цепочка провайдеров (через запятую)
    label_only: bool = False  # только разметка существующих данных

    @field_validator("label_mode")
    @classmethod
    def _validate_label_mode(cls, v: str) -> str:
        if v not in LABEL_MODES:
            allowed = ", ".join(sorted(LABEL_MODES))
            raise ValueError(f"label_mode должен быть одним из: {allowed}; получено '{v}'")
        return v

    @field_validator("depth")
    @classmethod
    def _validate_depth(cls, v: int) -> int:
        if v not in DEPTH_VALUES:
            allowed = ", ".join(str(d) for d in sorted(DEPTH_VALUES))
            raise ValueError(f"depth должен быть одним из: {allowed}; получено '{v}'")
        return v

    @field_validator("date", "report_date")
    @classmethod
    def _validate_date_field(cls, v: str) -> str:
        allowed_special = {"today", "latest"}
        if v in allowed_special:
            return v
        if not DATE_RE.match(v):
            raise ValueError(f"дата должна быть 'today', 'latest' или 'YYYY-MM-DD'; получено '{v}'")
        return v


class ClientCreateRequest(BaseModel):
    """Тело запроса на создание клиента."""
    client_id: str
    client_name: str
    project_id: int | None = None
    sheet_id: str | None = None
    searchers: list[str] | None = None
    geos: list[str] | None = None
    regions_map: list | str | None = None
    queries: list[dict] | None = None


class ClientUpdateRequest(BaseModel):
    """Тело запроса на обновление клиента."""
    client_name: str | None = None
    project_id: int | None = None
    sheet_id: str | None = None
    searchers: list[str] | None = None
    geos: list[str] | None = None
    regions_map: list | str | None = None
    queries: list[dict] | None = None


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
        if client.get("queries"):
            config["queries"] = client["queries"]
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
    date: str = "today",
    force_rebuild_report: bool = False,
    provider_chain: str | None = None,
    label_only: bool = False,
) -> None:
    """Запускает полный пайплайн или только построение/разметку/сбор в фоновом потоке."""
    global _last_run
    _last_run["status"] = "running"
    _last_run["message"] = ""
    _last_run["client_id"] = client_id
    _last_run["stats"] = None
    log.info(
        "Фоновый прогон запущен: client_id=%s, label_mode=%s, force_relabel=%s, "
        "report_only=%s, label_only=%s, date=%s",
        client_id, label_mode, force_relabel, report_only, label_only, date,
    )

    try:
        client = storage.get_client(client_id, storage.DB_PATH)
        sheet_id = client.get("sheet_id") if client else None

        if report_only:
            # Только построение отчёта, без сбора/разметки
            from reporter import build_report
            date_arg = None if report_date == "latest" else report_date
            log.info("Построение отчёта за %s (report_only=True)", date_arg or "последнюю доступную")

            build_report(date=date_arg, force=True, sheet_id=sheet_id)
            _last_run["status"] = "ok"
            _last_run["message"] = "Отчёт построен успешно"
            log.info("Отчёт построен успешно")
            return

        if label_only:
            # Только разметка существующих данных, без сбора
            from labeler import label
            from reporter import build_report

            target_date = None if date == "today" else date
            filters: dict[str, Any] = {"client_id": client_id}
            if target_date:
                filters["date"] = target_date
            else:
                # Если date=today, берём последнюю доступную дату
                all_rows = storage.get_history(filters={"client_id": client_id}, db_path=storage.DB_PATH)
                if not all_rows:
                    raise ValueError("Нет данных для разметки")
                target_date = all_rows[0]["date"]
                filters["date"] = target_date

            rows = storage.get_history(filters=filters, db_path=storage.DB_PATH)
            if not rows:
                raise ValueError(f"Нет данных за {target_date} для разметки")

            log.info("Разметка %s строк (label_only=True) за %s", len(rows), target_date)
            labeled_rows = label(
                rows,
                label_mode=label_mode,
                force_relabel=force_relabel,
                client_id=client_id,
                provider_chain=provider_chain,
                db_path=storage.DB_PATH,
            )
            storage.insert_labels(labeled_rows, db_path=storage.DB_PATH)

            build_report(date=target_date, force=force_rebuild_report, sheet_id=sheet_id)
            _last_run["status"] = "ok"
            _last_run["message"] = "Разметка завершена успешно"
            _last_run["stats"] = {"collected": 0, "saved_new": 0, "labeled": len(labeled_rows), "exported": 0}
            log.info("Разметка завершена успешно")
            return

        # Полный пайплайн: collect → save → label → export → report
        from main import run

        request_params = {
            "with_labels": with_labels,
            "depth": depth,
            "label_mode": label_mode,
            "force_relabel": force_relabel,
            "force_rebuild_report": force_rebuild_report,
        }
        if date and date != "today":
            request_params["date"] = date
        if provider_chain:
            request_params["provider_chain"] = provider_chain
        # regions_map из тела запроса пока сохраняем для обратной совместимости,
        # но профиль клиента может его перекрыть
        if regions_map:
            request_params["regions_map"] = regions_map

        runtime_config = _build_client_config(client_id, request_params)
        log.info("Конфиг прогона: %s", runtime_config)

        result = run(runtime_config)
        # Поддерживаем старый int и новый dict
        if isinstance(result, dict):
            exit_code = result.get("exit_code", 0)
            _last_run["stats"] = result.get("stats")
        else:
            exit_code = result

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
    Запускает пайплайн сбора → разметки → выгрузки или только построение/разметку отчёта.

    Возвращает 202 Accepted сразу, прогон идёт в фоне.
    Повторный вызов пока идёт прогон возвращает 409 Conflict.

    При report_only=true пропускает сбор/разметку и строит только отчёт за report_date.
    При label_only=true размечает уже собранные данные без повторного сбора.
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
    _last_run["stats"] = None

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
            body.date,
            body.force_rebuild_report,
            body.provider_chain,
            body.label_only,
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
            queries=body.queries,
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
            "queries": body.queries,
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


@app.get("/clients/{client_id}/dates")
def list_client_dates(
    client_id: str,
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    """Возвращает список дат, за которые есть данные для клиента."""
    _verify_token(authorization)
    client = storage.get_client(client_id, storage.DB_PATH)
    if not client:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Клиент '{client_id}' не найден",
        )
    dates = storage.get_dates(client_id=client_id, db_path=storage.DB_PATH)
    return JSONResponse({"dates": dates})


@app.get("/topvisor/regions")
def list_topvisor_regions(
    project_id: int,
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    """
    Возвращает доступные регионы проекта Topvisor.
    Обёртка над topvisor.list_regions(project_id).
    """
    _verify_token(authorization)
    try:
        regions = topvisor.list_regions(project_id=project_id)
    except requests.exceptions.Timeout:
        log.error("Timeout при запросе регионов Topvisor для проекта %s", project_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Topvisor не отвечает (timeout)",
        ) from None
    except requests.exceptions.ConnectionError:
        log.error("Ошибка соединения с Topvisor для проекта %s", project_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Не удалось подключиться к Topvisor",
        ) from None
    except Exception as e:
        log.error("Ошибка при получении регионов Topvisor: %s", e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Ошибка Topvisor: {e}",
        ) from e

    if not regions:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Регионы для проекта {project_id} не найдены",
        )
    return JSONResponse({"project_id": project_id, "regions": regions})


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
