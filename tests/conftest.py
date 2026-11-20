import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database  # noqa: E402
from app import create_app  # noqa: E402


@pytest.fixture()
def app(tmp_path):
    return create_app(db_path=str(tmp_path / "test.db"))


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def reel_id(app):
    def _find(handle: str) -> int:
        with app.app_context():
            row = database.get_db().execute(
                "SELECT r.id FROM reels r JOIN users u ON u.id = r.creator_id WHERE u.handle = ?", (handle,)
            ).fetchone()
            return row[0]
    return _find


@pytest.fixture()
def query(app):
    def _q(sql, params=()):
        with app.app_context():
            return database.get_db().execute(sql, params).fetchall()
    return _q
