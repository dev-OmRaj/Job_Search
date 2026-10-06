from __future__ import annotations

import operator
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph

from .adapters.amazon import AMAZON_RESULT_CAP, extract_amazon_jobs
from .adapters.ashby import extract_ashby_jobs
from .adapters.atlassian import extract_atlassian_jobs
from .adapters.eightfold import extract_eightfold_jobs
from .adapters.eurofins import extract_eurofins_jobs
from .adapters.greenhouse import extract_greenhouse_jobs
from .adapters.html_cards import extract_structured_html_jobs
from .adapters.keka import extract_keka_jobs
from .adapters.nextjs import extract_nextjs_jobs
from .adapters.oracle import extract_oracle_jobs
from .adapters.phenom import extract_phenom_jobs
from .adapters.salesforce import extract_salesforce_jobs
from .adapters.successfactors import extract_successfactors_jobs
from .adapters.workday import extract_workday_jobs
from .company_database import CompanyDirectory
from .crawler import CareersCrawler
from .database import Database
from .exporter import export_matches
from .extraction import deduplicate_jobs, extract_json_ld_jobs
from .llm import OllamaCloudGateway
from .models import (
    JobMatch,
    JobPosting,
    PageDocument,
    ProjectPreferences,
    ResumeProfile,
    ScanResult,
)
from .progress import ProgressCallback, emit_progress
from .ranking import HybridRanker, RankingResult
from .resumes import load_active_resume_profiles


class WorkflowState(TypedDict, total=False):
    source_url: str
    scan_id: int
    preferences: ProjectPreferences
    profiles: list[ResumeProfile]
    pages: list[PageDocument]
    skipped_pages: list[str]
    jobs: list[JobPosting]
    ranking: RankingResult
    matches: list[JobMatch]
    export_files: list[Path]
    portal_reported_jobs: int
    result_cards_scanned: int
    duplicate_result_cards: int
    coverage_complete: bool
    crawl_incomplete: bool
    errors: Annotated[list[str], operator.add]


@dataclass(slots=True)
class WorkflowServices:
    crawler: CareersCrawler
    cloud: OllamaCloudGateway
    ranker: HybridRanker
    database: Database
    export_dir: Path
    resume_dir: Path = Path("resumes")
    progress: ProgressCallback | None = None
    company_directory: CompanyDirectory | None = None
    archive_exports: bool = False


