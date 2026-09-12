"""
T-00C — тесты collector.py: работа с regions_map из профиля клиента
и проброс depth в вызовы topvisor.
"""

import json
import logging
import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import collector
import topvisor


def test_get_regions_map_uses_list_directly():
    """Если config['regions_map'] — список, использовать его напрямую."""
    regions = [
        {"searcher": "google", "searcher_key": 1, "geo_name": "Литва",
         "region_index": 1300, "region_key": 117, "region_lang": "lt", "region_device": 0},
    ]
    result = collector._get_regions_map({"regions_map": regions})
    assert result == regions


def test_get_regions_map_reads_file_for_string(tmp_path, monkeypatch):
    """Если config['regions_map'] — строка, читать файл."""
    regions = [
        {"searcher": "google", "searcher_key": 1, "geo_name": "Литва",
         "region_index": 1300, "region_key": 117, "region_lang": "lt", "region_device": 0},
    ]
    map_path = tmp_path / "regions_map_test.json"
    map_path.write_text(json.dumps(regions), encoding="utf-8")

    result = collector._get_regions_map({"regions_map": str(map_path)})
    assert result == regions


def test_get_regions_map_fallback_to_env(tmp_path, monkeypatch):
    """Если regions_map не задан — фоллбэк на env REGIONS_MAP."""
    regions = [
        {"searcher": "google", "searcher_key": 1, "geo_name": "Литва",
         "region_index": 1300, "region_key": 117, "region_lang": "lt", "region_device": 0},
    ]
    map_path = tmp_path / "regions_map_env.json"
    map_path.write_text(json.dumps(regions), encoding="utf-8")
    monkeypatch.setenv("REGIONS_MAP", str(map_path))

    result = collector._get_regions_map({})
    assert result == regions


def test_get_regions_map_fallback_to_default_file(tmp_path, monkeypatch):
    """Если regions_map не задан и env нет — фоллбэк на regions_map.json в корне."""
    # Убираем env REGIONS_MAP, если был
    monkeypatch.delenv("REGIONS_MAP", raising=False)
    result = collector._get_regions_map({})
    assert isinstance(result, list)
    assert len(result) > 0
    assert all("searcher" in r and "geo_name" in r for r in result)


# ─── Проброс depth в вызовы topvisor ──────────────────────────────────────────

_REGIONS = [
    {"searcher": "google", "searcher_key": 1, "geo_name": "Литва",
     "region_index": 1300, "region_key": 117, "region_lang": "lt", "region_device": 0},
]


def test_collect_passes_depth_to_run_check(monkeypatch):
    """collect() должен передавать config['depth'] в run_check (запуск проверки)."""
    cfg = {"depth": 50, "searchers": ["google"], "geos": ["Литва"],
           "regions_map": _REGIONS, "project_id": 123, "date": "2026-06-19"}

    captured = {}

    def fake_snapshot_exists(*a, **k):
        return False  # снимков нет → будет запущена проверка

    def fake_run_check(project_id, depth, region_indexes):
        captured["run_check"] = (project_id, depth, region_indexes)
        return [project_id]

    def fake_poll_status(*a, **k):
        return True

    def fake_get_snapshot(*a, **k):
        return []

    monkeypatch.setattr(collector, "snapshot_exists", fake_snapshot_exists)
    monkeypatch.setattr(collector, "run_check", fake_run_check)
    monkeypatch.setattr(collector, "poll_status", fake_poll_status)
    monkeypatch.setattr(collector, "get_snapshot", fake_get_snapshot)

    collector.collect(cfg)
    assert captured["run_check"][0] == 123
    assert captured["run_check"][1] == 50
    assert captured["run_check"][2] == [1300]


def test_collect_passes_depth_to_get_snapshot(monkeypatch):
    """collect() должен передавать config['depth'] в get_snapshot (скачивание снимка)."""
    cfg = {"depth": 50, "searchers": ["google"], "geos": ["Литва"],
           "regions_map": _REGIONS, "project_id": 123, "date": "2026-06-19"}

    captured = {}

    def fake_snapshot_exists(*a, **k):
        return True  # снимок уже есть → run_check не вызывается

    def fake_get_snapshot(project_id, region_index, today, depth, **k):
        captured["get_snapshot"] = (project_id, region_index, today, depth)
        return []

    monkeypatch.setattr(collector, "snapshot_exists", fake_snapshot_exists)
    monkeypatch.setattr(collector, "get_snapshot", fake_get_snapshot)

    collector.collect(cfg)
    assert captured["get_snapshot"][0] == 123
    assert captured["get_snapshot"][1] == 1300
    assert captured["get_snapshot"][3] == 50


