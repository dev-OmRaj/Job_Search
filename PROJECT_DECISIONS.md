# Project Decisions

The following product decisions were confirmed for the first release:

- Accept one public company careers-page URL per manual CLI scan.
- Support arbitrary company careers pages rather than one company-specific adapter.
- Automatically discover every local PDF, DOCX, or TXT resume directly under `resumes/`.
- Use each resume filename stem as its profile name and infer role/skill relevance from resume content.
- Apply global YAML preferences for job terms, location, work mode, employment type, experience level, internships, and exclusions.
- Compare every discovered job with every active resume profile and recommend the best resume per job.
- Store sources, scan history, jobs, profile metadata, embeddings, and match history in SQLite through SQLAlchemy.
- Keep a separate minimal SQLite directory containing company names and every unique
  successful full scan URL. Repeated scans and cosmetic URL variants reuse one row, while
  genuinely different category paths remain separate.
- Use an HTTP-first crawler with a Playwright fallback for JavaScript-rendered sites.
- Scan Salesforce's original company careers page using the public job feeds loaded by
  its own frontend. Preserve URL filters and original Salesforce job-detail URLs rather
  than automatically routing that page to Workday.
- Avoid zstd HTTP responses, retry decompression errors without compression, and use the
  Playwright fallback if HTTP decoding still fails.
- Recover public pages with Chromium after transient network or recoverable HTTP failures,
  while never bypassing robots.txt, authentication, or persistent access controls.
- Crawl only the active careers site and recognized applicant-tracking systems; skip account,
  social, unrelated external, off-site redirect, and non-HTML links.
- Detect Phenom portals and extract their embedded job records and pagination through an
  isolated adapter without changing JSON-LD or Ollama extraction for other career sites.
- Detect standard Greenhouse boards and compatible local Greenhouse mirrors, request their
  public structured job feeds, and preserve canonical job URLs and Greenhouse IDs.
- Prefer a career portal's public job-description URL over its external application endpoint,
  and refresh stored canonical URLs when a job is rescanned.
- Respect `robots.txt`, rate limits, public-access boundaries, and private-network URL blocking.
- Use the direct Ollama Cloud API with `gpt-oss:120b` for unstructured extraction and fit assessments.
- Use local Ollama only for the downloaded `embeddinggemma` model.
- Generate local embeddings in small retryable batches, persist each completed batch in
  a model-scoped SQLite cache, and skip jobs rejected by every active profile.
- Keep original resumes local; send only best-effort PII-sanitized resume text to Ollama Cloud.
- Score matches out of 100: 50 semantic, 30 explicit preferences, and 20 cloud-model assessment.
- Shortlist jobs at a configurable threshold of 50.
- Skip cloud assessment when a pair has fewer than 30 local points because it cannot reach the threshold.
- Show recommendations in the terminal and export every scan to CSV and JSON.
- After the all-jobs table, show a separate terminal table containing only shortlisted jobs.
- Render terminal job URLs as explicit full-target hyperlinks so line wrapping cannot change
  the address opened by the terminal.
- Keep full scan history in SQLite, but show only the latest completed scan by default and
  require `jobs --history` to display repeated historical matches.
- Overwrite stable shortlisted-only CSV/JSON exports on normal scans; create timestamped
  shortlisted snapshots only when `scan --archive` is explicitly supplied.
- Show live CLI progress for workflow stages, crawling, extraction, embeddings, and matching.
- Defer automatic scheduled rescanning to a future release, as described in `ROADMAP.md`.
- Defer suppression of jobs already shortlisted in an earlier scan of the same company
  portal to a future release, as described in `ROADMAP.md`.
