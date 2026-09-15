"""Silnik gramatur (OR-Tools CP-SAT)."""

from .catalog import Catalog, EngineInputError
from .settings import EngineSettings
from .solver import ENGINE_NAME, solve_week, split_blocks
from .types import BlockReport, BlockStatus, EngineRequest, EngineResult, EngineSlot

__all__ = [
    "Catalog",
    "ENGINE_NAME",
    "BlockReport",
    "BlockStatus",
    "EngineInputError",
    "EngineRequest",
    "EngineResult",
    "EngineSettings",
    "EngineSlot",
    "solve_week",
    "split_blocks",
]