# ─── depth НЕ попадает в payload (Topvisor API не поддерживает глубину) ──────


def test_run_check_payload_has_no_depth_field(monkeypatch):
    """checker/go не должен получать выдуманное поле depth/limit в payload."""
    captured = {}

    def fake_post(service, method, payload):
        captured["payload"] = payload
        return {"projectsIds": [123]}

    monkeypatch.setattr(topvisor, "_post", fake_post)

    ids = topvisor.run_check(123, 50, [1300])
    assert ids == [123]
    assert "depth" not in captured["payload"]
    assert "limit" not in captured["payload"]
    assert captured["payload"]["do_snapshots"] is True


def test_get_snapshot_payload_has_no_depth_field(monkeypatch):
    """snapshots_2/history не должен получать выдуманное поле depth в payload."""
    captured = {}

    def fake_post(service, method, payload):
        captured["payload"] = payload
        return {"keywords": []}

    monkeypatch.setattr(topvisor, "_post", fake_post)

    rows = topvisor.get_snapshot(123, 1300, "2026-06-19", 50,
                                 searcher_key=1, region_key=117,
                                 region_lang="lt", region_device=0, geo="Литва")
    assert rows == []
    assert "depth" not in captured["payload"]
    assert "limit" not in captured["payload"]


# ─── WARNING при depth > фактической возможности API ─────────────────────────


def test_run_check_warns_when_depth_gt_10(monkeypatch, caplog):
    """При depth > 10 run_check логирует явный WARNING о том, что API не
    принимает глубину на уровне запроса."""
    monkeypatch.setattr(topvisor, "_post",
                        lambda *a, **k: {"projectsIds": [123]})
    # setup_logging ставит propagate=False — включаем для захвата caplog
    monkeypatch.setattr(topvisor.log, "propagate", True)

    with caplog.at_level(logging.WARNING):
        topvisor.run_check(123, 50, [1300])

    assert any("не поддерживает глубину" in r.message for r in caplog.records)


def test_get_snapshot_warns_when_real_depth_less_than_requested(monkeypatch, caplog):
    """Если фактическая глубина выдачи меньше запрошенного depth — WARNING с evidence."""
    fake_result = {
        "keywords": [{
            "name": "test query",
            "snapshotsData": {
                "site:1": {"url": "http://a.example/1",
                           "snippet_title": "", "snippet_body": ""},
                "site:2": {"url": "http://b.example/2",
                           "snippet_title": "", "snippet_body": ""},
            },
        }],
    }
    monkeypatch.setattr(topvisor, "_post", lambda *a, **k: fake_result)
    monkeypatch.setattr(topvisor.log, "propagate", True)

    with caplog.at_level(logging.WARNING):
        rows = topvisor.get_snapshot(123, 1300, "2026-06-19", 50,
                                     searcher_key=1, region_key=117,
                                     region_lang="lt", region_device=0, geo="Литва")

    assert len(rows) == 2
    assert any("Глубина выдачи меньше запрошенной" in r.message for r in caplog.records)


def test_no_warning_when_depth_equals_real_depth(monkeypatch, caplog):
    """При depth <= фактической глубине выдачи WARNING отсутствует."""
    fake_result = {
        "keywords": [{
            "name": "test query",
            "snapshotsData": {
                "site:1": {"url": "http://a.example/1",
                           "snippet_title": "", "snippet_body": ""},
                "site:2": {"url": "http://b.example/2",
                           "snippet_title": "", "snippet_body": ""},
            },
        }],
    }
    monkeypatch.setattr(topvisor, "_post", lambda *a, **k: fake_result)
    monkeypatch.setattr(topvisor.log, "propagate", True)

    with caplog.at_level(logging.WARNING):
        topvisor.get_snapshot(123, 1300, "2026-06-19", 2,
                              searcher_key=1, region_key=117,
                              region_lang="lt", region_device=0, geo="Литва")

    assert not any("Глубина выдачи меньше запрошенной" in r.message for r in caplog.records)


