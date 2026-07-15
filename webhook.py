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
from fastapi import Body, FastAPI, Header, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator

import config
import storage
import topvisor

load_dotenv()

LABEL_MODES = {"auto", "deep"}
DEPTH_VALUES = {10, 20, 50, 100}
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

log = config.setup_logging(__name__)

app = FastAPI(title="SERPlux Webhook", version="1.0.0")

# Глобальный флаг: не запускаем два прогона одновременно
_run_lock = threading.Lock()


class RunRequest(BaseModel):
    """Тело запроса от Google Apps Script."""
    regions_map: str = "regions_map.json"  # имя файла карты регионов
    with_labels: bool = True
    depth: int = 10
    client_id: str = "default"
    label_mode: str = "auto"  # "auto" (дефолт) | "deep"
    force_relabel: bool = False
    report_only: bool = False  # если True — только построить отчёт, без сбора
    report_date: str = "latest"  # дата для отчёта (YYYY-MM-DD или "latest")
    date: str = "today"  # дата сбора (today или YYYY-MM-DD)
    force_rebuild_report: bool = False  # принудительная перестройка отчёта
    provider_chain: str | None = None  # цепочка провайдеров (через запятую)
    model: str | None = None  # конкретная модель LLM (override default_model)
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
    model: str | None = None,
    label_only: bool = False,
) -> None:
    """Запускает полный пайплайн или только построение/разметку/сбор в фоновом потоке."""
    started_at = datetime.now(timezone.utc).isoformat()
    storage.update_run_status(
        {
            "status": "running",
            "client_id": client_id,
            "started_at": started_at,
            "finished_at": None,
            "message": "",
            "stats": None,
        },
        db_path=storage.DB_PATH,
    )
    log.info(
        "Фоновый прогон запущен: client_id=%s, label_mode=%s, force_relabel=%s, "
        "report_only=%s, label_only=%s, date=%s",
        client_id, label_mode, force_relabel, report_only, label_only, date,
    )

    def _set_status(status: str, message: str, stats: dict | None = None) -> None:
        """Атомарно фиксирует статус прогона в БД."""
        storage.update_run_status(
            {
                "status": status,
                "message": message,
                "finished_at": datetime.now(timezone.utc).isoformat() if status in ("ok", "error") else None,
                "stats": stats,
            },
            db_path=storage.DB_PATH,
        )

    try:
        client = storage.get_client(client_id, storage.DB_PATH)
        sheet_id = client.get("sheet_id") if client else None

        if report_only:
            # Только построение отчёта, без сбора/разметки
            from reporter import build_report
            date_arg = None if report_date == "latest" else report_date
            log.info("Построение отчёта за %s (report_only=True)", date_arg or "последнюю доступную")

            build_report(date=date_arg, force=True, sheet_id=sheet_id,
                        client_id=client_id, db_path=storage.DB_PATH)
            _set_status("ok", "Отчёт построен успешно")
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
                model=model,
                db_path=storage.DB_PATH,
            )
            storage.insert_labels(labeled_rows, db_path=storage.DB_PATH)

            build_report(date=target_date, force=force_rebuild_report, sheet_id=sheet_id,
                        client_id=client_id, db_path=storage.DB_PATH)
            _set_status(
                "ok",
                "Разметка завершена успешно",
                stats={"collected": 0, "saved_new": 0, "labeled": len(labeled_rows), "exported": 0},
            )
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
        if model:
            request_params["model"] = model
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
            stats = result.get("stats")
        else:
            exit_code = result
            stats = None

        if exit_code == 0:
            _set_status("ok", "Прогон завершён успешно", stats=stats)
            log.info("Прогон завершён успешно")
        else:
            _set_status("error", "Прогон завершился с ошибкой (exit_code=%d)" % exit_code, stats=stats)
            log.error("Прогон завершился с ошибкой: exit_code=%d", exit_code)
    except Exception as e:
        _set_status("error", str(e))
        log.exception("Необработанное исключение в пайплайне: %s", e)
    finally:
        _run_lock.release()


@app.get("/health")
def health() -> JSONResponse:
    """Health-check для мониторинга контейнера."""
    return JSONResponse({"status": "ok", "service": "serplux-webhook"})


