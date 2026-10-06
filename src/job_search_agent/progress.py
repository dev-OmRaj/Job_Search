from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProgressUpdate:
    key: str
    description: str
    completed: int | None = None
    total: int | None = None
    finished: bool = False


ProgressCallback = Callable[[ProgressUpdate], None]


def emit_progress(
    callback: ProgressCallback | None,
    key: str,
    description: str,
    *,
    completed: int | None = None,
    total: int | None = None,
    finished: bool = False,
) -> None:
    if callback is not None:
        callback(
            ProgressUpdate(
                key=key,
                description=description,
                completed=completed,
                total=total,
                finished=finished,
            )
        )
