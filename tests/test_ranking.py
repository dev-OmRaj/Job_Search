from pathlib import Path

import pytest

from job_search_agent.models import JobPosting, JobPreferences, LLMFitAssessment, ResumeProfile
from job_search_agent.ranking import (
    HybridRanker,
    hard_rejection_reason,
    minimum_required_experience_years,
    preference_score,
)


class FakeEmbeddings:
    def embed_documents(self, texts):
        vectors = []
        for text in texts:
            if "Unrelated cashier" in text:
                vectors.append([0.0, 1.0])
            else:
                vectors.append([1.0, 0.0])
        return vectors


class MemoryEmbeddingCache:
    def __init__(self):
        self.vectors = {}
        self.saved_batches = []

    def load_many(self, texts):
        return [self.vectors.get(text) for text in texts]

    def save_many(self, items):
        self.saved_batches.append(list(items))
        self.vectors.update(items)


class FlakyBatchEmbeddings:
    def __init__(self):
        self.calls = []
        self.failed_once = False

    def embed_documents(self, texts):
        self.calls.append(list(texts))
        if not self.failed_once and any("Backend Engineer 2" in text for text in texts):
            self.failed_once = True
            raise ConnectionError("temporary local Ollama runner reset")
        return [[1.0, 0.0] for _ in texts]


class UnexpectedEmbeddingCall:
    def embed_documents(self, texts):
        raise AssertionError(f"Cached text was embedded again: {texts!r}")


class PermanentlyFailingEmbeddings:
    def __init__(self):
        self.calls = []

    def embed_documents(self, texts):
        self.calls.append(list(texts))
        if any("Backend Engineer 3" in text for text in texts):
            raise ConnectionError("local Ollama runner reset")
        return [[1.0, 0.0] for _ in texts]


class FakeCloud:
    def __init__(self):
        self.calls = 0

    def assess_fit(self, profile, job):
        self.calls += 1
        return LLMFitAssessment(
            fit_score=15,
            rationale="Strong backend alignment.",
            strengths=["Python"],
            gaps=[],
        )


def make_profile() -> ResumeProfile:
    preferences = JobPreferences(
        preferred_job_terms=["Backend Engineer"],
        preferred_locations=["Remote"],
        preferred_work_modes=["remote"],
        preferred_employment_types=["full-time"],
        internship_preference="exclude",
    )
    return ResumeProfile(
        name="backend",
        path=Path("resume.txt"),
        file_hash="a" * 64,
        sanitized_summary="Backend engineer with Python and SQL.",
        preferences=preferences,
    )


def test_preference_scoring_and_hard_rejection():
    profile = make_profile()
    good = JobPosting(
        title="Backend Engineer",
        location="Remote",
        work_mode="remote",
        employment_type="FULL_TIME",
        description="Python and SQL",
        url="https://example.com/jobs/1",
        source_url="https://example.com/careers",
    )
    rejected = good.model_copy(update={"title": "Backend Intern"})

    assert preference_score(profile, good) == 30
    assert hard_rejection_reason(profile, rejected) is not None


def test_missing_job_description_is_rejected_before_scoring():
    profile = make_profile()
    job = JobPosting(
        title="Backend Engineer",
        location="Remote",
        work_mode="remote",
        employment_type="full-time",
        description="",
        description_complete=False,
        url="https://example.com/jobs/missing-description",
        source_url="https://example.com/careers",
    )
    cloud = FakeCloud()
    ranker = HybridRanker(
        embeddings=UnexpectedEmbeddingCall(),
        cloud=cloud,
        shortlist_threshold=50,
    )

    result = ranker.rank([profile], [job])

    assert cloud.calls == 0
    assert result.job_embeddings == {}
    assert result.matches[0].shortlisted is False
    assert "description is unavailable" in result.matches[0].hard_rejection_reason