# ─── Hotfix v1.0.3: финальная попытка скачивания после таймаута poll_status ───


def _timeout_cfg():
    """Конфиг для тестов таймаута: одна связка, фиксированный project_id."""
    return {"depth": 10, "searchers": ["google"], "geos": ["Литва"],
            "regions_map": _REGIONS, "project_id": 123, "date": "2026-06-19",
            "timeout_sec": 5}


def _row(n=1):
    """Минимальная строка результата get_snapshot."""
    return {"date": "2026-06-19", "searcher": "google", "query": f"q{n}",
            "geo": "Литва", "position": n, "url": f"http://a.example/{n}",
            "domain": "a.example", "label": None}


def test_collect_poll_timeout_but_snapshot_has_rows(monkeypatch):
    """poll_status=False, но финальная попытка get_snapshot дала строки —
    collect возвращает rows как обычный успех (без исключения)."""
    monkeypatch.setattr(collector, "snapshot_exists", lambda *a, **k: False)
    monkeypatch.setattr(collector, "run_check", lambda *a, **k: [123])
    monkeypatch.setattr(collector, "poll_status", lambda *a, **k: False)
    monkeypatch.setattr(collector, "get_snapshot", lambda *a, **k: [_row(1), _row(2)])

    rows = collector.collect(_timeout_cfg())

    assert len(rows) == 2
    assert rows[0]["domain"] == "a.example"


def test_collect_poll_timeout_and_zero_rows_raises(monkeypatch):
    """poll_status=False и финальная попытка дала 0 строк — CollectTimeoutError."""
    monkeypatch.setattr(collector, "snapshot_exists", lambda *a, **k: False)
    monkeypatch.setattr(collector, "run_check", lambda *a, **k: [123])
    monkeypatch.setattr(collector, "poll_status", lambda *a, **k: False)
    monkeypatch.setattr(collector, "get_snapshot", lambda *a, **k: [])

    with pytest.raises(collector.CollectTimeoutError):
        collector.collect(_timeout_cfg())


def test_collect_poll_success_keeps_old_behavior(monkeypatch):
    """poll_status=True — старое поведение: rows возвращаются, исключения нет."""
    monkeypatch.setattr(collector, "snapshot_exists", lambda *a, **k: False)
    monkeypatch.setattr(collector, "run_check", lambda *a, **k: [123])
    monkeypatch.setattr(collector, "poll_status", lambda *a, **k: True)
    monkeypatch.setattr(collector, "get_snapshot", lambda *a, **k: [_row(1)])

    rows = collector.collect(_timeout_cfg())
    assert len(rows) == 1


def test_collect_poll_success_zero_rows_returns_empty(monkeypatch):
    """poll_status=True и 0 строк — как раньше, return [] (не CollectTimeoutError)."""
    monkeypatch.setattr(collector, "snapshot_exists", lambda *a, **k: False)
    monkeypatch.setattr(collector, "run_check", lambda *a, **k: [123])
    monkeypatch.setattr(collector, "poll_status", lambda *a, **k: True)
    monkeypatch.setattr(collector, "get_snapshot", lambda *a, **k: [])

    assert collector.collect(_timeout_cfg()) == []





# ─── Hotfix v1.0.4: Google-обёртки /goto?url= и /url?q= в snapshotsData ───────

import base64

# Реальные payload'ы из prod-БД (209 строк за 2026-09-10). Проверено разбором:
# base64url-декод даёт protobuf 08 01 12 <len> 01 EB 3B 30 15 <binary>, где
# заявленная длина поля 2 (98/197/99) больше фактических данных (33) — токен
# подписанный/opaque, URL внутри отсутствует (подтверждено autom.dev и
# openwebninja.com, сентябрь 2026). Декодер обязан возвращать None, не падать
# и не выдумывать URL.
_PROD_GOTO_PAYLOADS = [
    "CAESYgHrOzAVqwYjzJOREhR_MWy9Zhz4soRlvg2oY-o8f6GoAE",
    "CAESxQEB6zswFWYMc62AkWFu2YNiWdlIp6tF7G3F24GKnLlCup",
    "CAESYwHrOzAViFbErvNSvrdT8IzJhVPXJt25c74EszWiXpRQgg",
]


