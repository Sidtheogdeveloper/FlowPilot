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

## Analysis and collaboration tools

- **Dataset library:** paginated source previews, column quality summaries and
  numeric distributions. Upload a new CSV/Excel version without overwriting the
  original. Matching headers retain stable column IDs; saved workflow versions
  keep their original inputs.
- **Results:** interactive bar/line charts with selectable measures and labels,
  calculated-row previews, PDF findings, selected-result CSV and multi-sheet Excel
  exports. Charts show at most 20 observations; exports include complete accepted
  analytical results. Spreadsheet formula-like text is escaped.
- **Conversations:** rename, search across questions, pin, archive, move to
  recoverable trash and restore. Each follow-up stays in the original chat.
- **Workflow tools:** compare added, removed and changed DAG definitions; optionally
  reuse unchanged accepted subtrees within the same owner's conversation. Reuse
  checks executor/dependency versions, operation parameters, input fingerprints,
  output contracts and artifact checksums. Reports are regenerated.
- **Feedback:** record correct/incorrect results, inefficient workflows and
  suggestions. Feedback and uploads never automatically retrain a model.
- **Schedules:** repeat a saved validated analysis hourly, daily or weekly, with
  optional latest uploaded dataset versions. Schedule controls and completion/
  failure notifications are under **Activity & teams**. The application and
  workers must remain running. Delivery is in-app, not email or push.
- **Documents:** PDF, DOCX, UTF-8 text and Markdown uploads support bounded
  extractive search/overview workflows, quoted evidence and source-passage
  inspection. This is keyword-ranked extraction, not semantic RAG or free-form
  generated answering. Scanned PDFs require external OCR; encrypted PDFs are
  rejected. Table and document inputs use separate workflows.
- **Team spaces:** invite an email as viewer or editor; recipients accept in-app.
  Explicitly share an immutable dataset/conversation snapshot. Every snapshot
  download checks current membership and the exact artifact allowlist. Viewers
  read/download; editors create private editable copies and execute them in their
  own account. Originals are never edited collaboratively. Removing membership or
  stopping a share revokes future snapshot access; downloaded/private copies
  cannot be recalled.

All tools use the existing authenticated API. Team sharing does not widen the
global owner filter. No third-party model or mail service is required for
document extraction, exports, charts or in-app notifications. PDF exports use an
embedded font with limited character coverage; Excel/CSV retain Unicode text.

## Partitioned execution and planning controls

Large-table aggregations (50,000 or more source rows) can expand into independent
immutable chunk loads, row-local transformations and partial aggregations, followed
by a reducer. Sum, non-null count, row count, min, max and weighted mean are
supported. Mean combines partial sums/counts; it never averages chunk averages.
Filters, conversions, arithmetic measures, time buckets and explicit missing-value
fills can execute before each partial aggregation.

Joins, exact distinct counts, median, IQR and other global operations retain their
original execution path. Expansion is bounded to 96 chunks per aggregation and
256 total DAG nodes; fallback reasons appear in estimate details. Chunk files are
read independently by workers. The reducer still needs sufficient memory for its
partial states and output groups. Existing upload/table/intermediate row limits
remain unchanged. Reassociation can change the last floating-point digits.

Every unstarted preview shows an advisory execution-time estimate, declared
largest-task memory and compatible live-worker capacity. Details identify blocked
tasks and explain the estimator. Historical accepted durations or cold-start
per-row priors feed a dependency/resource-aware list simulation. Accepted/reused
outputs are skipped. This is not a calibrated timing guarantee or a measured peak
memory figure; planning, chunk preparation, competing queues and variable storage
overhead are excluded.

**Edit plan (optional)** opens form controls for filters, grouping, metrics and
composite equality-join keys/semantics. Users can add a source filter or disable
partitioning. Edited joins require explicit confirmation; many-to-many joins
require an additional opt-in and still obey row limits. Validation and join
preflight must succeed before a new preview version is saved. The earlier
unstarted preview is marked superseded/cancelled; running/completed workflows
cannot be edited. The normal Run button does not require opening the editor.

Dataset tools include **Drift**. Version uploads immediately compare the new
version with the selected baseline: added/removed columns, changed inferred types,
missing-rate changes of at least 10 percentage points and numeric empirical CDF
(KS) distances of at least 0.20. Distribution comparison needs at least 20
nonmissing numeric observations in each version. These are review heuristics, not
significance tests or claims that a changed dataset is wrong. Original files,
profiles and previous results stay immutable.
