import pytest
from app import db

@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """把数据库指到临时文件，隔离测试数据"""
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    return db