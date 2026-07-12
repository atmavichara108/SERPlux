"""
Тесты для reporter.py — динамическая раскладка субъектов и колонок.

Проверяют:
- Профиль с 2 субъектами → 2 блока (4 колонки + 1 разделитель = 5)
- Профиль с 4 субъектами → 4 блока (8 колонок + 3 разделителя = 11, но с учётом начальной колонки = 12)
- Профиль с 7 субъектами → 7 блоков
- Гео берутся из regions_map клиента
"""

import pytest
import tempfile
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import storage
from reporter import _build_subject_layout


class TestSubjectLayout:
    """Тесты вспомогательной функции _build_subject_layout."""
    
    def test_layout_2_subjects(self):
        """Раскладка для 2 субъектов."""
        queries = [
            {"key": "subject1", "display": "Subject 1"},
            {"key": "subject2", "display": "Subject 2"},
        ]
        layout = _build_subject_layout(queries)
        
        assert layout["num_subjects"] == 2
        # 2 субъекта: S1=(1,2), буфер 3 (3,4,5), S2=(6,7) → cols=8 (0..7)
        # col_idx: 1 → +2=3 → +3=6 → +2=8
        assert layout["cols"] == 8
        assert len(layout["subjects"]) == 2
        assert layout["subjects"][0]["key"] == "subject1"
        assert layout["subjects"][0]["pos"] == 1
        assert layout["subjects"][0]["url"] == 2
        assert layout["subjects"][1]["key"] == "subject2"
        assert layout["subjects"][1]["pos"] == 6
        assert layout["subjects"][1]["url"] == 7
    
    def test_layout_4_subjects(self):
        """Раскладка для 4 субъектов (как client1)."""
        queries = [
            {"key": "juri sudheimer", "display": "Juri Sudheimer"},
            {"key": "erik sudheimer", "display": "Erik Sudheimer"},
            {"key": "sct chemicals", "display": "SCT Chemicals"},
            {"key": "chempioil", "display": "Chempioil"},
        ]
        layout = _build_subject_layout(queries)
        
        assert layout["num_subjects"] == 4
        # S1=(1,2), буфер 3 (3,4,5), S2=(6,7), буфер 1 (8), S3=(9,10), буфер 1 (11), S4=(12,13)
        # col_idx: 1 → +2=3 → +3=6 → +2=8 → +1=9 → +2=11 → +1=12 → +2=14
        assert layout["cols"] == 14
        
        assert layout["subjects"][0]["pos"] == 1
        assert layout["subjects"][0]["url"] == 2
        assert layout["subjects"][1]["pos"] == 6
        assert layout["subjects"][1]["url"] == 7
        assert layout["subjects"][2]["pos"] == 9
        assert layout["subjects"][2]["url"] == 10
        assert layout["subjects"][3]["pos"] == 12
        assert layout["subjects"][3]["url"] == 13
    
    def test_layout_7_subjects(self):
        """Раскладка для 7 субъектов."""
        queries = [
            {"key": f"subject{i}", "display": f"Subject {i}"}
            for i in range(1, 8)
        ]
        layout = _build_subject_layout(queries)
        
        assert layout["num_subjects"] == 7
        # S1=(1,2), буфер 3, S2=(6,7), буфер 1, S3=(9,10), буфер 1, S4=(12,13), буфер 1,
        # S5=(15,16), буфер 1, S6=(18,19), буфер 1, S7=(21,22)
        # col_idx: 1 → +2+3=6 → +2+1=9 → +2+1=12 → +2+1=15 → +2+1=18 → +2+1=21 → +2=23
        assert layout["cols"] == 23
        assert len(layout["subjects"]) == 7
        
        # Проверяем возрастание индексов
        for i, sb in enumerate(layout["subjects"]):
            assert "key" in sb
            assert "display" in sb
            assert "pos" in sb
            assert "url" in sb
            assert sb["url"] == sb["pos"] + 1
            if i > 0:
                assert sb["pos"] > layout["subjects"][i-1]["url"]
    
    def test_layout_single_subject(self):
        """Раскладка для 1 субъекта."""
        queries = [
            {"key": "only", "display": "Only Subject"},
        ]
        layout = _build_subject_layout(queries)
        
        assert layout["num_subjects"] == 1
        assert layout["cols"] == 3  # 0(empty) + 1(pos) + 2(url)
        assert layout["subjects"][0]["pos"] == 1
        assert layout["subjects"][0]["url"] == 2
    
    def test_layout_empty(self):
        """Раскладка для пустого списка."""
        queries = []
        layout = _build_subject_layout(queries)
        
        assert layout["num_subjects"] == 0
        assert layout["cols"] == 1  # только начальная колонка
        assert layout["subjects"] == []