class JobSearchWorkflow:
    def __init__(self, services: WorkflowServices):
        self.services = services
        self.graph = self._build_graph()

    def _build_graph(self):
        services = self.services

        def load_profiles(state: WorkflowState) -> dict:
            report = load_active_resume_profiles(
                state["preferences"],
                services.resume_dir,
                services.progress,
            )
            services.database.synchronize_resume_profiles(report.profiles)
            if not report.profiles:
                details = "; ".join(report.errors) or "no supported resume files were found"
                raise ValueError(f"No usable resume profiles found. {details}")
            emit_progress(
                services.progress,
                "workflow",
                f"Loaded {len(report.profiles)} resumes",
                completed=1,
                total=6,
            )
            return {"profiles": report.profiles, "errors": report.errors}

        def crawl(state: WorkflowState) -> dict:
            report = services.crawler.crawl(state["source_url"])
            emit_progress(
                services.progress,
                "workflow",
                f"Crawled {len(report.pages)} pages",
                completed=2,
                total=6,
            )
            return {
                "pages": report.pages,
                "skipped_pages": report.skipped,
                "crawl_incomplete": bool(report.errors),
                "errors": report.errors,
            }

        def extract(state: WorkflowState) -> dict:
            jobs: list[JobPosting] = []
            amazon_jobs: list[JobPosting] = []
            amazon_expected_total = 0
            greenhouse_jobs: list[JobPosting] = []
            greenhouse_expected_total = 0
            atlassian_jobs: list[JobPosting] = []
            atlassian_expected_total = 0
            atlassian_raw_count = 0
            eurofins_jobs: list[JobPosting] = []
            eurofins_expected_total = 0
            eurofins_raw_count = 0
            ashby_jobs: list[JobPosting] = []
            ashby_expected_total = 0
            ashby_raw_count = 0
            oracle_jobs: list[JobPosting] = []
            oracle_expected_total = 0
            oracle_raw_count = 0
            eightfold_jobs: list[JobPosting] = []
            eightfold_expected_total = 0
            eightfold_raw_count = 0
            eightfold_missing_detail_count = 0
            workday_jobs: list[JobPosting] = []
            workday_expected_total = 0
            workday_raw_count = 0
            phenom_jobs: list[JobPosting] = []
            phenom_expected_total = 0
            phenom_raw_count = 0
            successfactors_jobs: list[JobPosting] = []
            successfactors_expected_total = 0
            successfactors_raw_count = 0
            salesforce_total = 0
            salesforce_raw_count = 0
            salesforce_complete: bool | None = None
            errors: list[str] = []
            pages = state.get("pages", [])
            confirmed_empty_board = False
            emit_progress(
                services.progress,
                "extraction",
                "Extracting job listings",
                completed=0,
                total=len(pages),
            )
            for index, page in enumerate(pages, start=1):
                salesforce = extract_salesforce_jobs(page)
                if salesforce is not None:
                    jobs.extend(salesforce.jobs)
                    salesforce_total += salesforce.total_hits
                    salesforce_raw_count += salesforce.raw_job_count
                    salesforce_complete = salesforce.coverage_complete and (
                        salesforce_complete is not False
                    )
                    confirmed_empty_board = salesforce.total_hits == 0
                    if not salesforce.coverage_complete or len(salesforce.jobs) < salesforce.total_hits:
                        salesforce_complete = False
                        errors.append("Incomplete Salesforce scan: not every filtered job has verified details.")
                    emit_progress(services.progress, "extraction", "Extracting Salesforce job listings",
                                  completed=index, total=len(pages), finished=index == len(pages))
                    continue
                amazon = extract_amazon_jobs(page)
                greenhouse = extract_greenhouse_jobs(page) if amazon is None else None
                atlassian = (
                    extract_atlassian_jobs(page)
                    if amazon is None and greenhouse is None
                    else None
                )
                eurofins = (
                    extract_eurofins_jobs(page)
                    if amazon is None and greenhouse is None and atlassian is None
                    else None
                )
                ashby = (
                    extract_ashby_jobs(page)
                    if (
                        amazon is None
                        and greenhouse is None
                        and atlassian is None
                        and eurofins is None
                    )
                    else None
                )
                oracle = (
                    extract_oracle_jobs(page)
                    if (
                        amazon is None
                        and greenhouse is None
                        and atlassian is None
                        and eurofins is None
                        and ashby is None
                    )
                    else None
                )
                eightfold = (
                    extract_eightfold_jobs(page)
                    if (
                        amazon is None
                        and greenhouse is None
                        and atlassian is None
                        and eurofins is None
                        and ashby is None
                        and oracle is None
                    )
                    else None
                )
                workday = (
                    extract_workday_jobs(page)
                    if (
                        amazon is None
                        and greenhouse is None
                        and atlassian is None
                        and eurofins is None
                        and ashby is None
                        and oracle is None
                        and eightfold is None
                    )
                    else None
                )
                phenom = (
                    extract_phenom_jobs(page)
                    if (
                        amazon is None
                        and greenhouse is None
                        and atlassian is None
                        and eurofins is None
                        and ashby is None
                        and oracle is None
                        and eightfold is None
                        and workday is None
                    )
                    else None
                )
                successfactors = (
                    extract_successfactors_jobs(page)
                    if (
                        amazon is None
                        and greenhouse is None
                        and atlassian is None
                        and eurofins is None
                        and ashby is None
                        and oracle is None
                        and eightfold is None
                        and workday is None
                        and phenom is None
                    )
                    else None
                )
                nextjs = (
                    extract_nextjs_jobs(page)
                    if (
                        amazon is None
                        and greenhouse is None
                        and atlassian is None
                        and eurofins is None
                        and ashby is None
                        and oracle is None
                        and eightfold is None
                        and phenom is None
                        and successfactors is None
                    )
                    else None
                )
                keka = (
                    extract_keka_jobs(page)
                    if (
                        amazon is None
                        and greenhouse is None
                        and atlassian is None
                        and eurofins is None
                        and ashby is None
                        and oracle is None
                        and eightfold is None
                        and phenom is None
                        and successfactors is None
                        and nextjs is None
                    )
                    else None
                )
                html_cards = (
                    extract_structured_html_jobs(page)
                    if (
                        amazon is None
                        and greenhouse is None
                        and eurofins is None
                        and ashby is None
                        and oracle is None
                        and eightfold is None
                        and phenom is None
                        and successfactors is None
                        and nextjs is None
                        and keka is None
                    )
                    else None
                )
                for board in (amazon, greenhouse, atlassian, eurofins, ashby, oracle,
                              eightfold, workday, phenom, successfactors, nextjs, keka, html_cards):
                    if board is not None and getattr(board, "total_hits", None) == 0:
                        confirmed_empty_board = True
                if amazon is not None:
                    jobs.extend(amazon.jobs)
                    amazon_jobs.extend(amazon.jobs)
                    amazon_expected_total = max(amazon_expected_total, amazon.total_hits)
                    if amazon.total_hits > 0 and not amazon.jobs:
                        errors.append(
                            f"Amazon reported {amazon.total_hits} jobs at {page.url}, "
                            "but none could be extracted"
                        )
                elif greenhouse is not None:
                    jobs.extend(greenhouse.jobs)
                    greenhouse_jobs.extend(greenhouse.jobs)
                    greenhouse_expected_total = max(
                        greenhouse_expected_total,
                        greenhouse.total_hits,
                    )
                    if greenhouse.total_hits > 0 and not greenhouse.jobs:
                        errors.append(
                            f"Greenhouse reported {greenhouse.total_hits} jobs at {page.url}, "
                            "but none could be extracted"
                        )
                elif atlassian is not None:
                    jobs.extend(atlassian.jobs)
                    atlassian_jobs.extend(atlassian.jobs)
                    atlassian_expected_total = max(
                        atlassian_expected_total, atlassian.total_hits
                    )
                    atlassian_raw_count += atlassian.raw_job_count
                elif eurofins is not None:
                    jobs.extend(eurofins.jobs)
                    eurofins_jobs.extend(eurofins.jobs)
                    eurofins_expected_total = max(
                        eurofins_expected_total, eurofins.total_hits
                    )
                    eurofins_raw_count += eurofins.raw_job_count
                elif ashby is not None:
                    jobs.extend(ashby.jobs)
                    ashby_jobs.extend(ashby.jobs)
                    ashby_expected_total = max(ashby_expected_total, ashby.total_hits)
                    ashby_raw_count += ashby.raw_job_count
                elif oracle is not None:
                    jobs.extend(oracle.jobs)
                    oracle_jobs.extend(oracle.jobs)
                    oracle_expected_total = max(oracle_expected_total, oracle.total_hits)
                    oracle_raw_count += oracle.raw_job_count
                elif eightfold is not None:
                    jobs.extend(eightfold.jobs)
                    eightfold_jobs.extend(eightfold.jobs)
                    eightfold_expected_total = max(eightfold_expected_total, eightfold.total_hits)
                    eightfold_raw_count += eightfold.raw_job_count
                    eightfold_missing_detail_count += eightfold.missing_detail_count
                elif workday is not None:
                    jobs.extend(workday.jobs)
                    workday_jobs.extend(workday.jobs)
                    workday_expected_total = max(workday_expected_total, workday.total_hits)
                    workday_raw_count += workday.raw_job_count
                elif phenom is not None:
                    jobs.extend(phenom.jobs)
                    phenom_jobs.extend(phenom.jobs)
                    phenom_expected_total = max(phenom_expected_total, phenom.total_hits)
                    phenom_raw_count += phenom.raw_job_count
                    if phenom.total_hits > 0 and not phenom.jobs:
                        errors.append(
                            f"Phenom reported {phenom.total_hits} jobs at {page.url}, "
                            "but none could be extracted"
                        )
                elif successfactors is not None:
                    jobs.extend(successfactors.jobs)
                    successfactors_jobs.extend(successfactors.jobs)
                    successfactors_expected_total = max(
                        successfactors_expected_total,
                        successfactors.total_hits,
                    )
                    successfactors_raw_count += successfactors.raw_job_count
                elif nextjs is not None:
                    jobs.extend(nextjs.jobs)
                elif keka is not None:
                    jobs.extend(keka.jobs)
                elif html_cards is not None:
                    jobs.extend(html_cards.jobs)
                else:
                    structured_jobs = extract_json_ld_jobs(page)
                    if structured_jobs:
                        jobs.extend(structured_jobs)
                    else:
                        try:
                            jobs.extend(services.cloud.extract_jobs(page))
                        except Exception as exc:
                            errors.append(f"Cloud extraction failed for {page.url}: {exc}")
                emit_progress(
                    services.progress,
                    "extraction",
                    "Extracting job listings",
                    completed=index,
                    total=len(pages),
                    finished=index == len(pages),
                )
            if not pages:
                emit_progress(
                    services.progress,
                    "extraction",
                    "No pages available for extraction",
                    completed=0,
                    total=0,
                    finished=True,
                )
            unique_jobs = deduplicate_jobs(jobs)
            if not unique_jobs and (
                not confirmed_empty_board or state.get("errors") or errors
            ):
                details = "; ".join((state.get("errors", []) + errors)[:5])
                raise RuntimeError(
                    "No jobs could be verified: careers-page discovery or extraction was "
                    "unsuccessful. This is not confirmation that the company has no openings. "
                    "Previous exports were preserved. " + details
                )
            unique_amazon_jobs = deduplicate_jobs(amazon_jobs)
            unique_greenhouse_jobs = deduplicate_jobs(greenhouse_jobs)
            unique_atlassian_jobs = deduplicate_jobs(atlassian_jobs)
            unique_eurofins_jobs = deduplicate_jobs(eurofins_jobs)
            unique_ashby_jobs = deduplicate_jobs(ashby_jobs)
            unique_oracle_jobs = deduplicate_jobs(oracle_jobs)
            unique_eightfold_jobs = deduplicate_jobs(eightfold_jobs)
            unique_workday_jobs = deduplicate_jobs(workday_jobs)
            unique_phenom_jobs = deduplicate_jobs(phenom_jobs)
            unique_successfactors_jobs = deduplicate_jobs(successfactors_jobs)
            if amazon_expected_total > len(unique_amazon_jobs):
                extracted_count = len(unique_amazon_jobs)
                job_label = "job" if extracted_count == 1 else "jobs"
                verb = "was" if extracted_count == 1 else "were"
                errors.append(
                    "Incomplete Amazon scan: the portal reported "
                    f"{amazon_expected_total} jobs, but only "
                    f"{extracted_count} unique {job_label} {verb} extracted. "
                    "Check crawler limits and preceding crawl warnings."
                )
            if amazon_expected_total >= AMAZON_RESULT_CAP:
                errors.append(
                    "Amazon's public search endpoint reports at most 10,000 jobs; "
                    "an unfiltered scan may contain additional jobs beyond this API cap."
                )
            if greenhouse_expected_total > len(unique_greenhouse_jobs):
                errors.append(
                    "Incomplete Greenhouse scan: the portal reported "
                    f"{greenhouse_expected_total} jobs, but only "
                    f"{len(unique_greenhouse_jobs)} unique jobs were extracted."
                )
            if atlassian_expected_total > atlassian_raw_count:
                errors.append(
                    "Incomplete Atlassian scan: the endpoint reported "
                    f"{atlassian_expected_total} jobs, but only {atlassian_raw_count} records "
                    f"were scanned ({len(unique_atlassian_jobs)} unique jobs)."
                )
            if eurofins_expected_total > eurofins_raw_count:
                errors.append(
                    "Incomplete Eurofins scan: the filtered feed contained "
                    f"{eurofins_expected_total} jobs, but only {eurofins_raw_count} records "
                    f"were scanned ({len(unique_eurofins_jobs)} unique jobs)."
                )
            if ashby_expected_total > ashby_raw_count:
                errors.append(
                    "Incomplete Ashby scan: the portal reported "
                    f"{ashby_expected_total} jobs, but only {ashby_raw_count} result cards "
                    f"were scanned ({len(unique_ashby_jobs)} unique jobs)."
                )
            if oracle_expected_total > oracle_raw_count:
                errors.append(
                    "Incomplete Oracle scan: the portal reported "
                    f"{oracle_expected_total} jobs, but only {oracle_raw_count} result cards "
                    f"were scanned ({len(unique_oracle_jobs)} unique jobs)."
                )
            if eightfold_expected_total > eightfold_raw_count:
                errors.append(
                    "Incomplete Eightfold scan: the portal reported "
                    f"{eightfold_expected_total} jobs, but only {eightfold_raw_count} result cards "
                    f"were scanned ({len(unique_eightfold_jobs)} unique jobs)."
                )
            if eightfold_missing_detail_count:
                errors.append(
                    "Incomplete Eightfold job details: "
                    f"{eightfold_missing_detail_count} jobs could not be assessed because "
                    "their full descriptions were unavailable after retries."
                )
            if workday_expected_total > workday_raw_count:
                errors.append(
                    "Incomplete Workday scan: the portal reported "
                    f"{workday_expected_total} jobs, but only {workday_raw_count} result cards "
                    f"were scanned ({len(unique_workday_jobs)} unique jobs)."
                )
            if phenom_expected_total > phenom_raw_count:
                card_label = "result card" if phenom_raw_count == 1 else "result cards"
                verb = "was" if phenom_raw_count == 1 else "were"
                errors.append(
                    "Incomplete Phenom scan: the portal reported "
                    f"{phenom_expected_total} jobs, but only "
                    f"{phenom_raw_count} {card_label} {verb} scanned. "
                    "Check preceding crawl warnings."
                )
            if successfactors_expected_total > successfactors_raw_count:
                errors.append(
                    "Incomplete SuccessFactors scan: the portal reported "
                    f"{successfactors_expected_total} jobs, but only "
                    f"{successfactors_raw_count} result cards were extracted "
                    f"({len(unique_successfactors_jobs)} unique jobs). "
                    "Increase crawler.max_pages if the result set exceeds the scan limit."
                )
            emit_progress(
                services.progress,
                "workflow",
                f"Extracted {len(unique_jobs)} jobs",
                completed=3,
                total=6,
            )
            extraction_result = {
                "jobs": unique_jobs,
                "errors": errors,
                "portal_reported_jobs": (
                    atlassian_expected_total
                    or eurofins_expected_total
                    or ashby_expected_total
                    or oracle_expected_total
                    or eightfold_expected_total
                    or workday_expected_total
                    or phenom_expected_total
                    or 0
                ),
                "result_cards_scanned": (
                    atlassian_raw_count
                    or eurofins_raw_count
                    or ashby_raw_count
                    or oracle_raw_count
                    or eightfold_raw_count
                    or workday_raw_count
                    or phenom_raw_count
                ),
                "duplicate_result_cards": (
                    max(0, atlassian_raw_count - len(unique_atlassian_jobs))
                    if atlassian_expected_total
                    else (
                        max(0, eurofins_raw_count - len(unique_eurofins_jobs))
                        if eurofins_expected_total
                        else (
                            max(0, ashby_raw_count - len(unique_ashby_jobs))
                            if ashby_expected_total
                            else (
                                max(0, oracle_raw_count - len(unique_oracle_jobs))
                                if oracle_expected_total
                                else (
                                    max(0, eightfold_raw_count - len(unique_eightfold_jobs))
                                    if eightfold_expected_total
                                    else (
                                        max(0, workday_raw_count - len(unique_workday_jobs))
                                        if workday_expected_total
                                        else max(0, phenom_raw_count - len(unique_phenom_jobs))
                                    )
                                )
                            )
                        )
                    )
                ),
                "coverage_complete": (
                    atlassian_expected_total <= atlassian_raw_count
                    if atlassian_expected_total
                    else (
                        eurofins_expected_total <= eurofins_raw_count
                        if eurofins_expected_total
                        else (
                            ashby_expected_total <= ashby_raw_count
                            if ashby_expected_total
                            else (
                                oracle_expected_total <= oracle_raw_count
                                if oracle_expected_total
                                else (
                                    eightfold_expected_total <= eightfold_raw_count
                                    and eightfold_missing_detail_count == 0
                                    if eightfold_expected_total
                                    else (
                                        workday_expected_total <= workday_raw_count
                                        if workday_expected_total
                                        else phenom_expected_total <= phenom_raw_count
                                    )
                                )
                            )
                        )
                    )
                ),
            }
            if salesforce_complete is not None:
                extraction_result.update({
                    "portal_reported_jobs": salesforce_total,
                    "result_cards_scanned": salesforce_raw_count,
                    "duplicate_result_cards": max(0, salesforce_raw_count - len(unique_jobs)),
                    "coverage_complete": salesforce_complete,
                })
            return extraction_result

        def rank(state: WorkflowState) -> dict:
            ranking = services.ranker.rank(state["profiles"], state.get("jobs", []))
            emit_progress(
                services.progress,
                "workflow",
                f"Evaluated {len(ranking.matches)} matches",
                completed=4,
                total=6,
            )
            return {"ranking": ranking, "matches": ranking.matches, "errors": ranking.errors}

        def persist(state: WorkflowState) -> dict:
            services.database.persist_ranking(
                scan_id=state["scan_id"],
                profiles=state["profiles"],
                result=state["ranking"],
            )
            emit_progress(
                services.progress,
                "workflow",
                "Saved results",
                completed=5,
                total=6,
            )
            return {}

        def export(state: WorkflowState) -> dict:
            files = export_matches(
                state.get("matches", []),
                services.export_dir,
                state["scan_id"],
                archive=services.archive_exports,
                partial=bool(state.get("crawl_incomplete") or state.get("coverage_complete") is False),
                warnings=state.get("errors", []),
            )
            emit_progress(
                services.progress,
                "workflow",
                "Partial scan finished" if (
                    state.get("crawl_incomplete") or state.get("coverage_complete") is False
                ) else "Scan complete",
                completed=6,
                total=6,
                finished=True,
            )
            return {"export_files": files}

        builder = StateGraph(WorkflowState)
        builder.add_node("load_profiles", load_profiles)
        builder.add_node("crawl", crawl)
        builder.add_node("extract", extract)
        builder.add_node("rank", rank)
        builder.add_node("persist", persist)
        builder.add_node("export", export)
        builder.add_edge(START, "load_profiles")
        builder.add_edge("load_profiles", "crawl")
        builder.add_edge("crawl", "extract")
        builder.add_edge("extract", "rank")
        builder.add_edge("rank", "persist")
        builder.add_edge("persist", "export")
        builder.add_edge("export", END)
        return builder.compile()

    def run(self, source_url: str, preferences: ProjectPreferences) -> ScanResult:
        scan_id = self.services.database.start_scan(source_url)
        jobs_found = 0
        company_directory_errors: list[str] = []
        emit_progress(
            self.services.progress,
            "workflow",
            "Starting scan",
            completed=0,
            total=6,
        )
        try:
            final = self.graph.invoke(
                {
                    "source_url": source_url,
                    "scan_id": scan_id,
                    "preferences": preferences,
                    "errors": [],
                }
            )
            jobs_found = len(final.get("jobs", []))
            if self.services.company_directory is not None and jobs_found > 0:
                try:
                    self.services.company_directory.upsert(
                        source_url,
                        (job.company for job in final.get("jobs", [])),
                    )
                except Exception as exc:
                    company_directory_errors.append(f"Company directory update failed: {exc}")
            self.services.database.finish_scan(
                scan_id, jobs_found=jobs_found,
                warnings=final.get("errors", []) + company_directory_errors,
                partial=bool(final.get("crawl_incomplete") or final.get("coverage_complete") is False),
            )
        except Exception as exc:
            emit_progress(
                self.services.progress,
                "workflow",
                "Scan failed",
                finished=True,
            )
            self.services.database.finish_scan(scan_id, jobs_found=jobs_found, error=str(exc))
            raise

        matches = final.get("matches", [])
        shortlisted_urls = {match.job.identity_key for match in matches if match.shortlisted}
        return ScanResult(
            scan_id=scan_id,
            source_url=source_url,
            jobs_found=jobs_found,
            matches_evaluated=len(matches),
            shortlisted_jobs=len(shortlisted_urls),
            export_files=final.get("export_files", []),
            matches=matches,
            errors=final.get("errors", []) + company_directory_errors,
            pages_scanned=len(final.get("pages", [])),
            skipped_pages=final.get("skipped_pages", []),
            portal_reported_jobs=final.get("portal_reported_jobs") or None,
            result_cards_scanned=(
                final.get("result_cards_scanned") if final.get("portal_reported_jobs") else None
            ),
            duplicate_result_cards=(
                final.get("duplicate_result_cards") if final.get("portal_reported_jobs") else None
            ),
            coverage_complete=(
                False if final.get("crawl_incomplete") else (
                    final.get("coverage_complete") if final.get("portal_reported_jobs") else None
                )
            ),
        )
