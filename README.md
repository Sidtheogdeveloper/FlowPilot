# FlowPilot

**From questions to traceable insights.** FlowPilot is a chat-based CSV/Excel
analysis application. It profiles unfamiliar tables, proposes constrained intent,
compiles a validated DAG and runs approved operations on three independent worker
processes. Reports explain calculated findings, filters, joins and exclusions.
Follow-ups create separate workflow versions while preserving earlier results.

## Release branches

- **phase-3**: public accounts, password sign-in, private datasets/workflows/downloads,
  CSRF protection, revocable sessions and ownership regression tests.
- **phase-4**: FlowPilot branding, general-table workspace, profile/session settings,
  password changes, email verification/recovery and deployment hardening.
- **main**: integrated application release.

These are delivery snapshots, not claims that every future research feature is
complete. Tabular analysis is implemented; document analysis is outside this release.

## Start

1. Copy `.env.example` to `.env` and set a unique POSTGRES_PASSWORD.
2. Run `docker compose up --build -d`.
3. Open `http://localhost:8080` and create your account.
4. Upload a CSV or Excel worksheet and request an overview. For analytical questions,
   configure the local model endpoint as described in [DEPLOYMENT.md](DEPLOYMENT.md).
5. Review the DAG, run it and inspect the accepted insights or downloaded report.

The repository contains source and synthetic examples, not uploaded user data,
passwords, session tokens, model weights or the local training artifact cache.
No paid API is required for deterministic overview analysis.

## Product capabilities

- CSV/XLSX ingestion, worksheet selection and versioned column profiles.
- Typed filters, restricted arithmetic, grouping, time aggregation, sorting and IQR.
- Composite equality joins with confirmation and size/cardinality checks.
- Clarifications for ambiguous requests; schema and graph validation before execution.
- Greedy, HEFT-inspired and bounded joint-placement scheduling policies.
- PostgreSQL-backed attempts, heartbeat/lease recovery and stale-result rejection.
- Three same-host worker processes; whole operations execute on individual workers.
- Account-owned history, data selection, DAG versions and narrative reports.
- Argon2id passwords, HttpOnly sessions, CSRF checks, session revocation and email flows.

Limits: 25 MiB uploads, one million rows per table/intermediate result and 200 columns.
The planner executes approved operations, never generated Python or arbitrary SQL.
Uploading a dataset does not retrain the model. Current scheduling measurements do
not demonstrate consistent speed superiority over external systems.

## Development and testing

Use Python 3.12 and PostgreSQL 17. Install `requirements.txt`, configure DATABASE_URL
and ARTIFACT_ROOT, then run `uvicorn app.api:app --port 8000` and distinct workers
with `python -m app.worker`. Frontend: `cd frontend`, `npm ci`, `npm run dev`.

Tests reset a separate database named **workflow_test** and refuse the application
DB. Run `python -m pytest -q tests`; frontend `npm test` and `npm run build`.
See [SECURITY.md](SECURITY.md), [DEPLOYMENT.md](DEPLOYMENT.md) and
[RELEASE_VERIFICATION.md](RELEASE_VERIFICATION.md) for security boundaries,
configuration, measured checks and known deployment requirements.

## Public deployment

HTTPS and a working SMTP provider are required for production registration,
verification and recovery. Local HTTP is a development mode. No public host or
mail-provider account is provisioned by this repository. Existing anonymous data
stays unassigned until the operator approves migration.

## Appearance

Use **Appearance** in the top bar (also available on the sign-in screen). Choose
Light, Dark or the device setting, and Comfortable or Focused workspace layouts.
Preferences are saved on this device and followed across tabs. Navigation collapses
on narrow screens. The composer remains visible while the conversation scrolls.
Transitions, loading indicators and progress states respect reduced-motion settings.