def _proto_with_url(url: bytes) -> bytes:
    """Синтетический protobuf: field1 varint=1, field2 length-delimited с URL."""
    return b"\x08\x01\x12" + bytes([len(url)]) + url


def _goto_wrapper(url: bytes) -> str:
    token = base64.urlsafe_b64encode(_proto_with_url(url)).decode().rstrip("=")
    return "/goto?url=" + token


def test_decode_google_redirect_prod_tokens_return_none():
    """Реальные prod-токены /goto?url=CAES... не содержат URL — декодер
    возвращает None (консервативно), не падает и не выдумывает URL."""
    for payload in _PROD_GOTO_PAYLOADS:
        assert topvisor._decode_google_redirect_url(f"/goto?url={payload}") is None


def test_decode_google_redirect_proto_with_url():
    """Синтетический protobuf с URL в length-delimited поле — URL извлекается."""
    wrapper = _goto_wrapper(b"https://example.com/page")
    assert topvisor._decode_google_redirect_url(wrapper) == "https://example.com/page"


def test_decode_google_redirect_proto_nested_url():
    """URL во вложенном length-delimited поле (внутри field 2) — извлекается."""
    inner = b"\x12" + bytes([len(b"https://nested.example.org/")]) + b"https://nested.example.org/"
    wrapper = _goto_wrapper(inner)
    assert topvisor._decode_google_redirect_url(wrapper) == "https://nested.example.org/"


def test_decode_google_redirect_url_q_open_url():
    """Старый формат /url?q= с открытым http(s) URL — URL возвращается как есть."""
    assert topvisor._decode_google_redirect_url(
        "/url?q=https://example.org/a&sa=t") == "https://example.org/a"


def test_decode_google_redirect_percent_encoded_open_url():
    """Percent-encoded открытый URL в /url?q= — декодируется."""
    assert topvisor._decode_google_redirect_url(
        "/url?q=https%3A%2F%2Fexample.org%2Fpath") == "https://example.org/path"


def test_decode_google_redirect_url_q_with_proto_token():
    """/url?q= с base64url-токеном protobuf — URL извлекается walker'ом."""
    token = base64.urlsafe_b64encode(_proto_with_url(b"http://tok.example/")).decode().rstrip("=")
    assert topvisor._decode_google_redirect_url(f"/url?q={token}") == "http://tok.example/"


def test_decode_google_redirect_garbage_payload_returns_none():
    """Мусор вместо base64 — None без исключений."""
    assert topvisor._decode_google_redirect_url("/goto?url=!!!not-base64!!!") is None


def test_decode_google_redirect_non_wrapper_returns_none():
    """Обычный URL и пустая строка — не обёртки, None."""
    assert topvisor._decode_google_redirect_url("https://example.com/x") is None
    assert topvisor._decode_google_redirect_url("") is None
    assert topvisor._decode_google_redirect_url(None) is None


def test_is_google_redirect_wrapper_absolute_hosts():
    """Абсолютные обёртки — только на хостах google.com; чужой /url?q= не трогаем."""
    assert topvisor._is_google_redirect_wrapper("https://www.google.com/goto?url=CAES")
    assert topvisor._is_google_redirect_wrapper("https://google.com/url?q=x")
    assert not topvisor._is_google_redirect_wrapper("https://example.com/url?q=x")


def _snapshot_result_for_url(url: str, domain: str = "") -> dict:
    """Минимальный result snapshots_2/history с одной строкой."""
    return {
        "keywords": [{
            "name": "test query",
            "snapshotsData": {"1:1": {"url": url, "domain": domain}},
        }],
    }