def test_internship_policy_can_require_only_internships():
    profile = make_profile().model_copy(
        update={"preferences": JobPreferences(internship_preference="only")}
    )
    internship = JobPosting(
        title="Software Engineering Internship",
        employment_type="internship",
        url="https://example.com/jobs/intern",
        source_url="https://example.com/careers",
    )
    full_time = internship.model_copy(
        update={"title": "Software Engineer", "employment_type": "full-time"}
    )

    assert hard_rejection_reason(profile, internship) is None
    assert hard_rejection_reason(profile, full_time) == "only internships are allowed"


def test_cloud_is_only_called_when_pair_can_reach_threshold():
    profile = make_profile()
    likely = JobPosting(
        title="Backend Engineer",
        location="Remote",
        work_mode="remote",
        employment_type="full-time",
        description="Python and SQL services",
        url="https://example.com/jobs/1",
        source_url="https://example.com/careers",
    )
    unrelated = JobPosting(
        title="Store Clerk",
        description="Unrelated cashier retail role",
        url="https://example.com/jobs/2",
        source_url="https://example.com/careers",
    )
    cloud = FakeCloud()
    progress_updates = []
    ranker = HybridRanker(
        embeddings=FakeEmbeddings(),
        cloud=cloud,
        shortlist_threshold=50,
        progress=progress_updates.append,
    )

    result = ranker.rank([profile], [likely, unrelated])

    assert cloud.calls == 1
    assert result.matches[0].shortlisted is True
    low_match = next(match for match in result.matches if match.job.title == "Store Clerk")
    assert low_match.llm_score == 0
    assert "cannot reach" in low_match.rationale
    assert any(update.key == "embeddings" and update.finished for update in progress_updates)
    matching = [update for update in progress_updates if update.key == "matching"]
    assert matching[-1].completed == 2
    assert matching[-1].finished is True


def test_embeddings_are_batched_retried_checkpointed_and_reused():
    profile = make_profile()
    jobs = [
        JobPosting(
            title=f"Backend Engineer {index}",
            location="Remote",
            work_mode="remote",
            employment_type="full-time",
            description="Python services",
            url=f"https://example.com/jobs/{index}",
            source_url="https://example.com/careers",
        )
        for index in range(1, 4)
    ]
    embeddings = FlakyBatchEmbeddings()
    cache = MemoryEmbeddingCache()
    progress_updates = []
    ranker = HybridRanker(
        embeddings=embeddings,
        cloud=FakeCloud(),
        shortlist_threshold=50,
        progress=progress_updates.append,
        embedding_batch_size=2,
        embedding_max_retries=1,
        embedding_retry_delay_seconds=0,
        embedding_cache=cache,
    )

    result = ranker.rank([profile], jobs)

    assert [len(call) for call in embeddings.calls] == [1, 2, 2, 1]
    assert embeddings.calls[1] == embeddings.calls[2]
    assert len(cache.saved_batches) == 3
    assert len(result.job_embeddings) == 3
    assert any("Retrying job embedding batch" in update.description for update in progress_updates)
    embedding_updates = [update for update in progress_updates if update.key == "embeddings"]
    assert embedding_updates[-1].completed == 4
    assert embedding_updates[-1].finished is True

    cached_ranker = HybridRanker(
        embeddings=UnexpectedEmbeddingCall(),
        cloud=FakeCloud(),
        shortlist_threshold=50,
        embedding_batch_size=2,
        embedding_retry_delay_seconds=0,
        embedding_cache=cache,
    )
    cached_result = cached_ranker.rank([profile], jobs)

    assert len(cached_result.job_embeddings) == 3


