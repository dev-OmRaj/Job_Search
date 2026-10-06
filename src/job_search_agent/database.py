from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    delete,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship

from .models import JobPosting, ResumeProfile
from .ranking import RankingResult


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class ResumeSyncResult:
    active: int
    removed: int
    replaced: int


class Base(DeclarativeBase):
    pass


class SourceRecord(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    url: Mapped[str] = mapped_column(Text, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ScanRecord(Base):
    __tablename__ = "scans"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    jobs_found: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)


class ResumeRecord(Base):
    __tablename__ = "resume_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    path: Mapped[str] = mapped_column(Text)
    file_hash: Mapped[str] = mapped_column(String(64), index=True)
    sanitized_summary: Mapped[str] = mapped_column(Text)
    preferences_json: Mapped[str] = mapped_column(Text)
    embedding_json: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class JobRecord(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), index=True)
    identity_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    canonical_url: Mapped[str] = mapped_column(Text, index=True)
    title: Mapped[str] = mapped_column(Text, index=True)
    company: Mapped[str] = mapped_column(Text, default="")
    location: Mapped[str] = mapped_column(Text, default="")
    work_mode: Mapped[str] = mapped_column(String(20), default="unknown")
    description: Mapped[str] = mapped_column(Text, default="")
    external_id: Mapped[str | None] = mapped_column(Text)
    employment_type: Mapped[str | None] = mapped_column(Text)
    experience_level: Mapped[str | None] = mapped_column(Text)
    salary: Mapped[str | None] = mapped_column(Text)
    skills_json: Mapped[str] = mapped_column(Text, default="[]")
    posted_at: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    embedding_json: Mapped[str | None] = mapped_column(Text)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MatchRecord(Base):
    __tablename__ = "job_matches"

    id: Mapped[int] = mapped_column(primary_key=True)
    scan_id: Mapped[int] = mapped_column(ForeignKey("scans.id"), index=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)
    resume_profile_id: Mapped[int] = mapped_column(ForeignKey("resume_profiles.id"), index=True)
    semantic_score: Mapped[float] = mapped_column(Float)
    preference_score: Mapped[float] = mapped_column(Float)
    llm_score: Mapped[float] = mapped_column(Float)
    total_score: Mapped[float] = mapped_column(Float, index=True)
    shortlisted: Mapped[bool] = mapped_column(Boolean, index=True)
    hard_rejection_reason: Mapped[str | None] = mapped_column(Text)
    rationale: Mapped[str] = mapped_column(Text, default="")
    strengths_json: Mapped[str] = mapped_column(Text, default="[]")
    gaps_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    job: Mapped[JobRecord] = relationship()
    profile: Mapped[ResumeRecord] = relationship()