def test_get_snapshot_skips_undecodable_url(monkeypatch, caplog):
    """Обёртка с невосстановимым токеном (реальный prod-кейс) — строка
    пропускается, 0 строк, WARNING с примерами (не более 3)."""
    monkeypatch.setattr(
        topvisor, "_post",
        lambda *a, **k: _snapshot_result_for_url(f"/goto?url={_PROD_GOTO_PAYLOADS[0]}"))
    monkeypatch.setattr(topvisor.log, "propagate", True)

    with caplog.at_level(logging.WARNING):
        rows = topvisor.get_snapshot(123, 1300, "2026-09-10", 10,
                                     searcher_key=1, region_key=117,
                                     region_lang="lt", region_device=0, geo="Литва")

    assert rows == []
    warnings = [r for r in caplog.records
                if "пропущено" in r.message and "невосстановимым URL" in r.message]
    assert warnings
    assert "1 строк" in warnings[0].message
    assert "CAESYg" in warnings[0].message  # пример урезан до 80 символов


def test_get_snapshot_decodes_and_substitutes_url(monkeypatch):
    """Обёртка с URL внутри protobuf — в Row попадает реальный url и domain
    (netloc, lowercase, без www), даже если val.domain пуст."""
    wrapper = _goto_wrapper(b"https://WWW.Example.COM/path?q=1")
    monkeypatch.setattr(
        topvisor, "_post", lambda *a, **k: _snapshot_result_for_url(wrapper))

    rows = topvisor.get_snapshot(123, 1300, "2026-09-10", 10,
                                 searcher_key=1, region_key=117,
                                 region_lang="lt", region_device=0, geo="Литва")

    assert len(rows) == 1
    assert rows[0]["url"] == "https://WWW.Example.COM/path?q=1"
    assert rows[0]["domain"] == "example.com"


def test_get_snapshot_uses_val_domain_when_present(monkeypatch):
    """Если val.domain заполнен — используется он (после нормализации),
    а не netloc декодированного URL."""
    wrapper = _goto_wrapper(b"https://other.example.org/deep")
    monkeypatch.setattr(
        topvisor, "_post",
        lambda *a, **k: _snapshot_result_for_url(wrapper, domain="www.ValDomain.Example"))

    rows = topvisor.get_snapshot(123, 1300, "2026-09-10", 10,
                                 searcher_key=1, region_key=117,
                                 region_lang="lt", region_device=0, geo="Литва")

    assert len(rows) == 1
    assert rows[0]["domain"] == "valdomain.example"


def test_get_snapshot_rejects_non_http_url(monkeypatch, caplog):
    """javascript:/текст в val.url — строка пропускается (sanity-gate),
    счётчик в WARNING."""
    monkeypatch.setattr(
        topvisor, "_post",
        lambda *a, **k: _snapshot_result_for_url("javascript:alert(1)"))
    monkeypatch.setattr(topvisor.log, "propagate", True)

    with caplog.at_level(logging.WARNING):
        rows = topvisor.get_snapshot(123, 1300, "2026-09-10", 10,
                                     searcher_key=1, region_key=117,
                                     region_lang="lt", region_device=0, geo="Литва")

    assert rows == []
    assert any("невосстановимым URL" in r.message for r in caplog.records)


def test_get_snapshot_rejects_plain_text_url(monkeypatch):
    """"code text" в val.url — не обёртка, не http(s) — пропуск."""
    monkeypatch.setattr(
        topvisor, "_post", lambda *a, **k: _snapshot_result_for_url("code text"))

    rows = topvisor.get_snapshot(123, 1300, "2026-09-10", 10,
                                 searcher_key=1, region_key=117,
                                 region_lang="lt", region_device=0, geo="Литва")

    assert rows == []


def test_get_snapshot_mixed_rows_keeps_valid(monkeypatch):
    """Смешанный снимок: валидный URL + обёртка-мусор — валидный сохраняется,
    мусор пропускается, счётчик = 1."""
    result = {
        "keywords": [{
            "name": "test query",
            "snapshotsData": {
                "1:1": {"url": "https://good.example/", "domain": "good.example"},
                "1:2": {"url": f"/goto?url={_PROD_GOTO_PAYLOADS[1]}", "domain": ""},
            },
        }],
    }
    monkeypatch.setattr(topvisor, "_post", lambda *a, **k: result)
    monkeypatch.setattr(topvisor.log, "propagate", True)

    rows = topvisor.get_snapshot(123, 1300, "2026-09-10", 10,
                                 searcher_key=1, region_key=117,
                                 region_lang="lt", region_device=0, geo="Литва")

    assert len(rows) == 1
    assert rows[0]["url"] == "https://good.example/"
    assert rows[0]["position"] == 1
