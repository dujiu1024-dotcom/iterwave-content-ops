# IterWave — AI Visual Content Operations

[English](README.md) · [简体中文](README.zh-CN.md) · [Русский](README.ru.md) · [日本語](README.ja.md) · [Français](README.fr.md) · [Deutsch](README.de.md)

> **Early Preview** — An open-source project under active development. Consult the code, tests, and deployment validation for the actual status of each capability.

IterWave is a content operations workspace for creators publishing image-and-text content across multiple platforms. It connects topic discovery, content planning, platform-specific production, quality checks, human-approved publishing, and performance review into a traceable workflow.

## Core principles

- **Visual-content first:** Focuses on topics, copy, image cards, quality checks, and operational review.
- **Plan-driven production:** Master plans, platform sub-plans, stage dependencies, and execution states drive production—not merely plan-management screens.
- **Human-controlled publishing:** Automation can prepare and save drafts, but never clicks a platform's Publish button.
- **Traceability:** Plan and content versions, task states, execution outcomes, and review evidence are recorded.
- **Local-first control:** Operators retain control of credentials, configuration, databases, and content assets.

## Workflow

```text
Topic library / Account positioning / Trend discovery
                         ↓
                  Master and sub-plans
                         ↓
              Staged orchestration and execution
                         ↓
             Platform-specific copy and image cards
                         ↓
                    Quality checks and draft saving
                         ↓
                    Human-approved publishing
                         ↓
             Data collection → Review → Iteration
```

## Supported platforms

Xiaohongshu, Douyin, Kuaishou, and WeChat Channels (image-and-text workflows). Actual capabilities depend on platform interfaces, account permissions, and UI changes; live account workflows must be validated in the target environment.

## Technology stack

| Layer | Technology |
|---|---|
| Backend | Python 3.11+, FastAPI, Pydantic v2 |
| Storage | SQLite, versioned schema migrations |
| Scheduling and execution | APScheduler, global task queue, isolated workers |
| Browser automation | Playwright (draft saving and read-only collection) |
| Frontend | React 18, TypeScript, Vite |
| Image processing | Pillow, OpenCV, NumPy |

## Quick start

### Backend

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
uvicorn src.api.main:app --reload
```

Prepare configuration based on `config/config.yaml`. Keep API keys, cookies, and tokens in local environment configuration; never commit them.

### Frontend

```bash
cd web
npm install
npm run dev
npm test
npm run build
```

## Deployment and security

- Before first deployment, configure a strong random `ITERWAVE_BOOTSTRAP_TOKEN` and use the initialization flow to set the permanent access password. Remove the bootstrap token after initialization.
- Use HTTPS for public deployments. Do not enable `ITERWAVE_ALLOW_LOCAL_BOOTSTRAP=1` in public environments.
- Restrict configuration, database, and session directories to the service operating-system user. Apply least-privilege permissions to sensitive files.
- APScheduler does not yet provide cross-process leader election. Run a single Uvicorn worker; do not configure multiple `--workers`.
- The global queue defaults to one worker and FIFO serial execution. Enable cross-account-group parallelism only after assessing its process-level hard-timeout impact.
- Tasks interrupted by a hard timeout or worker exit are not replayed automatically. Verify the platform-side outcome before deciding how to recover.
- Automated comments, replies, and follows are disabled by default. Confirm platform rules and account authorization before enabling them.
- The Playwright publishing boundary is draft saving. A person must confirm the actual publishing action.

## Repository layout

```text
src/
  core/       State machines, plan versions, task queue, scheduling, recovery, analytics
  adapters/   Platform adapters
  api/        FastAPI routes and application lifecycle
  vendor/     Third-party code retained under its upstream license
web/          React frontend
migrations/   Database schema migrations
tests/        Backend tests
config/       Configuration templates (no real secrets)
```

## Development and validation status

The project remains under active development. Passing repository tests does not imply end-to-end validation against real platforms. The following require a target environment with the relevant accounts, network access, and model credentials:

- Real login, draft saving, and data-reading workflows for all four platforms;
- The complete model workflow: copy generation, image-card production, and quality checks;
- The end-to-end loop from post-publication data collection through review to next-round strategy execution.

Contributions should include relevant tests. Do not put credentials, user data, or real account state in logs or the repository.

## Third-party code and license

Some platform adapter code is derived from and modified based on [Easel](https://github.com/kaichen/Easel) (Apache License 2.0). See `src/vendor/easel/LICENSE` and `src/vendor/easel/NOTICE` for attribution and license details.

This project is licensed under Apache License 2.0. See [LICENSE](LICENSE).