class TestBuildReportWithDynamicProfile:
    """Тесты build_report с динамическими профилями клиентов."""
    
    @pytest.fixture
    def temp_db(self):
        """Временная БД для тестов."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        
        # Инициализируем схему
        storage._init_db(db_path=db_path)
        
        yield db_path
        
        # Очищаем после теста
        Path(db_path).unlink(missing_ok=True)
    
    @pytest.fixture
    def mock_gspread(self):
        """Мок gspread для изоляции от боевой таблицы."""
        fake_worksheet = MagicMock()
        fake_worksheet.id = 999
        fake_spreadsheet = MagicMock()
        fake_spreadsheet.worksheet.return_value = fake_worksheet
        fake_client = MagicMock()
        fake_client.open_by_key.return_value = fake_spreadsheet
        
        with patch("reporter.os.path.exists", return_value=True):
            with patch("reporter.gspread.service_account", return_value=fake_client) as mock_sa:
                yield {
                    "client": fake_client,
                    "spreadsheet": fake_spreadsheet,
                    "worksheet": fake_worksheet,
                    "service_account": mock_sa,
                }
    
    def test_report_with_4_subjects(self, temp_db, mock_gspread):
        """Построение отчёта для профиля с 4 субъектами (как client1)."""
        from reporter import build_report
        
        # Создаём профиль клиента с 4 субъектами
        client_id = "client_4subj"
        queries = [
            {"key": "juri sudheimer", "display": "Juri Sudheimer"},
            {"key": "erik sudheimer", "display": "Erik Sudheimer"},
            {"key": "sct chemicals", "display": "SCT Chemicals"},
            {"key": "chempioil", "display": "Chempioil"},
        ]
        regions_map = [
            {"geo_name": "Литва", "searcher": "google"},
            {"geo_name": "Германия", "searcher": "google"},
        ]
        
        storage.create_client(
            client_id=client_id,
            client_name="Test Client 4",
            queries=queries,
            regions_map=regions_map,
            db_path=temp_db,
        )
        
        # Добавляем тестовые данные
        test_rows = [
            {
                "date": "2026-07-10",
                "searcher": "google",
                "query": "juri sudheimer",
                "geo": "Литва",
                "region_index": 1300,
                "position": 1,
                "url": "https://example1.com",
                "domain": "example1.com",
                "snippet": "test snippet",
                "client_id": client_id,
            },
            {
                "date": "2026-07-10",
                "searcher": "google",
                "query": "erik sudheimer",
                "geo": "Литва",
                "region_index": 1300,
                "position": 2,
                "url": "https://example2.com",
                "domain": "example2.com",
                "snippet": "test snippet",
                "client_id": client_id,
            },
        ]
        
        storage.save(test_rows, client_id=client_id, db_path=temp_db)
        
        # Построение отчёта не должно упасть и НЕ должно писать в боевую таблицу
        build_report(date="2026-07-10", client_id=client_id, db_path=temp_db)
        
        # Проверяем, что gspread был вызван (тест дошёл до записи)
        mock_gspread["service_account"].assert_called_once()
    
    def test_report_with_2_subjects(self, temp_db, mock_gspread):
        """Построение отчёта для профиля с 2 субъектами."""
        from reporter import build_report
        
        client_id = "client_2subj"
        queries = [
            {"key": "subject1", "display": "Subject 1"},
            {"key": "subject2", "display": "Subject 2"},
        ]
        regions_map = [
            {"geo_name": "Литва", "searcher": "google"},
        ]
        
        storage.create_client(
            client_id=client_id,
            client_name="Test Client 2",
            queries=queries,
            regions_map=regions_map,
            db_path=temp_db,
        )
        
        test_rows = [
            {
                "date": "2026-07-10",
                "searcher": "google",
                "query": "subject1",
                "geo": "Литва",
                "region_index": 1300,
                "position": 1,
                "url": "https://example1.com",
                "domain": "example1.com",
                "snippet": "test",
                "client_id": client_id,
            },
        ]
        
        storage.save(test_rows, client_id=client_id, db_path=temp_db)
        
        build_report(date="2026-07-10", client_id=client_id, db_path=temp_db)
        mock_gspread["service_account"].assert_called_once()
    
    def test_report_with_7_subjects(self, temp_db, mock_gspread):
        """Построение отчёта для профиля с 7 субъектами."""
        from reporter import build_report
        
        client_id = "client_7subj"
        queries = [
            {"key": f"subject{i}", "display": f"Subject {i}"}
            for i in range(1, 8)
        ]
        regions_map = [
            {"geo_name": "Литва", "searcher": "google"},
            {"geo_name": "Германия", "searcher": "google"},
            {"geo_name": "Великобритания", "searcher": "google"},
        ]
        
        storage.create_client(
            client_id=client_id,
            client_name="Test Client 7",
            queries=queries,
            regions_map=regions_map,
            db_path=temp_db,
        )
        
        test_rows = [
            {
                "date": "2026-07-10",
                "searcher": "google",
                "query": f"subject{i}",
                "geo": "Литва",
                "region_index": 1300,
                "position": i,
                "url": f"https://example{i}.com",
                "domain": f"example{i}.com",
                "snippet": "test",
                "client_id": client_id,
            }
            for i in range(1, 8)
        ]
        
        storage.save(test_rows, client_id=client_id, db_path=temp_db)
        
        build_report(date="2026-07-10", client_id=client_id, db_path=temp_db)
        mock_gspread["service_account"].assert_called_once()
    
    def test_report_missing_client(self, temp_db, mock_gspread):
        """Отчёт для несуществующего клиента должен выдать ошибку."""
        from reporter import build_report
        
        # Попытка построить отчёт для клиента, которого нет в БД
        # Функция выведет лог и вернёт None
        build_report(date="2026-07-10", client_id="nonexistent", db_path=temp_db)
        # gspread не должен быть вызван — клиент не найден
        mock_gspread["service_account"].assert_not_called()
    
    def test_report_empty_queries(self, temp_db, mock_gspread):
        """Отчёт для профиля без субъектов должен выдать ошибку."""
        from reporter import build_report
        
        client_id = "client_empty"
        
        storage.create_client(
            client_id=client_id,
            client_name="Test Client Empty",
            queries=[],  # Пусто!
            db_path=temp_db,
        )
        
        build_report(date="2026-07-10", client_id=client_id, db_path=temp_db)
        # gspread не должен быть вызван — нет субъектов
        mock_gspread["service_account"].assert_not_called()


class TestLayoutBuffers:
    """Тесты буферных колонок между субъектами."""

    def test_buffer_after_first_subject_is_3_columns(self):
        """Буфер после первого субъекта = 3 колонки (D, E, F)."""
        queries = [
            {"key": "s1", "display": "Subject 1"},
            {"key": "s2", "display": "Subject 2"},
        ]
        layout = _build_subject_layout(queries)
        
        assert layout["subjects"][0]["pos"] == 1
        assert layout["subjects"][0]["url"] == 2
        assert layout["subjects"][1]["pos"] == 6
        assert layout["subjects"][1]["url"] == 7
        
        buffer_size = layout["subjects"][1]["pos"] - layout["subjects"][0]["url"] - 1
        assert buffer_size == 3, f"Буфер после первого субъекта должен быть 3 колонки, получено {buffer_size}"

    def test_buffer_between_subsequent_subjects_is_1_column(self):
        """Буфер между последующими субъектами (≥2) = 1 колонка."""
        queries = [
            {"key": "s1", "display": "Subject 1"},
            {"key": "s2", "display": "Subject 2"},
            {"key": "s3", "display": "Subject 3"},
            {"key": "s4", "display": "Subject 4"},
        ]
        layout = _build_subject_layout(queries)
        
        buffer_23 = layout["subjects"][2]["pos"] - layout["subjects"][1]["url"] - 1
        assert buffer_23 == 1, f"Буфер между S2 и S3 должен быть 1 колонка, получено {buffer_23}"
        
        buffer_34 = layout["subjects"][3]["pos"] - layout["subjects"][2]["url"] - 1
        assert buffer_34 == 1, f"Буфер между S3 и S4 должен быть 1 колонка, получено {buffer_34}"

    def test_subject_blocks_do_not_overlap(self):
        """Блоки субъектов не пересекаются."""
        queries = [
            {"key": f"s{i}", "display": f"Subject {i}"}
            for i in range(1, 5)
        ]
        layout = _build_subject_layout(queries)
        
        for i in range(1, len(layout["subjects"])):
            prev_url = layout["subjects"][i-1]["url"]
            curr_pos = layout["subjects"][i]["pos"]
            assert curr_pos > prev_url, \
                f"Блок субъекта {i} пересекается с предыдущим: pos={curr_pos} <= prev_url={prev_url}"


class TestSentimentFillCoordinates:
    """Тесты точных координат заливки sentiment (канон Лист1)."""

    def test_fill_coordinates_n3_subjects(self):
        """Для N=3: заливка строго на pos-колонках B(1), G(6), J(9)."""
        queries = [
            {"key": "s1", "display": "Subject 1"},
            {"key": "s2", "display": "Subject 2"},
            {"key": "s3", "display": "Subject 3"},
        ]
        layout = _build_subject_layout(queries)
        
        expected_positions = [1, 6, 9]
        expected_urls = [2, 7, 10]
        
        for i, sb in enumerate(layout["subjects"]):
            assert sb["pos"] == expected_positions[i], \
                f"S{i+1}: pos-колонка должна быть {expected_positions[i]}, получено {sb['pos']}"
            assert sb["url"] == expected_urls[i], \
                f"S{i+1}: url-колонка должна быть {expected_urls[i]}, получено {sb['url']}"
        
        buffer_cols = {3, 4, 5, 8}
        for sb in layout["subjects"]:
            assert sb["pos"] not in buffer_cols, \
                f"pos-колонка {sb['pos']} не должна быть в буфере"
            assert sb["url"] not in buffer_cols, \
                f"url-колонка {sb['url']} не должна быть в буфере"

    def test_fill_coordinates_n4_subjects(self):
        """Для N=4: заливка строго на pos-колонках B(1), G(6), J(9), M(12)."""
        queries = [
            {"key": "s1", "display": "Subject 1"},
            {"key": "s2", "display": "Subject 2"},
            {"key": "s3", "display": "Subject 3"},
            {"key": "s4", "display": "Subject 4"},
        ]
        layout = _build_subject_layout(queries)
        
        expected_positions = [1, 6, 9, 12]
        expected_urls = [2, 7, 10, 13]
        
        for i, sb in enumerate(layout["subjects"]):
            assert sb["pos"] == expected_positions[i], \
                f"S{i+1}: pos-колонка должна быть {expected_positions[i]}, получено {sb['pos']}"
            assert sb["url"] == expected_urls[i], \
                f"S{i+1}: url-колонка должна быть {expected_urls[i]}, получено {sb['url']}"
        
        buffer_cols = {3, 4, 5, 8, 11}
        for sb in layout["subjects"]:
            assert sb["pos"] not in buffer_cols, \
                f"pos-колонка {sb['pos']} не должна быть в буфере"
            assert sb["url"] not in buffer_cols, \
                f"url-колонка {sb['url']} не должна быть в буфере"

    def test_url_is_always_pos_plus_one(self):
        """URL-колонка всегда = pos-колонка + 1."""
        for n in range(1, 8):
            queries = [{"key": f"s{i}", "display": f"Subject {i}"} for i in range(n)]
            layout = _build_subject_layout(queries)
            for sb in layout["subjects"]:
                assert sb["url"] == sb["pos"] + 1, \
                    f"url ({sb['url']}) должен быть pos+1 ({sb['pos']+1})"

    def test_subject_name_in_url_column(self):
        """Имя субъекта пишется в url-колонку (правая), не в pos-колонку (левая)."""
        queries = [
            {"key": "juri sudheimer", "display": "Juri Sudheimer"},
            {"key": "erik sudheimer", "display": "Erik Sudheimer"},
        ]
        layout = _build_subject_layout(queries)
        
        # pos-колонка (левая, B/G) — для гео и номеров позиций
        # url-колонка (правая, C/H) — для имени субъекта и URL
        assert layout["subjects"][0]["pos"] == 1   # B
        assert layout["subjects"][0]["url"] == 2   # C — имя "Juri Sudheimer" здесь
        assert layout["subjects"][1]["pos"] == 6   # G
        assert layout["subjects"][1]["url"] == 7   # H — имя "Erik Sudheimer" здесь


class TestAccumulativeReport:
    """Тесты накопительного режима отчёта (версии сверху, старые снизу)."""

    def test_is_version_header(self):
        """_is_version_header распознаёт заголовки версий."""
        from reporter import _is_version_header
        
        assert _is_version_header(["Позиции Google на 11.7.2026", "", ""])
        assert _is_version_header(["Позиции Яндекс на 10.7.2026", "", ""])
        assert not _is_version_header(["", "", ""])
        assert not _is_version_header(["Juri Sudheimer", "", ""])
        assert not _is_version_header(["Lithuania", "", ""])
        assert not _is_version_header(["1", "", ""])

    def test_build_report_accumulates_versions(self, monkeypatch):
        """Второй вызов build_report вставляет новый блок сверху, не вызывая clear()."""
        from reporter import build_report
        
        monkeypatch.setenv("GOOGLE_CREDENTIALS_PATH", "credentials.json")
        monkeypatch.setenv("GOOGLE_SHEET_ID", "test-sheet-id")
        
        fake_worksheet = MagicMock()
        fake_worksheet.id = 123
        fake_worksheet.get_all_values.return_value = [
            ["Позиции Google на 10.7.2026", "", ""],
            ["", "", ""],
            ["", "Juri", ""],
        ]
        fake_spreadsheet = MagicMock()
        fake_spreadsheet.worksheet.return_value = fake_worksheet
        fake_client = MagicMock()
        fake_client.open_by_key.return_value = fake_spreadsheet
        
        # Создаём временную БД
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        
        try:
            storage._init_db(db_path=db_path)
            
            # Создаём профиль клиента
            storage.create_client(
                client_id="test",
                client_name="Test Client",
                queries=[{"key": "juri sudheimer", "display": "Juri"}],
                regions_map=[{"geo_name": "Литва", "searcher": "google"}],
                db_path=db_path,
            )
            
            # Добавляем тестовые данные
            test_rows = [
                {
                    "date": "2026-07-11",
                    "searcher": "google",
                    "query": "juri sudheimer",
                    "geo": "Литва",
                    "region_index": 1300,
                    "position": 1,
                    "url": "https://example.com",
                    "domain": "example.com",
                    "snippet": "test",
                    "label": "positive",
                }
            ]
            storage.save(test_rows, client_id="test", db_path=db_path)
            
            with patch("reporter.os.path.exists", return_value=True):
                with patch("reporter.gspread.service_account", return_value=fake_client):
                    build_report(date="2026-07-11", client_id="test", db_path=db_path)
        finally:
            Path(db_path).unlink(missing_ok=True)
        
        # Проверяем, что clear() НЕ вызывался
        fake_worksheet.clear.assert_not_called()
        
        # Проверяем, что batch_update вызывался для insertDimension
        batch_update_calls = fake_spreadsheet.batch_update.call_args_list
        assert len(batch_update_calls) >= 1, "batch_update должен вызываться для insertDimension"
        
        # Первый вызов — insertDimension
        first_call = batch_update_calls[0]
        requests = first_call[0][0]["requests"]
        assert any("insertDimension" in req for req in requests), \
            "Должен быть insertDimension для вставки строк сверху"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
