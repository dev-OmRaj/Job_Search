# Project Command Reference

Run commands from the project directory unless a section says otherwise:

```bash
cd /home/om-raj-singh/workspace/programming/Projects/Job_search
```

This ensures relative paths such as `.env`, `preferences.yaml`, `resumes/`, and
`data/` resolve to the correct project files.

## First-time installation

Create an isolated Python environment:

```bash
python3 -m venv .venv
```

Use this once to keep the project's Python packages separate from system
packages.

Activate the environment in every new terminal:

```bash
source .venv/bin/activate
```

Upgrade the environment's package installer:

```bash
python -m pip install --upgrade pip
```

Install the project with testing and linting tools:

```bash
python -m pip install -e '.[dev]'
```

The editable installation makes the `job-search` command available and applies
source-code changes without requiring a reinstall. The `dev` extra installs
Pytest, coverage, and Ruff.

Install only the runtime dependencies when development tools are unnecessary:

```bash
python -m pip install -e .
```

Install Chromium in the project-local Playwright directory:

```bash
PLAYWRIGHT_BROWSERS_PATH=.playwright-browsers playwright install chromium
```

Chromium is the fallback for JavaScript-rendered pages and recoverable HTTP
failures. Keeping it under the project prevents dependency on a global browser
installation.

Create the initial configuration and data directories:

```bash
job-search init
```

This creates `.env`, `preferences.yaml`, `resumes/`, `data/`, and
`data/exports/` when missing. Existing configuration files are not overwritten.

## Ollama setup

Start the local Ollama server in a separate terminal:

```bash
ollama serve
```

The project uses this local service only for private resume/job embeddings.
Leave it running while scanning.

Download the local embedding model once:

```bash
ollama pull embeddinggemma
```

This downloads EmbeddingGemma directly through Ollama. It does not download the
model from Hugging Face.

Show locally installed Ollama models:

```bash
ollama list
```

Use this to confirm that `embeddinggemma` is available before running a scan.
The hosted `gpt-oss:120b` model is accessed through the Ollama Cloud API and is
not downloaded locally.

Large scans generate embeddings in checkpointed batches instead of one oversized
request. These optional `.env` settings control that behavior:

```dotenv
EMBEDDING_BATCH_SIZE=32
EMBEDDING_MAX_RETRIES=2
EMBEDDING_RETRY_DELAY_SECONDS=1
```

Each successful batch is cached in SQLite. If Ollama fails later in the scan,
rerunning the same scan reuses completed embeddings and retries only missing work.
Reduce `EMBEDDING_BATCH_SIZE` to `16` or `8` on a machine with limited memory.

## Configuration diagnostics

Check the API key, Ollama Cloud model, resumes, local EmbeddingGemma service,
preferences, and Playwright browser:

```bash
job-search doctor
```

Run diagnostics with a different preferences file:

```bash
job-search doctor --preferences path/to/preferences.yaml
```

Use this before scanning when testing an alternative configuration.

Run diagnostics without live progress bars:

```bash
job-search doctor --no-progress
```

This is useful in scripts, CI output, or redirected logs.

## Scan career pages

Scan one public company career-page URL:

```bash
job-search scan "https://company.example/careers"
```

This discovers jobs, compares them with every resume in `resumes/`, applies
`preferences.yaml`, stores the complete scan in SQLite, prints all jobs followed
by shortlisted jobs, and overwrites the two latest shortlisted export files.
Public Greenhouse boards—including career pages that load Greenhouse jobs through
an iframe or a compatible local JSON mirror—are detected automatically.

Scan with a different preferences file:

```bash
job-search scan "https://company.example/careers" --preferences path/to/preferences.yaml
```

Use this to run a one-off scan with different locations, job types, exclusions,
threshold, or crawler limits.

Scan without progress bars:

```bash
job-search scan "https://company.example/careers" --no-progress
```

Use this for automation or when saving terminal output to a log.

