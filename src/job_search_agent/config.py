from __future__ import annotations

from pathlib import Path

import yaml

from .models import ProjectPreferences


def load_preferences(path: Path) -> ProjectPreferences:
    """Load optional role preferences for automatically discovered resumes."""

    if not path.is_file():
        raise FileNotFoundError(
            f"Preferences file not found: {path}. Copy preferences.example.yaml first."
        )
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return ProjectPreferences.model_validate(raw)