@app.get("/status")
def run_status(authorization: str | None = Header(default=None)) -> JSONResponse:
    """Возвращает статус последнего прогона из БД."""
    _verify_token(authorization)
    return JSONResponse(storage.get_run_status(db_path=storage.DB_PATH))


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

    started_at = datetime.now(timezone.utc).isoformat()
    storage.update_run_status(
        {
            "started_at": started_at,
            "finished_at": None,
            "status": "starting",
            "message": "",
            "client_id": body.client_id,
            "stats": None,
        },
        db_path=storage.DB_PATH,
    )

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
            body.model,
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
        {"accepted": True, "started_at": started_at, "client_id": body.client_id},
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
            "endpoint": cfg.get("endpoint", ""),
            "api_key_env_var": cfg.get("api_key_env_var", ""),
        })
    return JSONResponse(result)


class ProviderRegisterRequest(BaseModel):
    """Тело запроса на регистрацию нового провайдера."""
    provider_id: str
    enabled: bool = True
    priority: int = 999
    default_model: str
    models: list[str]
    endpoint: str
    api_key_env_var: str


@app.post("/providers/register", status_code=status.HTTP_201_CREATED)
def register_provider(
    body: ProviderRegisterRequest,
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    """Регистрирует нового LLM-провайдера в runtime.
    
    Провайдер добавляется в память (config.PROVIDERS) и доступен для разметки.
    При перезапуске контейнера нужно добавить провайдер в config.py или .env.
    Возвращает 409, если provider_id уже существует.
    """
    _verify_token(authorization)
    
    if not body.api_key_env_var.startswith(("OPENCODE_", "OPENAI_", "ANTHROPIC_", "GOOGLE_", "AZURE_")):
        log.warning("register_provider: подозрительное имя переменной %s", body.api_key_env_var)
    
    success = config.register_provider(body.provider_id, {
        "enabled": body.enabled,
        "priority": body.priority,
        "default_model": body.default_model,
        "models": body.models,
        "endpoint": body.endpoint,
        "api_key_env_var": body.api_key_env_var,
    })
    
    if not success:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Провайдер '{body.provider_id}' уже существует или невалидный конфиг",
        )
    
    return JSONResponse({
        "registered": True,
        "provider_id": body.provider_id,
        "message": f"Провайдер '{body.provider_id}' зарегистрирован",
    }, status_code=status.HTTP_201_CREATED)


class ProviderUpdateRequest(BaseModel):
    """Тело запроса на обновление провайдера."""
    enabled: bool | None = None
    priority: int | None = None
    default_model: str | None = None
    models: list[str] | None = None
    endpoint: str | None = None
    api_key_env_var: str | None = None


@app.put("/providers/{provider_id}")
def update_provider(
    provider_id: str,
    body: ProviderUpdateRequest,
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    """Обновляет параметры провайдера в runtime.
    
    Обновляются только переданные поля (None = не менять).
    Возвращает 404, если провайдер не найден.
    """
    _verify_token(authorization)
    
    if provider_id not in config.PROVIDERS:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Провайдер '{provider_id}' не найден",
        )
    
    cfg = config.PROVIDERS[provider_id]
    updates = {}
    if body.enabled is not None:
        updates["enabled"] = body.enabled
    if body.priority is not None:
        updates["priority"] = body.priority
    if body.default_model is not None:
        updates["default_model"] = body.default_model
    if body.models is not None:
        updates["models"] = body.models
    if body.endpoint is not None:
        updates["endpoint"] = body.endpoint
    if body.api_key_env_var is not None:
        updates["api_key_env_var"] = body.api_key_env_var
    
    cfg.update(updates)
    log.info("Провайдер '%s' обновлён: %s", provider_id, list(updates.keys()))
    
    return JSONResponse({
        "updated": True,
        "provider_id": provider_id,
        "provider": {
            "id": provider_id,
            "enabled": cfg.get("enabled", False),
            "priority": cfg.get("priority", 999),
            "default_model": cfg.get("default_model", ""),
            "models": cfg.get("models", []),
            "endpoint": cfg.get("endpoint", ""),
            "api_key_env_var": cfg.get("api_key_env_var", ""),
        },
    })


@app.delete("/providers/{provider_id}")
def delete_provider(
    provider_id: str,
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    """Удаляет провайдера из runtime-конфигурации.
    
    Возвращает 404, если провайдер не найден.
    Нельзя удалить последнего включённого провайдера.
    """
    _verify_token(authorization)
    
    if provider_id not in config.PROVIDERS:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Провайдер '{provider_id}' не найден",
        )
    
    # Нельзя удалить последнего включённого провайдера
    enabled_count = sum(1 for p in config.PROVIDERS.values() if p.get("enabled", False))
    if config.PROVIDERS[provider_id].get("enabled", False) and enabled_count <= 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Нельзя удалить последнего включённого провайдера",
        )
    
    del config.PROVIDERS[provider_id]
    log.info("Провайдер '%s' удалён", provider_id)
    
    return JSONResponse({
        "deleted": True,
        "provider_id": provider_id,
        "message": f"Провайдер '{provider_id}' удалён",
    })


