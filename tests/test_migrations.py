"""`alembic upgrade head` must build a working schema from NOTHING, and
the migrations must match the ORM models exactly (no drift)."""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _alembic(db_path, *args):
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{Path(db_path).as_posix()}", "PYTHONPATH": str(ROOT)}
    return subprocess.run([sys.executable, "-m", "alembic", *args], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=120)


def test_fresh_database_upgrades_to_head_and_matches_models():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "fresh.db"
        up = _alembic(db, "upgrade", "head")
        assert up.returncode == 0, up.stderr[-800:]
        check = _alembic(db, "check")
        assert check.returncode == 0, check.stdout[-800:] + check.stderr[-800:]


def test_single_migration_head():
    with tempfile.TemporaryDirectory() as tmp:
        heads = _alembic(Path(tmp) / "x.db", "heads")
        assert heads.returncode == 0 and heads.stdout.count("(head)") == 1
