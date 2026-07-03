# TASKS — SERPlux

Трекер задач. Формат: T-XXX: описание.
Обновлять при создании новой задачи. Отмечать [x] при завершении.

## T-001: Тесты на новую схему БД

**Приоритет:** высокий
**Зависимость:** после миграции БД (ADR 2026-07-03)
**Затронутые файлы:** tests/test_storage_schema.py, storage.py

### Что покрыть:

1. **Миграция без потери строк**
   - COUNT(*) results == COUNT(*) positions после переноса
   - Все UNIQUE-ключи сохранены
   - Метки перенесены в labels (version=1, mode='snippets')
   - DROP results только после верификации

2. **Инкремент версий**
   - Первая вставка → label_version=1
   - Повторная вставка того же position_id + label_mode → label_version=2
   - Другой label_mode → независимая ветка (version=1)
   - Гонка: два параллельных INSERT → один UNIQUE violation, retry → success

3. **get_history() с фильтрами**
   - Без фильтров → последняя метка на каждую позицию
   - filter client_id → только строки клиента
   - filter label_version='all' → все версии (дубли позиций)
   - filter date → строки за дату

4. **get_cached_label()**
   - Возвращает последнюю sentiment по (url, query) через JOIN
   - Кэш переживает смену даты (ORDER BY created_at DESC)

5. **insert_labels()**
   - sentiment=None → пропускается
   - Возвращает кол-во вставленных
   - label_mode CHECK: только domains/snippets/full

6. **Атомарность**
   - Retry на UNIQUE violation (макс. 3 попытки)
   - При 3 неудачах — ERROR log, не exception

### Критерий приёмки:
- Все тесты проходят на изолированной БД (:memory:)
- Ни один существующий тест не сломан (64 теста)

---

## T-002: Режим domains разметки + справочник доменов

**Статус:** ✅ Done (2026-07-03)
**Приоритет:** высокий
**Затронутые файлы:** storage.py, labeler.py, migrate.py, tests/test_labeler_modes.py, docs/contracts.md, docs/progress.md

### Что сделано:

- **storage.py**: таблица `domain_labels` + `get_domain_label()` / `upsert_domain_label()`, поле `confidence` в `labels`
- **labeler.py**: режим `domains` (справочник, без LLM), параметр `client_id`, `confidence='high'`
- **migrate.py**: DDL-заплатки для `domain_labels` и `confidence`
- **tests/test_labeler_modes.py**: 11 тестов (справочник, domains без LLM, snippets не сломан)
- **docs/contracts.md**: обновлена сигнатура `label()`

### Результат:
- 95/95 тестов зелёные
- LLM не вызывается в режиме `domains` (подтверждено моком)
- Обратная совместимость `Row` (label=алиас) сохранена

---

## T-003: Идемпотентность migrate.py по схеме (domain_labels + confidence)

**Статус:** ✅ Done (2026-07-03)
**Приоритет:** высокий
**Затронутые файлы:** migrate.py, tests/test_migrate_idempotent.py

### Что сделано:

- **migrate.py**: перестроен поток — убран ранний `return` при отсутствии `results`. Теперь `_create_new_schema()`, `_apply_schema_patches()` и авто-клиент `'default'` выполняются **всегда**. Перенос данных из `results` — условно (только если таблица существует).
- **`_verify_schema()`**: новая финальная проверка — логирует таблицы/колонки, `raise RuntimeError` при отсутствии `domain_labels` или `labels.confidence`.
- **DDL `labels`**: колонка `confidence` включена в `_create_new_schema()` для свежих БД.
- **tests/test_migrate_idempotent.py**: 3 теста:
  1. БД после 1-й миграции (без `domain_labels`/`confidence`) → досоздаёт
  2. Полностью мигрированная БД → идемпотентна (двойной запуск)
  3. Legacy `results` → перенос + новая схема

### Результат:
- 111/111 тестов зелёные (3 новых + 108 старых)
- `migrate.py` на любой БД (чистая / частично мигрированная / полностью мигрированная / legacy) отрабатывает корректно
- Рабочее дерево чистое
