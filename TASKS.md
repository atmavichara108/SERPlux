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
