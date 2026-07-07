# Supabase Self-Hosted Update Monitor

Monitor your local Supabase self-hosted deployment against the latest `master` branch on GitHub. Detects image version changes, new/removed environment variables, and generates detailed reports with AI-powered analysis.

## How It Works

1. Reads your local `docker-compose*.yml` files and `.env`
2. Fetches the corresponding files from `github.com/supabase/supabase`
3. Compares service image versions, environment variables, and file contents
4. Retrieves the real commit history between your deployed version and the latest GitHub version (scoped to `docker/` directory)
5. Sends an HTML email with a runbook (optional, requires SMTP)
6. Saves a markdown report to disk

## Quick Start

### Native (Python 3.11+)

```bash
# 1. Setup
cp .env.example .env
# edit .env with your paths and credentials

# 2. Create virtual environment and install dependencies
python3 -m venv venv
./venv/bin/pip install -r requirements.txt

# 3. Run
bash supabase-monitor.sh
```

### Docker (no Python required)

```bash
# 1. Setup
cp .env.example .env
# edit .env with your paths and credentials

# 2. Build and run
docker compose run --rm monitor
```

> The Docker image uses `python:3.11-slim`. On first run it builds automatically via `docker compose`.

## Configuration

All configuration is done through environment variables in `.env`:

| Variable | Required | Default | Description |
|---|---|---|---|
| `COMPOSE_DIR` | **yes** | — | Directory containing your local `docker-compose*.yml` files |
| `GIT_REPO_DIR` | **yes** | — | Path to a local clone of `github.com/supabase/supabase` (used to extract the `docker/` commit SHA) |
| `STATE_DIR` | no | `~/.supabase_monitor` | Directory for state file and markdown reports |
| `LOG_FILE` | no | `./supabase-monitor.log` | Path to the log file |
| `LITELLM_BASE_URL` | no | — | Base URL for LiteLLM (AI analysis) |
| `LITELLM_API_KEY` | no | — | API key for LiteLLM (if empty, AI analysis is skipped) |
| `LITELLM_MODEL` | no | `groq/llama-3.3-70b-versatile` | LLM model for analysis |
| `SMTP_SERVER` | no | — | SMTP server hostname (if empty, email is skipped) |
| `SMTP_PORT` | no | `587` | SMTP port |
| `SMTP_USER` | no | — | SMTP username |
| `SMTP_PASSWORD` | no | — | SMTP password |
| `MAIL_FROM` | no | — | Sender email address |
| `MAIL_TO` | no | — | Recipient email address (comma-separated) |

### Docker-specific notes

When running via Docker:

- `COMPOSE_DIR` is mounted at `/compose` inside the container. The path in `.env` points to your **host** directory; `docker-compose.yml` passes it as a bind mount.
- `STATE_DIR` defaults to `/root/.supabase_monitor` inside the container, mounted from `~/.supabase_monitor` on the host.
- `GIT_REPO_DIR` is **not used inside Docker** — the container cannot run `git` commands. The commit SHA comparison falls back to the saved state from the previous run.
- Logs are written to stdout and captured by `docker logs`.

## Output

### Markdown Report

Saved to `$STATE_DIR/reports/YYYY-MM-DD_HH-MM-SS_report.md`. Contains:

- Metadata (date, status, SHAs)
- Service version changes
- Full diff of all `docker-compose*.yml` files
- Environment variable changes (new and removed)
- .env diff (keys only, never values)
- Real commit history between deployed and available versions
- AI analysis (if configured) with risk level, breaking changes, and migration steps
- Operating procedure checklist

### Email (optional)

If SMTP is configured, an HTML email with the same information is sent to the recipients.

## Dependencies

- Python 3.11+
- `pyyaml>=6.0`
- `requests>=2.28`

Or simply Docker.

## Files

| File | Purpose |
|---|---|
| `supabase_monitor.py` | Main monitoring script |
| `supabase-monitor.sh` | Wrapper script for native execution (sources `.env`, manages venv) |
| `.env.example` | Template for configuration |
| `Dockerfile` | Container image definition |
| `docker-compose.yml` | Docker Compose service for `docker compose run` |
| `requirements.txt` | Python dependencies |
| `supabase-monitor.log` | Execution log (native mode) |
