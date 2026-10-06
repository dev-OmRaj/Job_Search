from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass, field
from typing import Protocol

import numpy as np
from langchain_ollama import OllamaEmbeddings

from .llm import OllamaCloudGateway
from .locations import is_india_location
from .models import JobMatch, JobPosting, ResumeProfile
from .progress import ProgressCallback, emit_progress

TOKEN_RE = re.compile(r"[a-z0-9+#.]+")
EXPERIENCE_RANGE_PATTERN = re.compile(
    r"\b(\d{1,2})\s*(?:-|–|—|to)\s*\d{1,2}\s*(?:years?|yrs?)\b",
    re.IGNORECASE,
)
EXPERIENCE_PATTERNS = (
    re.compile(r"\b(\d{1,2})\s*\+\s*(?:years?|yrs?)\b", re.IGNORECASE),
    re.compile(
        r"\b(?:at\s+least|minimum(?:\s+of)?|min(?:imum)?\.?)\s*"
        r"(\d{1,2})\s*(?:years?|yrs?)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(\d{1,2})\s*(?:years?|yrs?)\s+(?:of\s+)?[^.;\n]{0,120}?experience\b",
        re.IGNORECASE,
    ),
)
SENIOR_TITLE_TERMS = {
    "director",
    "head",
    "lead",
    "manager",
    "principal",
    "senior",
    "sr",
    "staff",
    "vp",
}
LOCATION_ALIASES = {
    "united states": {"us", "usa"},
    "united kingdom": {"gb", "gbr", "uk"},
}
REMOTE_MARKERS = ("remote", "virtual", "work from home", "wfh")
UNKNOWN_LOCATION_VALUES = {
    "",
    "n/a",
    "na",
    "not available",
    "not specified",
    "tbd",
    "to be determined",
    "unknown",
    "unspecified",
}


@dataclass(slots=True)
class RankingResult:
    matches: list[JobMatch]
    profile_embeddings: dict[str, list[float]]
    job_embeddings: dict[str, list[float]]
    errors: list[str] = field(default_factory=list)


class EmbeddingCache(Protocol):
    def load_many(self, texts: list[str]) -> list[list[float] | None]: ...

    def save_many(self, items: list[tuple[str, list[float]]]) -> None: ...


def _tokens(value: str) -> set[str]:
    return set(TOKEN_RE.findall(value.lower()))


def _phrase_match(phrase: str, haystack: str) -> bool:
    phrase_tokens = TOKEN_RE.findall(phrase.lower())
    haystack_tokens = TOKEN_RE.findall(haystack.lower())
    if not phrase_tokens:
        return False
    width = len(phrase_tokens)
    return any(
        haystack_tokens[index : index + width] == phrase_tokens
        for index in range(len(haystack_tokens) - width + 1)
    )


def _best_job_term_match(preferred_terms: list[str], title: str) -> float:
    if not preferred_terms:
        return 0
    title_tokens = _tokens(title)
    scores: list[float] = []
    for term in preferred_terms:
        if _phrase_match(term, title) or _phrase_match(title, term):
            scores.append(1.0)
            continue
        term_tokens = _tokens(term)
        union = term_tokens | title_tokens
        scores.append(len(term_tokens & title_tokens) / len(union) if union else 0)
    return max(scores, default=0)


def _is_internship(job: JobPosting) -> bool:
    tokens = _tokens(f"{job.title} {job.employment_type or ''}")
    return bool(tokens & {"intern", "internship", "internships"})


def minimum_required_experience_years(job: JobPosting) -> int | None:
    """Return the strongest explicit minimum experience requirement in a job."""

    text = f"{job.experience_level or ''}\n{job.description}"
    requirements = [int(match.group(1)) for match in EXPERIENCE_RANGE_PATTERN.finditer(text)]
    text_without_ranges = EXPERIENCE_RANGE_PATTERN.sub("", text)
    requirements.extend(
        int(match.group(1))
        for pattern in EXPERIENCE_PATTERNS
        for match in pattern.finditer(text_without_ranges)
    )
    return max(requirements, default=None)


def _is_remote_job(job: JobPosting) -> bool:
    if job.work_mode == "remote":
        return True
    text = f"{job.title} {job.location} {job.description}".casefold()
    normalized = " ".join(re.findall(r"[a-z0-9]+", text))
    padded = f" {normalized} "
    return any(f" {marker} " in padded for marker in REMOTE_MARKERS)


def _location_matches(preferred_location: str, actual_location: str) -> bool:
    if _phrase_match(preferred_location, actual_location):
        return True
    if preferred_location.casefold().strip() == "india":
        return is_india_location(actual_location)
    aliases = LOCATION_ALIASES.get(preferred_location.casefold().strip(), set())
    return bool(aliases & _tokens(actual_location))


