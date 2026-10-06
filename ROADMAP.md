# Job Search Agent Roadmap

## Deferred features

### Automatic careers-page rescanning

Status: Deferred to a future update.

Add a scheduler that periodically revisits saved public company careers-page URLs, detects new or changed job listings, updates the database, and reruns matching against the active resumes and preferences.

Planned behavior:

- Support a configurable scan interval, with daily scans as the suggested default.
- Retain a manual CLI command for immediate scans.
- Deduplicate listings and track when each job was first seen, last seen, changed, or removed.
- Recalculate shortlist scores when a job, resume, or preference changes.
- Record scan failures without deleting previously collected jobs.

The initial release will scan a careers-page URL only when invoked through the CLI.

### Prevent repeated shortlisting of the same job

Status: Deferred to a future update.

Avoid presenting a job as newly shortlisted when that same job was already shortlisted
during an earlier scan of the same company career portal.

Planned behavior:

- Create a persistent job identity using the company/portal identity plus a canonical job
  URL or stable requisition ID. Do not deduplicate using the title alone because different
  openings can have identical titles.
- Store when a job was first shortlisted and when it was most recently seen.
- Mark later matches as `previously_shortlisted` and exclude them from the default new-jobs
  shortlist and its CSV/JSON exports.
- Continue updating the stored job, availability, score, and recommended resume so history
  remains accurate.
- Provide an explicit option to include previously shortlisted jobs when reviewing history.
- Treat separate requisition IDs, locations, or canonical job URLs as distinct openings.
- Define safe handling for jobs that are removed and later reposted or materially changed,
  so a genuinely new opportunity is not hidden as a duplicate.

### Discover jobs across companies through APIs

Status: Deferred to a future update (requested 2026-09-15).

Extend the application to discover openings across multiple companies through job-search
APIs and retrieve structured job data from shared applicant-tracking system (ATS) feeds.
Build on the existing feed adapters to reduce dependence on individual career-page layouts.

Candidate providers to evaluate:

- Aggregators: [Adzuna](https://developer.adzuna.com/overview),
  [Jooble](https://jooble.org/api/about), and
  [TheirStack](https://theirstack.com/en/job-posting-api).
- Company-board feeds: [Greenhouse](https://docs.greenhouse.io/job-board.html),
  [Lever](https://github.com/lever/postings-api), and
  [Ashby](https://developers.ashbyhq.com/docs/public-job-posting-api).

Planned behavior:

- Use aggregators to discover jobs by role and location across employers, with India,
  remote work, internships, and entry-level opportunities as evaluation priorities.
- Use official ATS feeds to retrieve or verify descriptions and canonical job/application
  URLs. These feeds require a company's board identifier; they are not global job searches.
- Retain career-page crawling as a fallback when no supported feed is available.
- Normalize API results into the existing job model and apply the active resumes,
  preferences, ranking, database storage, and CSV/JSON exports.
- Deduplicate jobs across providers using stable requisition IDs and canonical URLs;
  retain provenance, freshness, and description-completeness information.
- Handle pagination, rate limits, retries, and partial results explicitly.

Decisions to make before implementation:

- Select providers after checking current India coverage, freshness, full-description
  availability, quotas, costs, and permitted storage/use of results.
- Confirm whether to use only public/free access or allow a paid API budget.
- Decide how company-board identifiers will be discovered and maintained, and how API
  searches will be configured alongside the existing manual scan workflow.

No provider subscription or integration is selected by this roadmap entry.
