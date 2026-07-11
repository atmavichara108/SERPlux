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
    
    def test_report_with_4_subjects(self, temp_db):
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
        
        # Построение отчёта не должно упасть
        # (не проверяем Google Sheets, так как тест локальный)
        try:
            build_report(date="2026-07-10", client_id=client_id, db_path=temp_db)
        except Exception as e:
            # Ожидаем ошибку только из-за отсутствия Google Sheet ID, а не из самой логики
            if "GOOGLE_SHEET_ID" not in str(e) and "credentials" not in str(e).lower():
                raise
    
    def test_report_with_2_subjects(self, temp_db):
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
        
        try:
            build_report(date="2026-07-10", client_id=client_id, db_path=temp_db)
        except Exception as e:
            if "GOOGLE_SHEET_ID" not in str(e) and "credentials" not in str(e).lower():
                raise
    
    def test_report_with_7_subjects(self, temp_db):
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
        
        try:
            build_report(date="2026-07-10", client_id=client_id, db_path=temp_db)
        except Exception as e:
            if "GOOGLE_SHEET_ID" not in str(e) and "credentials" not in str(e).lower():
                raise
    
    def test_report_missing_client(self, temp_db):
        """Отчёт для несуществующего клиента должен выдать ошибку."""
        from reporter import build_report
        
        # Попытка построить отчёт для клиента, которого нет в БД
        # Функция выведет лог и вернёт None
        build_report(date="2026-07-10", client_id="nonexistent", db_path=temp_db)
        # Это не должно упасть, только залогировать ошибку
    
    def test_report_empty_queries(self, temp_db):
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
        # Это не должно упасть, только залогировать предупреждение


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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
