"""Normalized observations shared by polling and webhooks."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal


@dataclass(frozen=True)
class Observation:
    source: str
    entity_id: str
    name: str
    state: Literal["UP", "DOWN"]
    detail: str = ""
    occurred_at: datetime | None = None
    observed_at: datetime | None = None
    severity: str = "WARNING"