class EmbeddingCacheRecord(Base):
    __tablename__ = "embedding_cache"

    cache_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    model: Mapped[str] = mapped_column(String(250), index=True)
    text_hash: Mapped[str] = mapped_column(String(64), index=True)
    embedding_json: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Database:
    def __init__(self, url: str):
        if url.startswith("sqlite:///"):
            db_path = Path(url.removeprefix("sqlite:///"))
            db_path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(url)

    def create_schema(self) -> None:
        Base.metadata.create_all(self.engine)

    @staticmethod
    def _embedding_cache_key(model: str, text: str) -> tuple[str, str]:
        text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        cache_key = hashlib.sha256(f"{model}\0{text_hash}".encode()).hexdigest()
        return cache_key, text_hash

    def load_cached_embeddings(
        self,
        model: str,
        texts: list[str],
    ) -> list[list[float] | None]:
        """Load cached vectors in the same order as the supplied input texts."""

        keys = [self._embedding_cache_key(model, text)[0] for text in texts]
        if not keys:
            return []
        with Session(self.engine) as session:
            rows = session.execute(
                select(
                    EmbeddingCacheRecord.cache_key,
                    EmbeddingCacheRecord.embedding_json,
                ).where(EmbeddingCacheRecord.cache_key.in_(keys))
            ).all()
        stored = {key: payload for key, payload in rows}
        vectors: list[list[float] | None] = []
        for key in keys:
            payload = stored.get(key)
            if payload is None:
                vectors.append(None)
                continue
            try:
                vector = json.loads(payload)
                vectors.append([float(value) for value in vector])
            except (TypeError, ValueError, json.JSONDecodeError):
                vectors.append(None)
        return vectors

    def save_cached_embeddings(
        self,
        model: str,
        items: list[tuple[str, list[float]]],
    ) -> None:
        """Commit one completed embedding batch for use by retries and later scans."""

        if not items:
            return
        with Session(self.engine) as session:
            for text, vector in items:
                cache_key, text_hash = self._embedding_cache_key(model, text)
                record = session.get(EmbeddingCacheRecord, cache_key)
                if record is None:
                    record = EmbeddingCacheRecord(
                        cache_key=cache_key,
                        model=model,
                        text_hash=text_hash,
                    )
                    session.add(record)
                record.embedding_json = json.dumps(vector)
                record.updated_at = utcnow()
            session.commit()

    def start_scan(self, source_url: str) -> int:
        with Session(self.engine) as session:
            source = session.scalar(select(SourceRecord).where(SourceRecord.url == source_url))
            if source is None:
                source = SourceRecord(url=source_url)
                session.add(source)
                session.flush()
            scan = ScanRecord(source_id=source.id)
            session.add(scan)
            session.commit()
            return scan.id

    def finish_scan(
        self, scan_id: int, *, jobs_found: int, error: str | None = None,
        warnings: list[str] | None = None,
        partial: bool = False,
    ) -> None:
        with Session(self.engine) as session:
            scan = session.get(ScanRecord, scan_id)
            if scan is None:
                raise LookupError(f"Scan {scan_id} was not found")
            scan.status = "failed" if error else ("partial" if partial else "completed")
            scan.completed_at = utcnow()
            scan.jobs_found = jobs_found
            scan.error = error or ("Warnings: " + "\n".join(warnings) if warnings else None)
            source = session.get(SourceRecord, scan.source_id)
            if source is not None:
                source.last_scanned_at = utcnow()
            session.commit()

    @staticmethod
    def _job_hash(job: JobPosting) -> str:
        payload = job.model_dump_json(exclude={"source_url"})
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _synchronize_resume_profiles(
        self,
        session: Session,
        profiles: list[ResumeProfile],
        embeddings: dict[str, list[float]] | None = None,
    ) -> tuple[dict[str, ResumeRecord], ResumeSyncResult]:
        active_by_name = {profile.name: profile for profile in profiles}
        stored = {record.name: record for record in session.scalars(select(ResumeRecord)).all()}
        removed = 0
        replaced = 0

        for name, record in stored.items():
            if name in active_by_name:
                continue
            session.execute(delete(MatchRecord).where(MatchRecord.resume_profile_id == record.id))
            session.delete(record)
            removed += 1
        session.flush()

        records: dict[str, ResumeRecord] = {}
        for profile in profiles:
            record = stored.get(profile.name)
            is_replacement = record is not None and record.file_hash != profile.file_hash
            if record is None:
                record = ResumeRecord(name=profile.name)
                session.add(record)
            elif is_replacement:
                session.execute(
                    delete(MatchRecord).where(MatchRecord.resume_profile_id == record.id)
                )
                record.embedding_json = None
                replaced += 1
            record.path = str(profile.path)
            record.file_hash = profile.file_hash
            record.sanitized_summary = profile.sanitized_summary
            record.preferences_json = profile.preferences.model_dump_json()
            if embeddings is not None:
                vector = embeddings.get(profile.name)
                record.embedding_json = json.dumps(vector) if vector is not None else None
            record.updated_at = utcnow()
            records[profile.name] = record
        session.flush()
        return records, ResumeSyncResult(
            active=len(profiles),
            removed=removed,
            replaced=replaced,
        )

    def synchronize_resume_profiles(self, profiles: list[ResumeProfile]) -> ResumeSyncResult:
        """Remove stale resume metadata and match history, then register active profiles."""

        with Session(self.engine) as session:
            _, result = self._synchronize_resume_profiles(session, profiles)
            session.commit()
            return result

    def persist_ranking(
        self,
        *,
        scan_id: int,
        profiles: list[ResumeProfile],
        result: RankingResult,
    ) -> None:
        with Session(self.engine) as session:
            scan = session.get(ScanRecord, scan_id)
            if scan is None:
                raise LookupError(f"Scan {scan_id} was not found")

            profile_records, _ = self._synchronize_resume_profiles(
                session,
                profiles,
                result.profile_embeddings,
            )

            job_records: dict[str, JobRecord] = {}
            unique_jobs = {match.job.identity_key: match.job for match in result.matches}
            for job in unique_jobs.values():
                record = session.scalar(
                    select(JobRecord).where(JobRecord.identity_key == job.identity_key)
                )
                if record is None:
                    record = JobRecord(
                        source_id=scan.source_id,
                        identity_key=job.identity_key,
                        first_seen_at=utcnow(),
                    )
                    session.add(record)
                record.canonical_url = job.url.rstrip("/")
                record.title = job.title
                record.company = job.company
                record.location = job.location
                record.work_mode = job.work_mode
                # A transient ATS detail failure must not erase a full description
                # collected by an earlier successful scan.
                if job.description.strip() or not record.description:
                    record.description = job.description
                record.external_id = job.external_id
                record.employment_type = job.employment_type
                record.experience_level = job.experience_level
                record.salary = job.salary
                record.skills_json = json.dumps(job.skills)
                record.posted_at = job.posted_at
                record.content_hash = self._job_hash(job)
                vector = result.job_embeddings.get(job.identity_key)
                record.embedding_json = json.dumps(vector) if vector is not None else None
                record.last_seen_at = utcnow()
                job_records[job.identity_key] = record
            session.flush()

            for match in result.matches:
                session.add(
                    MatchRecord(
                        scan_id=scan_id,
                        job_id=job_records[match.job.identity_key].id,
                        resume_profile_id=profile_records[match.profile_name].id,
                        semantic_score=match.semantic_score,
                        preference_score=match.preference_score,
                        llm_score=match.llm_score,
                        total_score=match.total_score,
                        shortlisted=match.shortlisted,
                        hard_rejection_reason=match.hard_rejection_reason,
                        rationale=match.rationale,
                        strengths_json=json.dumps(match.strengths),
                        gaps_json=json.dumps(match.gaps),
                    )
                )
            session.commit()

    def list_matches(
        self,
        *,
        shortlisted_only: bool = False,
        latest_scan_only: bool = False,
    ) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            statement = (
                select(MatchRecord, JobRecord, ResumeRecord)
                .join(JobRecord, MatchRecord.job_id == JobRecord.id)
                .join(ResumeRecord, MatchRecord.resume_profile_id == ResumeRecord.id)
                .order_by(MatchRecord.created_at.desc(), MatchRecord.total_score.desc())
            )
            if shortlisted_only:
                statement = statement.where(MatchRecord.shortlisted.is_(True))
            if latest_scan_only:
                latest_scan_id = session.scalar(
                    select(ScanRecord.id)
                    .where(ScanRecord.status.in_(["completed", "partial"]))
                    .order_by(ScanRecord.id.desc())
                    .limit(1)
                )
                if latest_scan_id is None:
                    return []
                statement = statement.where(MatchRecord.scan_id == latest_scan_id)
            rows = session.execute(statement).all()
            return [
                {
                    "scan_id": match.scan_id,
                    "job_identity": job.identity_key,
                    "job_title": job.title,
                    "company": job.company,
                    "location": job.location,
                    "url": job.canonical_url,
                    "profile": profile.name,
                    "score": match.total_score,
                    "shortlisted": match.shortlisted,
                    "rationale": match.rationale,
                }
                for match, job, profile in rows
            ]

    def list_company_portals(self) -> list[dict[str, Any]]:
        """Return source URLs and discovered company names for directory backfills."""

        with Session(self.engine) as session:
            rows = session.execute(
                select(SourceRecord.url, JobRecord.company)
                .join(JobRecord, JobRecord.source_id == SourceRecord.id)
                .order_by(SourceRecord.url)
            ).all()
        grouped: dict[str, list[str]] = {}
        for portal_url, company_name in rows:
            grouped.setdefault(portal_url, [])
            if company_name:
                grouped[portal_url].append(company_name)
        return [
            {"job_portal_url": portal_url, "company_names": company_names}
            for portal_url, company_names in grouped.items()
        ]

    def list_profiles(self) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            records = session.scalars(select(ResumeRecord).order_by(ResumeRecord.name)).all()
            return [
                {
                    "name": item.name,
                    "path": item.path,
                    "file_hash": item.file_hash,
                    "updated_at": item.updated_at,
                }
                for item in records
            ]


class DatabaseEmbeddingCache:
    """Model-scoped cache adapter used by the ranking service."""

    def __init__(self, database: Database, model: str):
        self.database = database
        self.model = model

    def load_many(self, texts: list[str]) -> list[list[float] | None]:
        return self.database.load_cached_embeddings(self.model, texts)

    def save_many(self, items: list[tuple[str, list[float]]]) -> None:
        self.database.save_cached_embeddings(self.model, items)