Create timestamped all-job and shortlisted snapshots in addition to the latest files:

```bash
job-search scan "https://company.example/careers" --archive
```

Normal scans overwrite `data/exports/latest-all-jobs.csv`,
`data/exports/latest-all-jobs.json`, `data/exports/latest-shortlisted.csv`, and
`data/exports/latest-shortlisted.json`. Use `--archive` only when permanent
per-scan CSV/JSON snapshots are required.

Options can be combined:

```bash
job-search scan "https://company.example/careers" \
  --preferences path/to/preferences.yaml \
  --archive \
  --no-progress
```

This example uses custom preferences, creates an archive, and hides progress.

## View job results

Salesforce's original careers page is scanned using the public JSON feeds loaded by
that page. It preserves country, region, location, team, contract type, employee type,
and search filters, including repeated employee-type selections. It scans every matching
job, regardless of the `page` display parameter, and uses original Salesforce job URLs.

```bash
job-search scan "https://www.salesforce.com/company/careers/jobs/?country=India&employeeTypes=Intern&employeeTypes=New+Grads&employeeTypes=Regular&page=1"
```

The crawler also discovers careers links embedded in script navigation data, subject
to its normal URL and site checks.

If no jobs are extracted and an empty board cannot be verified from structured data,
the scan fails before exporting, preserving the previous CSV/JSON and HTML reports.
An API-confirmed empty board can still complete when no crawl/extraction errors occurred.
Crawl and extraction warnings are retained in the scan database for later diagnosis.

Every completed `job-search scan` now generates `latest-report.html` in the configured
export directory and opens it in your default browser. The same report file is updated
on each scan. Browser tab reuse depends on your browser.

Generate the report after a scan without launching a browser (useful over SSH or for
concurrent scans):

```bash
job-search scan "https://company.example/careers" --no-open-report
```

If the browser cannot open, the terminal prints the report path so you can open it manually.
Report-generation or browser errors do not discard saved scan results.

Regenerate a searchable HTML dashboard for the latest scan:

```bash
job-search report
```

Open `data/exports/latest-report.html` in a browser. The report groups shortlisted,
near-match, eligible, and hard-rejected jobs and supports text, status, work-mode,
and minimum-score filters. Generate a report from an archived all-jobs export with:

```bash
job-search report --input data/exports/scan-ID-TIMESTAMP-all-jobs.json \
  --output data/exports/scan-ID-report.html
```

Show matches from the latest completed scan:

```bash
job-search jobs
```

Use this to revisit the most recent scan without crawling the website again.

Show only unique shortlisted jobs from the latest completed scan:

```bash
job-search jobs --shortlisted
```

Each job appears once with its best-scoring resume, avoiding repeated rows from
older scans or other resume profiles.

Show all stored scan history:

```bash
job-search jobs --history
```

Use this when auditing past scans. Repeated jobs and separate resume matches are
expected in the historical view.

Show shortlisted matches from every historical scan:

```bash
job-search jobs --shortlisted --history
```

Use this to compare how shortlisted results changed across scans.

Limit the number of displayed rows:

```bash
job-search jobs --shortlisted --limit 50
```

The allowed limit is 1 through 1000. The limit is applied after current
shortlisted jobs have been deduplicated.

Combine history and a limit:

```bash
job-search jobs --shortlisted --history --limit 200
```

This displays up to 200 historical shortlisted match records.

## Manage resumes

Place PDF, DOCX, or TXT resume files directly inside `resumes/`, then synchronize
them without running a website scan:

```bash
job-search sync-resumes
```

This registers usable files, refreshes changed resumes, and removes database
metadata and old match history for resumes no longer present in the directory.

Synchronize resumes using another preferences file:

```bash
job-search sync-resumes --preferences path/to/preferences.yaml
```

Use this when the profiles should be synchronized with an alternative global
preference configuration.

Synchronize resumes without progress bars:

```bash
job-search sync-resumes --no-progress
```

