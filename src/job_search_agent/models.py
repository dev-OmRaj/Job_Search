from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl, field_validator

WorkMode = Literal["remote", "hybrid", "onsite", "unknown"]
InternshipPreference = Literal["include", "prefer", "exclude", "only"]


class CrawlerConfig(BaseModel):
    max_pages: int = Field(default=100, ge=1, le=1000)
    max_depth: int = Field(default=2, ge=0, le=5)
    request_timeout_seconds: float = Field(default=20, gt=0, le=120)
    delay_seconds: float = Field(default=0.5, ge=0, le=10)
    use_playwright_fallback: bool = True


class JobPreferences(BaseModel):
    preferred_job_terms: list[str] = Field(default_factory=list)
    preferred_locations: list[str] = Field(default_factory=list)
    require_preferred_location: bool = False
    allow_remote_outside_preferred_locations: bool = True
    preferred_work_modes: list[WorkMode] = Field(default_factory=list)
    preferred_employment_types: list[str] = Field(default_factory=list)
    preferred_experience_levels: list[str] = Field(default_factory=list)
    maximum_required_experience_years: int | None = Field(default=None, ge=0, le=50)
    reject_unknown_experience: bool = False
    internship_preference: InternshipPreference = "include"
    excluded_title_terms: list[str] = Field(default_factory=list)
    excluded_locations: list[str] = Field(default_factory=list)


class ProjectPreferences(BaseModel):
    shortlist_threshold: float = Field(default=50, ge=0, le=100)
    job_preferences: JobPreferences = Field(default_factory=JobPreferences)
    crawler: CrawlerConfig = Field(default_factory=CrawlerConfig)


class ResumeProfile(BaseModel):
    name: str
    path: Path
    file_hash: str
    sanitized_summary: str
    preferences: JobPreferences

    @property
    def embedding_text(self) -> str:
        return f"Resume: {self.sanitized_summary}"


class PageDocument(BaseModel):
    url: HttpUrl
    source_url: HttpUrl
    html: str
    text: str
    depth: int = 0


class JobPosting(BaseModel):
    title: str
    company: str = ""
    location: str = ""
    work_mode: WorkMode = "unknown"
    description: str = ""
    description_complete: bool = True
    url: str
    source_url: str
    external_id: str | None = None
    employment_type: str | None = None
    experience_level: str | None = None
    salary: str | None = None
    skills: list[str] = Field(default_factory=list)
    posted_at: str | None = None

    @field_validator("title", "url")
    @classmethod
    def required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value cannot be empty")
        return value

    @property
    def embedding_text(self) -> str:
        return "\n".join(
            part
            for part in (
                self.title,
                self.company,
                self.location,
                self.work_mode,
                self.employment_type or "",
                ", ".join(self.skills),
                self.description,
            )
            if part
        )

    @property
    def identity_key(self) -> str:
        stable_reference = self.external_id or self.url.rstrip("/")
        raw = f"{self.company.lower()}|{stable_reference.lower()}|{self.title.lower()}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class LLMFitAssessment(BaseModel):
    fit_score: float = Field(ge=0, le=20)
    rationale: str
    strengths: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)


class JobMatch(BaseModel):
    job: JobPosting
    profile_name: str
    semantic_score: float = Field(ge=0, le=50)
    preference_score: float = Field(ge=0, le=30)
    llm_score: float = Field(ge=0, le=20)
    total_score: float = Field(ge=0, le=100)
    shortlisted: bool
    hard_rejection_reason: str | None = None
    rationale: str = ""
    strengths: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)


class ScanResult(BaseModel):
    scan_id: int
    source_url: str
    jobs_found: int
    matches_evaluated: int
    shortlisted_jobs: int
    export_files: list[Path]
    matches: list[JobMatch] = Field(default_factory=list, exclude=True)
    errors: list[str] = Field(default_factory=list)
    pages_scanned: int = 0
    skipped_pages: list[str] = Field(default_factory=list, exclude=True)
    portal_reported_jobs: int | None = None
    result_cards_scanned: int | None = None
    duplicate_result_cards: int | None = None
    coverage_complete: bool | None = None
    completed_at: datetime = Field(default_factory=datetime.now)
