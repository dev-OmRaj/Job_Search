# Job Search Agent

A Python CLI that scans public company career pages, extracts job listings, matches them against every resume in `resumes/`, applies configurable hard constraints and preferences, and generates searchable reports.

## Features

- Multiple PDF, DOCX, and TXT resume profiles
- Local `embeddinggemma` embeddings through Ollama
- Hosted `gpt-oss:120b` fit assessment through the Ollama API
- Adapters for common career platforms, with a bounded generic crawler fallback
- Location, remote-work, employment-type, internship, and experience constraints
- SQLite scan history and a separate company-portal database
- Deduplicated CSV, JSON, terminal, and dark-mode HTML reports
- Partial-scan diagnostics for blocked, throttled, or incomplete job pages

## Requirements

- Python 3.12 or newer
- [Ollama](https://ollama.com/) running locally for embeddings
- An Ollama API key for hosted model calls

## Installation

```bash
git clone https://github.com/dev-OmRaj/Job_Search.git
cd Job_Search
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
PLAYWRIGHT_BROWSERS_PATH=.playwright-browsers playwright install chromium
job-search init
```

Copy the configuration examples if `job-search init` has not created them:

```bash
cp .env.example .env
cp preferences.example.yaml preferences.yaml
```

Set `OLLAMA_API_KEY` in `.env`. Start Ollama and download the local embedding model:

```bash
ollama serve
ollama pull embeddinggemma
```

The real `.env`, `preferences.yaml`, resumes, databases, generated reports, virtual environment, and browser binaries are intentionally excluded from Git.

## Usage

Add one or more resumes to `resumes/`, edit `preferences.yaml`, then verify the configuration:

```bash
job-search doctor
```

Scan a public career page:

```bash
job-search scan "https://company.example/careers"
```

The latest complete job set, shortlist, and searchable HTML report are written under `data/exports/`. Scan history is stored in `data/job_search.db`.

Useful result commands include:

```bash
job-search results
job-search results --shortlisted
job-search scans
job-search companies
```

See [commard.md](commard.md) for the complete command reference and [PROJECT_DECISIONS.md](PROJECT_DECISIONS.md) for important design decisions.

## Development

```bash
pytest
ruff check src tests
```

## Responsible crawling

The scanner respects robots policy and treats inaccessible or incomplete pages as partial results. It does not guarantee that every career site can be crawled; prefer a company public job board or supported ATS endpoint when available.
