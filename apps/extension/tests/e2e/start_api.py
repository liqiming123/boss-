from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parents[4]
DATABASE = Path(__file__).with_name("recruitment-e2e.db")


def main() -> None:
    DATABASE.unlink(missing_ok=True)
    os.environ["DATABASE_URL"] = f"sqlite:///{DATABASE}"
    os.environ["PYTHONPATH"] = str(ROOT / "apps/api/src")
    sys.path.insert(0, os.environ["PYTHONPATH"])
    subprocess.run(
        [
            str(ROOT / ".venv/bin/python"),
            "-m",
            "alembic",
            "-c",
            str(ROOT / "apps/api/alembic.ini"),
            "upgrade",
            "head",
        ],
        cwd=ROOT,
        check=True,
        env=os.environ,
    )
    from recruitment_collab.infrastructure.seed import seed

    seed()
    try:
        uvicorn.run("recruitment_collab.main:app", host="127.0.0.1", port=8000)
    finally:
        DATABASE.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