def _has_known_location(location: str) -> bool:
    normalized = " ".join(location.casefold().split())
    return normalized not in UNKNOWN_LOCATION_VALUES


def hard_rejection_reason(profile: ResumeProfile, job: JobPosting) -> str | None:
    if not job.description_complete:
        return "full job description is unavailable; fit cannot be assessed safely"
    preferences = profile.preferences
    for term in preferences.excluded_title_terms:
        if _phrase_match(term, job.title):
            return f"title contains excluded term {term!r}"
    for location in preferences.excluded_locations:
        if _phrase_match(location, job.location):
            return f"location contains excluded term {location!r}"
    if (
        preferences.require_preferred_location
        and preferences.preferred_locations
        and _has_known_location(job.location)
        and not (preferences.allow_remote_outside_preferred_locations and _is_remote_job(job))
        and not any(
            _location_matches(location, job.location)
            for location in preferences.preferred_locations
        )
    ):
        return f"location {job.location!r} is outside the configured preferred locations"
    maximum_years = preferences.maximum_required_experience_years
    required_years = minimum_required_experience_years(job)
    if maximum_years is not None:
        if required_years is not None and required_years > maximum_years:
            return (
                f"requires at least {required_years} years of experience; "
                f"configured maximum is {maximum_years}"
            )
        senior_terms = _tokens(job.title) & SENIOR_TITLE_TERMS
        if maximum_years <= 2 and senior_terms:
            senior_term = min(senior_terms)
            return f"title indicates a {senior_term!r} role above the configured junior level"
        if required_years is None and preferences.reject_unknown_experience:
            return "required experience could not be determined"
    is_internship = _is_internship(job)
    if preferences.internship_preference == "exclude" and is_internship:
        return "internships are excluded"
    if preferences.internship_preference == "only" and not is_internship:
        return "only internships are allowed"
    return None


def preference_score(profile: ResumeProfile, job: JobPosting) -> float:
    """Return an explicit-preference score scaled to 30 points."""

    preferences = profile.preferences
    categories: list[tuple[float, float]] = []

    if preferences.preferred_job_terms:
        categories.append((8, _best_job_term_match(preferences.preferred_job_terms, job.title)))
    if preferences.preferred_locations:
        matched = _is_remote_job(job) or any(
            _location_matches(location, job.location)
            for location in preferences.preferred_locations
        )
        categories.append((6, float(matched)))
    if preferences.preferred_work_modes:
        categories.append((5, float(job.work_mode in preferences.preferred_work_modes)))
    if preferences.preferred_employment_types:
        employment_type = job.employment_type or ""
        matched = max(
            (
                _phrase_match(kind, employment_type)
                for kind in preferences.preferred_employment_types
            ),
            default=False,
        )
        categories.append((5, float(matched)))
    if preferences.preferred_experience_levels:
        experience_text = f"{job.experience_level or ''} {job.title}"
        matched = max(
            (
                _phrase_match(level, experience_text)
                for level in preferences.preferred_experience_levels
            ),
            default=False,
        )
        categories.append((3, float(matched)))
    if preferences.internship_preference == "prefer":
        categories.append((3, float(_is_internship(job))))

    if not categories:
        return 15.0
    available_weight = sum(weight for weight, _ in categories)
    earned = sum(weight * fraction for weight, fraction in categories)
    return round(30 * earned / available_weight, 2)


def cosine_similarity(left: list[float], right: list[float]) -> float:
    left_array = np.asarray(left, dtype=np.float32)
    right_array = np.asarray(right, dtype=np.float32)
    denominator = float(np.linalg.norm(left_array) * np.linalg.norm(right_array))
    if denominator == 0 or not math.isfinite(denominator):
        return 0.0
    return float(np.dot(left_array, right_array) / denominator)