def test_jobs_rejected_by_every_profile_are_not_embedded():
    profile = make_profile().model_copy(
        update={
            "preferences": JobPreferences(
                preferred_locations=["India"],
                require_preferred_location=True,
            )
        }
    )
    eligible = JobPosting(
        title="Data Scientist",
        location="Bengaluru, India",
        url="https://example.com/jobs/india",
        source_url="https://example.com/careers",
    )
    rejected = eligible.model_copy(
        update={
            "location": "Seattle, USA",
            "url": "https://example.com/jobs/usa",
        }
    )
    embeddings = FlakyBatchEmbeddings()
    progress_updates = []
    ranker = HybridRanker(
        embeddings=embeddings,
        cloud=FakeCloud(),
        shortlist_threshold=50,
        progress=progress_updates.append,
        embedding_batch_size=2,
        embedding_retry_delay_seconds=0,
    )

    result = ranker.rank([profile], [eligible, rejected])

    embedded_text = "\n".join(text for call in embeddings.calls for text in call)
    assert "Seattle" not in embedded_text
    assert len(result.job_embeddings) == 1
    rejected_match = next(match for match in result.matches if "Seattle" in match.job.location)
    assert rejected_match.semantic_score == 0
    assert rejected_match.hard_rejection_reason is not None
    embedding_updates = [update for update in progress_updates if update.key == "embeddings"]
    assert embedding_updates[-1].total == 2


def test_completed_batches_remain_checkpointed_when_a_later_batch_fails():
    profile = make_profile()
    jobs = [
        JobPosting(
            title=f"Backend Engineer {index}",
            location="Remote",
            work_mode="remote",
            url=f"https://example.com/jobs/{index}",
            source_url="https://example.com/careers",
        )
        for index in range(1, 4)
    ]
    embeddings = PermanentlyFailingEmbeddings()
    cache = MemoryEmbeddingCache()
    ranker = HybridRanker(
        embeddings=embeddings,
        cloud=FakeCloud(),
        shortlist_threshold=50,
        embedding_batch_size=1,
        embedding_max_retries=1,
        embedding_retry_delay_seconds=0,
        embedding_cache=cache,
    )

    with pytest.raises(RuntimeError, match="failed after 2 attempts"):
        ranker.rank([profile], jobs)

    assert profile.embedding_text in cache.vectors
    assert jobs[0].embedding_text in cache.vectors
    assert jobs[1].embedding_text in cache.vectors
    assert jobs[2].embedding_text not in cache.vectors
    assert embeddings.calls[-1] == [jobs[2].embedding_text]
    assert embeddings.calls[-2] == embeddings.calls[-1]


def test_experience_maximum_rejects_jobs_requiring_too_many_years():
    profile = make_profile().model_copy(
        update={
            "preferences": JobPreferences(
                preferred_experience_levels=["fresher", "entry", "junior"],
                maximum_required_experience_years=2,
            )
        }
    )
    experienced = JobPosting(
        title="Data Scientist",
        description="Basic qualifications: 5+ years of SQL and Python experience.",
        url="https://example.com/jobs/experienced",
        source_url="https://example.com/careers",
    )
    junior = experienced.model_copy(
        update={
            "description": "This position requires 1-2 years of professional experience.",
            "url": "https://example.com/jobs/junior",
        }
    )

    assert minimum_required_experience_years(experienced) == 5
    assert "configured maximum is 2" in (hard_rejection_reason(profile, experienced) or "")
    assert minimum_required_experience_years(junior) == 1
    assert hard_rejection_reason(profile, junior) is None


def test_junior_experience_limit_rejects_senior_title_without_numeric_requirement():
    profile = make_profile().model_copy(
        update={"preferences": JobPreferences(maximum_required_experience_years=2)}
    )
    job = JobPosting(
        title="Senior Data Scientist",
        description="Build forecasting systems.",
        url="https://example.com/jobs/senior",
        source_url="https://example.com/careers",
    )

    assert "above the configured junior level" in (hard_rejection_reason(profile, job) or "")


def test_strict_location_rejects_known_outside_location_but_allows_remote():
    profile = make_profile().model_copy(
        update={
            "preferences": JobPreferences(
                preferred_locations=["India"],
                require_preferred_location=True,
                allow_remote_outside_preferred_locations=True,
            )
        }
    )
    onsite = JobPosting(
        title="Data Scientist",
        location="Seattle, Washington, USA",
        work_mode="onsite",
        url="https://example.com/jobs/onsite",
        source_url="https://example.com/careers",
    )
    remote = onsite.model_copy(
        update={
            "title": "Remote Data Scientist",
            "work_mode": "remote",
            "url": "https://example.com/jobs/remote",
        }
    )

    assert "outside the configured preferred locations" in (
        hard_rejection_reason(profile, onsite) or ""
    )
    assert hard_rejection_reason(profile, remote) is None


