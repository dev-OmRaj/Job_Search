from __future__ import annotations

import csv
import json
from datetime import UTC, datetime
from pathlib import Path

from .models import JobMatch


def best_match_per_job(matches: list[JobMatch]) -> list[JobMatch]:
    best: dict[str, JobMatch] = {}
    for match in matches:
        current = best.get(match.job.identity_key)
        if current is None or match.total_score > current.total_score:
            best[match.job.identity_key] = match
    return sorted(best.values(), key=lambda item: item.total_score, reverse=True)


def shortlisted_best_matches(matches: list[JobMatch]) -> list[JobMatch]:
    return [match for match in best_match_per_job(matches) if match.shortlisted]


def _write_csv(matches: list[JobMatch], path: Path) -> None:
    fieldnames = [
        "job_title",
        "company",
        "location",
        "work_mode",
        "description",
        "job_url",
        "source_url",
        "external_id",
        "employment_type",
        "experience_level",
        "salary",
        "skills",
        "posted_at",
        "profile",
        "semantic_score",
        "preference_score",
        "llm_score",
        "total_score",
        "shortlisted",
        "hard_rejection_reason",
        "rationale",
        "strengths",
        "gaps",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for match in matches:
            writer.writerow(
                {
                    "job_title": match.job.title,
                    "company": match.job.company,
                    "location": match.job.location,
                    "work_mode": match.job.work_mode,
                    "description": match.job.description,
                    "job_url": match.job.url,
                    "source_url": match.job.source_url,
                    "external_id": match.job.external_id or "",
                    "employment_type": match.job.employment_type or "",
                    "experience_level": match.job.experience_level or "",
                    "salary": match.job.salary or "",
                    "skills": "; ".join(match.job.skills),
                    "posted_at": match.job.posted_at or "",
                    "profile": match.profile_name,
                    "semantic_score": match.semantic_score,
                    "preference_score": match.preference_score,
                    "llm_score": match.llm_score,
                    "total_score": match.total_score,
                    "shortlisted": match.shortlisted,
                    "hard_rejection_reason": match.hard_rejection_reason or "",
                    "rationale": match.rationale,
                    "strengths": "; ".join(match.strengths),
                    "gaps": "; ".join(match.gaps),
                }
            )


def _write_json(
    matches: list[JobMatch],
    path: Path,
    scan_id: int,
    generated_at: str,
    *,
    collection_name: str,
    partial: bool = False,
    warnings: list[str] | None = None,
) -> None:
    payload = {
        "scan_id": scan_id,
        "generated_at": generated_at,
        "scan_status": "partial" if partial else "completed",
        "warnings": warnings or [],
        collection_name: [match.model_dump(mode="json") for match in matches],
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def export_matches(
    matches: list[JobMatch],
    export_dir: Path,
    scan_id: int,
    *,
    archive: bool = False,
    partial: bool = False,
    warnings: list[str] | None = None,
) -> list[Path]:
    """Overwrite current all-job and shortlisted exports, with optional snapshots."""

    export_dir.mkdir(parents=True, exist_ok=True)
    all_jobs = best_match_per_job(matches)
    shortlisted = [match for match in all_jobs if match.shortlisted]
    generated_at = datetime.now(UTC)
    all_csv = export_dir / "latest-all-jobs.csv"
    all_json = export_dir / "latest-all-jobs.json"
    shortlisted_csv = export_dir / "latest-shortlisted.csv"
    shortlisted_json = export_dir / "latest-shortlisted.json"
    _write_csv(all_jobs, all_csv)
    _write_json(
        all_jobs,
        all_json,
        scan_id,
        generated_at.isoformat(),
        collection_name="all_jobs",
        partial=partial,
        warnings=warnings,
    )
    _write_csv(shortlisted, shortlisted_csv)
    _write_json(
        shortlisted,
        shortlisted_json,
        scan_id,
        generated_at.isoformat(),
        collection_name="shortlisted_jobs",
        partial=partial,
        warnings=warnings,
    )
    paths = [all_csv, all_json, shortlisted_csv, shortlisted_json]

    if archive:
        timestamp = generated_at.strftime("%Y%m%dT%H%M%SZ")
        for label, exported_matches, collection_name in (
            ("all-jobs", all_jobs, "all_jobs"),
            ("shortlisted", shortlisted, "shortlisted_jobs"),
        ):
            archive_base = export_dir / f"scan-{scan_id}-{timestamp}-{label}"
            archive_csv = archive_base.with_suffix(".csv")
            archive_json = archive_base.with_suffix(".json")
            _write_csv(exported_matches, archive_csv)
            _write_json(
                exported_matches,
                archive_json,
                scan_id,
                generated_at.isoformat(),
                collection_name=collection_name,
                partial=partial,
                warnings=warnings,
            )
            paths.extend((archive_csv, archive_json))
    return paths