class HybridRanker:
    def __init__(
        self,
        *,
        embeddings: OllamaEmbeddings,
        cloud: OllamaCloudGateway,
        shortlist_threshold: float,
        progress: ProgressCallback | None = None,
        embedding_batch_size: int = 32,
        embedding_max_retries: int = 2,
        embedding_retry_delay_seconds: float = 1.0,
        embedding_cache: EmbeddingCache | None = None,
    ):
        if embedding_batch_size < 1:
            raise ValueError("embedding_batch_size must be at least 1")
        if embedding_max_retries < 0:
            raise ValueError("embedding_max_retries cannot be negative")
        if embedding_retry_delay_seconds < 0:
            raise ValueError("embedding_retry_delay_seconds cannot be negative")
        self.embeddings = embeddings
        self.cloud = cloud
        self.shortlist_threshold = shortlist_threshold
        self.progress = progress
        self.embedding_batch_size = embedding_batch_size
        self.embedding_max_retries = embedding_max_retries
        self.embedding_retry_delay_seconds = embedding_retry_delay_seconds
        self.embedding_cache = embedding_cache

    @staticmethod
    def _validated_vectors(
        vectors: object,
        *,
        expected: int,
    ) -> list[list[float]]:
        if not isinstance(vectors, (list, tuple)) or len(vectors) != expected:
            actual = len(vectors) if isinstance(vectors, (list, tuple)) else "invalid"
            raise ValueError(f"embedding provider returned {actual} vectors for {expected} inputs")
        normalized: list[list[float]] = []
        for vector in vectors:
            if not isinstance(vector, (list, tuple)):
                raise ValueError("embedding provider returned an invalid vector")
            values = [float(value) for value in vector]
            if not values or not all(math.isfinite(value) for value in values):
                raise ValueError("embedding provider returned an empty or non-finite vector")
            normalized.append(values)
        return normalized

    def _embed_texts(
        self,
        texts: list[str],
        *,
        kind: str,
        completed_before: int,
        total: int,
        errors: list[str],
    ) -> list[list[float]]:
        results: list[list[float] | None] = [None] * len(texts)
        batch_count = math.ceil(len(texts) / self.embedding_batch_size)
        for batch_number, start in enumerate(
            range(0, len(texts), self.embedding_batch_size),
            start=1,
        ):
            stop = min(start + self.embedding_batch_size, len(texts))
            batch_texts = texts[start:stop]
            cached: list[list[float] | None] = [None] * len(batch_texts)
            if self.embedding_cache is not None:
                try:
                    loaded = self.embedding_cache.load_many(batch_texts)
                    if len(loaded) != len(batch_texts):
                        raise ValueError("embedding cache returned an unexpected item count")
                    cached = loaded
                except Exception as exc:
                    errors.append(f"Embedding cache lookup failed for {kind} batch: {exc}")

            missing_indexes = [index for index, vector in enumerate(cached) if vector is None]
            if missing_indexes:
                missing_texts = [batch_texts[index] for index in missing_indexes]
                generated: list[list[float]] | None = None
                for attempt in range(self.embedding_max_retries + 1):
                    try:
                        generated = self._validated_vectors(
                            self.embeddings.embed_documents(missing_texts),
                            expected=len(missing_texts),
                        )
                        break
                    except Exception as exc:
                        if attempt >= self.embedding_max_retries:
                            raise RuntimeError(
                                f"Local embedding {kind} batch {batch_number}/{batch_count} "
                                f"failed after {attempt + 1} attempts; completed batches are "
                                f"checkpointed and will be reused on the next scan: {exc}"
                            ) from exc
                        retry_number = attempt + 1
                        emit_progress(
                            self.progress,
                            "embeddings",
                            f"Retrying {kind} embedding batch "
                            f"{batch_number}/{batch_count} "
                            f"({retry_number}/{self.embedding_max_retries})",
                            completed=completed_before + start,
                            total=total,
                        )
                        delay = self.embedding_retry_delay_seconds * retry_number
                        if delay:
                            time.sleep(delay)
                if generated is None:  # pragma: no cover - guarded by retry loop
                    raise RuntimeError("embedding provider returned no result")
                for index, vector in zip(missing_indexes, generated, strict=True):
                    cached[index] = vector
                if self.embedding_cache is not None:
                    try:
                        self.embedding_cache.save_many(
                            [
                                (batch_texts[index], vector)
                                for index, vector in zip(
                                    missing_indexes,
                                    generated,
                                    strict=True,
                                )
                            ]
                        )
                    except Exception as exc:
                        errors.append(f"Embedding checkpoint failed for {kind} batch: {exc}")

            for index, vector in enumerate(cached, start=start):
                if vector is None:  # pragma: no cover - defensive cache/provider check
                    raise RuntimeError("embedding batch completed without a vector")
                results[index] = vector
            emit_progress(
                self.progress,
                "embeddings",
                f"Generating local embeddings — {kind} batch {batch_number}/{batch_count}",
                completed=completed_before + stop,
                total=total,
            )

        return [vector for vector in results if vector is not None]

    def rank(self, profiles: list[ResumeProfile], jobs: list[JobPosting]) -> RankingResult:
        if not profiles or not jobs:
            emit_progress(
                self.progress,
                "matching",
                "No resume/job comparisons needed",
                completed=0,
                total=0,
                finished=True,
            )
            return RankingResult(matches=[], profile_embeddings={}, job_embeddings={})

        errors: list[str] = []
        rejections = [[hard_rejection_reason(profile, job) for job in jobs] for profile in profiles]
        eligible_profile_indexes = [
            profile_index
            for profile_index in range(len(profiles))
            if any(rejection is None for rejection in rejections[profile_index])
        ]
        eligible_job_indexes = [
            job_index
            for job_index in range(len(jobs))
            if any(
                rejections[profile_index][job_index] is None
                for profile_index in range(len(profiles))
            )
        ]
        skipped_jobs = len(jobs) - len(eligible_job_indexes)
        embedding_total = len(eligible_profile_indexes) + len(eligible_job_indexes)
        description = "Generating local embeddings"
        if skipped_jobs:
            description += f" ({skipped_jobs} hard-rejected jobs skipped)"
        emit_progress(
            self.progress,
            "embeddings",
            description,
            completed=0,
            total=embedding_total,
        )
        profile_vectors = self._embed_texts(
            [profiles[index].embedding_text for index in eligible_profile_indexes],
            kind="resume",
            completed_before=0,
            total=embedding_total,
            errors=errors,
        )
        job_vectors = self._embed_texts(
            [jobs[index].embedding_text for index in eligible_job_indexes],
            kind="job",
            completed_before=len(eligible_profile_indexes),
            total=embedding_total,
            errors=errors,
        )
        emit_progress(
            self.progress,
            "embeddings",
            "Generated local embeddings",
            completed=embedding_total,
            total=embedding_total,
            finished=True,
        )
        profile_embeddings = {
            profiles[index].name: vector
            for index, vector in zip(
                eligible_profile_indexes,
                profile_vectors,
                strict=True,
            )
        }
        job_embeddings = {
            jobs[index].identity_key: vector
            for index, vector in zip(
                eligible_job_indexes,
                job_vectors,
                strict=True,
            )
        }
        profile_vectors_by_index = dict(zip(eligible_profile_indexes, profile_vectors, strict=True))
        job_vectors_by_index = dict(zip(eligible_job_indexes, job_vectors, strict=True))

        matches: list[JobMatch] = []
        comparison_total = len(profiles) * len(jobs)
        comparisons_completed = 0
        emit_progress(
            self.progress,
            "matching",
            "Comparing resumes with jobs",
            completed=0,
            total=comparison_total,
        )
        for profile_index, profile in enumerate(profiles):
            for job_index, job in enumerate(jobs):
                rejection = rejections[profile_index][job_index]
                semantic = 0.0
                if rejection is None:
                    semantic = round(
                        max(
                            0.0,
                            min(
                                1.0,
                                cosine_similarity(
                                    profile_vectors_by_index[profile_index],
                                    job_vectors_by_index[job_index],
                                ),
                            ),
                        )
                        * 50,
                        2,
                    )
                explicit = preference_score(profile, job)
                local_score = semantic + explicit
                llm_score = 0.0
                rationale = ""
                strengths: list[str] = []
                gaps: list[str] = []

                if rejection:
                    rationale = f"Rejected: {rejection}."
                elif local_score >= self.shortlist_threshold - 20:
                    try:
                        assessment = self.cloud.assess_fit(profile, job)
                        llm_score = assessment.fit_score
                        rationale = assessment.rationale
                        strengths = assessment.strengths
                        gaps = assessment.gaps
                    except Exception as exc:
                        errors.append(
                            f"Cloud fit assessment failed for {profile.name!r} and {job.title!r}: {exc}"
                        )
                        rationale = (
                            "Cloud fit assessment was unavailable; score uses local evidence only."
                        )
                else:
                    rationale = (
                        "Skipped cloud assessment because the local score cannot reach the "
                        f"{self.shortlist_threshold:g}-point threshold."
                    )

                total = 0.0 if rejection else round(min(100, local_score + llm_score), 2)
                matches.append(
                    JobMatch(
                        job=job,
                        profile_name=profile.name,
                        semantic_score=semantic,
                        preference_score=explicit,
                        llm_score=llm_score,
                        total_score=total,
                        shortlisted=not rejection and total >= self.shortlist_threshold,
                        hard_rejection_reason=rejection,
                        rationale=rationale,
                        strengths=strengths,
                        gaps=gaps,
                    )
                )
                comparisons_completed += 1
                emit_progress(
                    self.progress,
                    "matching",
                    "Comparing resumes with jobs",
                    completed=comparisons_completed,
                    total=comparison_total,
                    finished=comparisons_completed == comparison_total,
                )
        matches.sort(key=lambda item: item.total_score, reverse=True)
        return RankingResult(
            matches=matches,
            profile_embeddings=profile_embeddings,
            job_embeddings=job_embeddings,
            errors=errors,
        )