This is useful for scripts and redirected logs.

Show resume profiles currently stored in SQLite:

```bash
job-search profiles
```

Use this to confirm which resume filenames are registered as active profiles.

## Manage the company directory

Show every unique successful scan URL stored in the separate company-link database:

```bash
job-search companies
```

This reads `data/companies.db`. Repeating the same URL does not create another
row. Fragments, query filters, `www`, and trailing-slash differences are
normalized, while genuinely different paths such as separate Amazon role
categories remain separate full URLs.

Backfill the company directory from existing job-search scan history:

```bash
job-search sync-companies
```

Use this after upgrading an older database or when company links exist in
`data/job_search.db` but are missing from `data/companies.db`.

## Command help and shell completion

Show all available project commands:

```bash
job-search --help
```

Show help for an individual command:

```bash
job-search scan --help
job-search jobs --help
job-search doctor --help
job-search sync-resumes --help
job-search companies --help
job-search sync-companies --help
job-search profiles --help
job-search init --help
```

Use command-specific help to see current options, defaults, argument formats,
and allowed ranges.

Display the shell-completion script:

```bash
job-search --show-completion
```

Use this to inspect or manually configure command completion for the active
shell.

Install completion for the active shell:

```bash
job-search --install-completion
```

This enables tab completion for command names and options in supported shells.

## Development and verification

Run the complete automated test suite:

```bash
pytest
```

Use this after changing source code or configuration models.

Run tests and display coverage information:

```bash
pytest --cov=job_search_agent --cov-report=term-missing
```

This identifies source lines not exercised by automated tests.

Check code style and common Python errors:

```bash
ruff check .
```

Run Ruff's safe automatic fixes and check again:

```bash
ruff check . --fix
ruff check .
```

Review any changes produced by automatic fixes before keeping them.

Compile-check the Python source and tests:

```bash
python -m compileall -q src tests
```

This quickly detects syntax errors without running the application.

Check that installed package requirements are consistent:

```bash
python -m pip check
```

Use this after installing or upgrading dependencies.

Reinstall the editable project after dependency declarations change:

```bash
python -m pip install -e '.[dev]'
```

Source edits are immediately available in editable mode, but changes to
`pyproject.toml` dependencies require this command.

Leave the Python virtual environment:

```bash
deactivate
```

Use this when finished working on the project in the current terminal.

## Important output locations

These are paths rather than commands:

- `resumes/` — input PDF, DOCX, and TXT resumes.
- `preferences.yaml` — shortlist threshold, job preferences, exclusions, and
  crawler limits.
- `.env` — Ollama Cloud credentials and model/API settings.
- `data/job_search.db` — complete sources, scans, jobs, resumes, and match
  history.
- `data/companies.db` — company names and unique successful full scan URLs.
- `data/exports/latest-all-jobs.csv` — every deduplicated job from the latest scan.
- `data/exports/latest-all-jobs.json` — every deduplicated job from the latest scan
  with all available job, recommended-resume, and scoring information.
- `data/exports/latest-shortlisted.csv` — current deduplicated shortlisted jobs.
- `data/exports/latest-shortlisted.json` — current deduplicated shortlisted jobs
  with structured scoring information.
- `data/exports/scan-*-all-jobs.csv` and `.json` — optional complete scan snapshots.
- `data/exports/scan-*-shortlisted.csv` and `.json` — optional shortlisted snapshots.
# Blocked or partial scans

No extra command is required. Anti-bot failures do not stop later URLs on the same host:
a subsequent page may still be accessible. The generic crawler's `crawler.max_pages`
setting caps attempted URLs as well as loaded pages, including failures, so the scan
remains bounded.

Inaccessible pages and reached limits produce a **partial** scan status in the database,
terminal, and JSON/HTML report. Accessible jobs are retained; partial results remain
available through the latest-results commands. This does not bypass Cloudflare or
guarantee all Wellfound jobs can be retrieved. Historical scans are not rewritten.