def test_strict_location_keeps_unknown_or_missing_location_and_matches_country_code():
    profile = make_profile().model_copy(
        update={
            "preferences": JobPreferences(
                preferred_locations=["India"],
                require_preferred_location=True,
            )
        }
    )
    missing = JobPosting(
        title="Data Scientist",
        location="",
        url="https://example.com/jobs/missing",
        source_url="https://example.com/careers",
    )
    india = missing.model_copy(
        update={
            "location": "IN, KA, Bengaluru",
            "url": "https://example.com/jobs/india",
        }
    )
    unknown = missing.model_copy(
        update={
            "location": "Not specified",
            "url": "https://example.com/jobs/unknown",
        }
    )

    assert hard_rejection_reason(profile, missing) is None
    assert hard_rejection_reason(profile, unknown) is None
    assert hard_rejection_reason(profile, india) is None


def test_strict_india_location_recognizes_bangalore_without_country_name():
    profile = make_profile().model_copy(
        update={
            "preferences": JobPreferences(
                preferred_locations=["India"],
                require_preferred_location=True,
            )
        }
    )
    job = JobPosting(
        title="Computer Vision AI Intern",
        location="Bangalore",
        work_mode="hybrid",
        url="https://example.com/jobs/cv-intern",
        source_url="https://example.com/careers",
    )

    assert hard_rejection_reason(profile, job) is None


@pytest.mark.parametrize(
    "location",
    [
        "Mysuru",
        "Visakhapatnam",
        "Kozhikode",
        "Calicut",
        "Kozhikode (Calicut)",
        "Ranchi, Jharkhand",
        "Himachal Pradesh",
        "Puducherry",
        "IN, KA, Bengaluru",
        "Mumbai / Pune",
    ],
)
def test_strict_india_location_recognizes_cities_states_and_country_codes(location):
    profile = make_profile().model_copy(
        update={
            "preferences": JobPreferences(
                preferred_locations=["India"],
                require_preferred_location=True,
            )
        }
    )
    job = JobPosting(
        title="Software Engineer",
        location=location,
        work_mode="onsite",
        url="https://example.com/jobs/india-location",
        source_url="https://example.com/careers",
    )

    assert hard_rejection_reason(profile, job) is None


def test_word_in_is_not_mistaken_for_india_country_code():
    profile = make_profile().model_copy(
        update={
            "preferences": JobPreferences(
                preferred_locations=["India"],
                require_preferred_location=True,
            )
        }
    )
    job = JobPosting(
        title="Software Engineer",
        location="Based in Seattle, USA",
        work_mode="onsite",
        url="https://example.com/jobs/seattle",
        source_url="https://example.com/careers",
    )

    assert hard_rejection_reason(profile, job) is not None


def test_remote_description_bypasses_location_gate_and_scores_as_location_match():
    profile = make_profile().model_copy(
        update={
            "preferences": JobPreferences(
                preferred_locations=["India"],
                require_preferred_location=True,
                allow_remote_outside_preferred_locations=True,
            )
        }
    )
    onsite = JobPosting(
        title="Data Scientist",
        location="Seattle, Washington, USA",
        work_mode="onsite",
        description="Work from the Seattle office.",
        url="https://example.com/jobs/onsite-only",
        source_url="https://example.com/careers",
    )
    remote_possible = onsite.model_copy(
        update={
            "description": "This position can work from home.",
            "url": "https://example.com/jobs/remote-possible",
        }
    )

    assert hard_rejection_reason(profile, onsite) is not None
    assert hard_rejection_reason(profile, remote_possible) is None
    assert preference_score(profile, remote_possible) > preference_score(profile, onsite)
