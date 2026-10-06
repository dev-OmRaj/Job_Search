from pathlib import Path

from job_search_agent.config import load_preferences
from job_search_agent.resumes import (
    load_active_resume_profiles,
    load_resume_profiles,
    sanitize_resume_text,
)


def test_sanitize_resume_removes_common_contact_details():
    text = """Name: Ada Lovelace
Email: ada@example.com
+91 98765 43210
Portfolio: https://example.com/ada
Python engineer with SQL experience.
"""
    sanitized = sanitize_resume_text(text)

    assert "ada@example.com" not in sanitized
    assert "98765" not in sanitized
    assert "https://" not in sanitized
    assert "Python engineer" in sanitized


def test_resumes_are_discovered_and_receive_global_job_preferences(tmp_path: Path):
    resume_dir = tmp_path / "resumes"
    resume_dir.mkdir()
    resume = resume_dir / "backend_2026.txt"
    resume.write_text("Backend engineer using Python and SQL", encoding="utf-8")
    config = tmp_path / "preferences.yaml"
    config.write_text(
        """shortlist_threshold: 50
job_preferences:
  preferred_job_terms: [Backend Engineer]
""",
        encoding="utf-8",
    )

    preferences = load_preferences(config)
    profiles = load_resume_profiles(preferences, resume_dir)

    assert profiles[0].path == resume.resolve()
    assert profiles[0].file_hash
    assert "Python" in profiles[0].sanitized_summary
    assert profiles[0].preferences.preferred_job_terms == ["Backend Engineer"]


def test_active_loader_discovers_unconfigured_resume_and_rejects_external_symlink(tmp_path: Path):
    resume_dir = tmp_path / "resumes"
    resume_dir.mkdir()
    valid = resume_dir / "unconfigured.txt"
    valid.write_text("Python backend engineer", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("Should not be loaded", encoding="utf-8")
    (resume_dir / "external.txt").symlink_to(outside)
    (resume_dir / "notes.md").write_text("Not a resume type", encoding="utf-8")
    preferences_file = tmp_path / "preferences.yaml"
    preferences_file.write_text(
        "job_preferences: {}\n",
        encoding="utf-8",
    )
    preferences = load_preferences(preferences_file)
    progress_updates = []

    report = load_active_resume_profiles(preferences, resume_dir, progress_updates.append)

    assert [profile.name for profile in report.profiles] == ["unconfigured"]
    assert report.profiles[0].preferences.preferred_job_terms == []
    assert len(report.errors) == 1
    assert "resolves outside" in report.errors[0]
    resume_updates = [update for update in progress_updates if update.key == "resumes"]
    assert resume_updates[-1].completed == 2
    assert resume_updates[-1].finished is True


def test_strict_loader_reports_empty_resume_directory(tmp_path: Path):
    resume_dir = tmp_path / "resumes"
    resume_dir.mkdir()
    preferences_file = tmp_path / "preferences.yaml"
    preferences_file.write_text("job_preferences: {}\n", encoding="utf-8")

    preferences = load_preferences(preferences_file)

    try:
        load_resume_profiles(preferences, resume_dir)
    except FileNotFoundError as exc:
        assert "No supported resumes found" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("Expected the empty resume directory to be rejected")