VALID_IMPORT_SENTIMENTS = {"positive", "negative", "neutral"}
VALID_IMPORT_SOURCES = {"manual_l1", "snippet", "page"}
DEFAULT_IMPORT_SOURCE = "manual_l1"
MAX_ERROR_SAMPLES = 5


@app.post("/labels/import")
def import_domain_labels(
    body: dict | list = Body(...),
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    """
    Батчевый импорт разметки в domain_labels.

    Принимает тело в двух формах:
      - массив объектов [{url, query, geo, sentiment, source}, ...]
      - объект {"labels": [...]}

    Для каждой записи вызывает storage.upsert_domain_label, поэтому
    срабатывают правила приоритета source (manual_l1 не перезаписывается
    snippet/page) и идемпотентность по PK (url, query, geo).

    Битая запись не прерывает батч. Возвращает сводку imported/skipped/errors
    и первые ~5 примеров ошибок.
    """
    _verify_token(authorization)

    # Поддержка двух форматов тела
    if isinstance(body, list):
        items = body
        format_name = "array"
    elif isinstance(body, dict) and "labels" in body:
        items = body["labels"]
        format_name = "object"
    else:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="body must be a list of labels or an object {labels: [...]}",
        )

    if not isinstance(items, list):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="labels must be a list",
        )

    log.info("labels_import: received %s items in %s format", len(items), format_name)

    imported = 0
    skipped = 0
    errors = 0
    error_samples: list[str] = []

    def _add_sample(message: str) -> None:
        if len(error_samples) < MAX_ERROR_SAMPLES:
            error_samples.append(message)

    for idx, raw in enumerate(items):
        if not isinstance(raw, dict):
            skipped += 1
            _add_sample(f"row {idx}: not an object")
            log.warning("labels_import: row %s is not an object", idx)
            continue

        # Нормализация полей
        url = _extract_str(raw.get("url"))
        query = _extract_str(raw.get("query"))
        geo = _extract_str(raw.get("geo"))
        sentiment = _extract_str(raw.get("sentiment")).lower()
        source = _extract_str(raw.get("source")).lower() or DEFAULT_IMPORT_SOURCE

        # Валидация
        if not url or not query or not geo or not sentiment:
            skipped += 1
            _add_sample(f"row {idx}: missing required fields")
            log.warning("labels_import: row %s missing required fields", idx)
            continue

        if sentiment not in VALID_IMPORT_SENTIMENTS:
            skipped += 1
            _add_sample(f"row {idx}: invalid sentiment '{sentiment}'")
            log.warning("labels_import: row %s invalid sentiment '%s'", idx, sentiment)
            continue

        if source not in VALID_IMPORT_SOURCES:
            skipped += 1
            _add_sample(f"row {idx}: invalid source '{source}'")
            log.warning("labels_import: row %s invalid source '%s'", idx, source)
            continue

        try:
            storage.upsert_domain_label(
                url=url,
                query=query,
                geo=geo,
                sentiment=sentiment,
                source=source,
                db_path=storage.DB_PATH,
            )
            imported += 1
        except Exception as exc:
            errors += 1
            _add_sample(f"row {idx}: db error for {url}/{query}/{geo}: {exc}")
            log.error(
                "labels_import: db error row %s %s/%s/%s: %s",
                idx, url, query, geo, exc,
            )

    log.info(
        "labels_import: imported=%s skipped=%s errors=%s",
        imported, skipped, errors,
    )

    return JSONResponse({
        "imported": imported,
        "skipped": skipped,
        "errors": errors,
        "error_samples": error_samples,
    })


def _extract_str(value: Any) -> str:
    """Приводит значение к строке и обрезает пробелы; для None/не-строки — пустая строка."""
    if value is None:
        return ""
    return str(value).strip()


if __name__ == "__main__":
    import uvicorn

    host = os.environ.get("WEBHOOK_HOST", "0.0.0.0")
    port = int(os.environ.get("WEBHOOK_PORT", "8000"))
    uvicorn.run("webhook:app", host=host, port=port, reload=False)
