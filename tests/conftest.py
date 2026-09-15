from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("DIETBOT_HOME", str(ROOT))
os.environ.setdefault("PYTHONUTF8", "1")

from fixtures_week import WEEK_SLOTS  # noqa: E402

from dietbot.engine import EngineRequest, solve_week  # noqa: E402
from dietbot.storage.paths import Paths  # noqa: E402
from dietbot.storage.repository import Repository  # noqa: E402


@pytest.fixture(scope="session")
def repo() -> Repository:
    return Repository.load(Paths(ROOT))


@pytest.fixture(scope="session")
def week_request() -> EngineRequest:
    return EngineRequest.model_validate({"slots": WEEK_SLOTS})


@pytest.fixture(scope="session")
def week_result(repo, week_request):
    return solve_week(week_request, repo.assumptions, repo.products, repo.dishes)
