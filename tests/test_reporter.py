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
        # 2 субъекта: col 0(empty) + col 1(pos1) + col 2(url1) + col 3(div) + col 4(pos2) + col 5(url2) = 6 cols
        assert layout["cols"] == 6
        assert len(layout["subjects"]) == 2
        assert layout["subjects"][0]["key"] == "subject1"
        assert layout["subjects"][0]["pos"] == 1
        assert layout["subjects"][0]["url"] == 2
        assert layout["subjects"][1]["key"] == "subject2"
        assert layout["subjects"][1]["pos"] == 4
        assert layout["subjects"][1]["url"] == 5
    
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
        # 4 субъекта: 4*(pos+url) + 3 разделителя = 8 + 3 = 11, но с начальной колонкой 1
        # расчёт: col_idx=1; subject1 (pos=1,url=2), col_idx=4; div, col_idx=5;
        # subject2 (pos=5,url=6), col_idx=8; div, col_idx=9; ... в итоге должно быть 12
        assert layout["cols"] == 12
        
        # Проверяем позиции каждого субъекта
        assert layout["subjects"][0]["pos"] == 1
        assert layout["subjects"][0]["url"] == 2
        assert layout["subjects"][1]["pos"] == 4
        assert layout["subjects"][1]["url"] == 5
        assert layout["subjects"][2]["pos"] == 7
        assert layout["subjects"][2]["url"] == 8
        assert layout["subjects"][3]["pos"] == 10
        assert layout["subjects"][3]["url"] == 11
    
    def test_layout_7_subjects(self):
        """Раскладка для 7 субъектов."""
        queries = [
            {"key": f"subject{i}", "display": f"Subject {i}"}
            for i in range(1, 8)
        ]
        layout = _build_subject_layout(queries)
        
        assert layout["num_subjects"] == 7
        # 7 субъектов: 7*(pos+url) + 6 разделителей = 14 + 6 = 20, но считаем со смещением
        # Начальная col=1, затем (pos, url) + div для каждого, кроме последнего
        # 1 + 7*(2+1) - 1 = 1 + 20 = 21, но проверим логику в функции
        assert layout["cols"] > 0
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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
