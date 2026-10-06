from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urljoin

from langchain_core.prompts import ChatPromptTemplate
from ollama import Client
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from .models import JobPosting, LLMFitAssessment, PageDocument, ResumeProfile

EXTRACTION_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You extract factual job listings from public careers-page text. "
            "Never invent a job, URL, skill, or requirement. Return JSON only, with no markdown.",
        ),
        (
            "human",
            """Extract every job listing explicitly present in the page below.

Return a JSON object with this exact shape:
{{"jobs": [{{"title": "", "company": "", "location": "", "work_mode": "remote|hybrid|onsite|unknown", "description": "", "url": "", "external_id": null, "employment_type": null, "experience_level": null, "salary": null, "skills": [], "posted_at": null}}]}}

Use the page URL to resolve intent, but preserve relative job URLs if that is all the page contains. If this page contains no actual job listing, return {{"jobs": []}}.

Page URL: {page_url}
Page text:
{page_text}
""",
        ),
    ]
)

FIT_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You assess job fit using only the sanitized resume summary and job facts supplied. "
            "Do not infer protected traits or fabricate experience. Return JSON only, with no markdown.",
        ),
        (
            "human",
            """Award a fit score from 0 through 20. Explain concrete matches and gaps.

Return this exact JSON shape:
{{"fit_score": 0, "rationale": "", "strengths": [], "gaps": []}}

Profile name: {profile_name}
Sanitized resume summary:
{resume_summary}

Job title: {job_title}
Company: {company}
Location: {location}
Work mode: {work_mode}
Job skills: {job_skills}
Job description:
{job_description}
""",
        ),
    ]
)


class OllamaCloudGateway:
    """Authenticated direct client for Ollama's hosted API."""

    def __init__(self, *, api_key: str, host: str, model: str):
        if not api_key:
            raise ValueError("OLLAMA_API_KEY is required for Ollama Cloud")
        self.model = model
        self.client = Client(
            host=host.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
        )

    @retry(
        retry=retry_if_exception_type((ConnectionError, TimeoutError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )
    def _chat(self, messages: list[dict[str, str]]) -> str:
        response = self.client.chat(model=self.model, messages=messages, stream=False)
        return response.message.content

    @staticmethod
    def _messages(prompt: ChatPromptTemplate, **values: Any) -> list[dict[str, str]]:
        output: list[dict[str, str]] = []
        for message in prompt.format_messages(**values):
            role = "system" if message.type == "system" else "user"
            output.append({"role": role, "content": str(message.content)})
        return output

    @staticmethod
    def _parse_json(content: str) -> dict[str, Any]:
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.IGNORECASE)
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end < start:
            raise ValueError("Ollama Cloud response did not contain a JSON object")
        parsed = json.loads(cleaned[start : end + 1])
        if not isinstance(parsed, dict):
            raise ValueError("Ollama Cloud response JSON must be an object")
        return parsed

    def extract_jobs(self, page: PageDocument) -> list[JobPosting]:
        messages = self._messages(
            EXTRACTION_PROMPT,
            page_url=str(page.url),
            page_text=page.text[:40_000],
        )
        payload = self._parse_json(self._chat(messages))
        raw_jobs = payload.get("jobs", [])
        if not isinstance(raw_jobs, list):
            raise ValueError("Ollama Cloud extraction response has a non-list `jobs` field")
        jobs: list[JobPosting] = []
        for raw_job in raw_jobs:
            if not isinstance(raw_job, dict):
                continue
            raw_job["url"] = urljoin(str(page.url), str(raw_job.get("url") or page.url))
            raw_job["source_url"] = str(page.source_url)
            try:
                jobs.append(JobPosting.model_validate(raw_job))
            except ValueError:
                continue
        return jobs

    def assess_fit(self, profile: ResumeProfile, job: JobPosting) -> LLMFitAssessment:
        messages = self._messages(
            FIT_PROMPT,
            profile_name=profile.name,
            resume_summary=profile.sanitized_summary[:8_000],
            job_title=job.title,
            company=job.company,
            location=job.location,
            work_mode=job.work_mode,
            job_skills=", ".join(job.skills),
            job_description=job.description[:12_000],
        )
        return LLMFitAssessment.model_validate(self._parse_json(self._chat(messages)))
