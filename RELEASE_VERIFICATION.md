# Release verification — 27 October 2026

Scope: account-enabled, same-host FlowPilot tabular-analysis product.

| Check | Measured result |
|---|---|
| Backend regression/security suite | 177 passed against real PostgreSQL workflow_test |
| Frontend unit suite | 23 passed |
| TypeScript and production frontend build | Passed |
| Three-worker Docker acceptance | Nine workflows completed; A accepted 14 tasks, B 7, C 6 |
| Real worker termination | Leased task expired, retried and completed; passed |
| Service restart | Completed workflow and accepted artifacts persisted; passed |
| Browser public registration and sign-in | Passed with a synthetic local test account |
| Browser conversational follow-up | Two turns retained in one sidebar chat; separate DAG versions |
| Real local-model follow-up | Average score by class: A=86, B=77, matching manually calculated results |
| Account isolation | Dataset/workflow/clarification/download/session cross-access rejected |
| Password/email controls | Rotation, revocation, one-use tokens, expiration and configuration checks passed |

The email tests use a fake delivery sink. No real SMTP provider was configured or
email delivery claimed. Production HTTPS hosting is not provisioned. The product
runs locally on port 18080. Existing anonymous records stay unassigned.

The restart exercise exposed redundant no-op table alterations and a database
startup race. Startup migrations now skip existing columns/indexes and serialize
real migrations; startup retries handle temporary database unavailability. A
regression test verifies that an existing-schema restart performs no table DDL.

Timing observations are functional acceptance evidence, not a new performance
benchmark or a claim of broader model superiority. Research/model-training results
from earlier work are not reclassified as production-security measurements.

Local raw evidence remains under the ignored evidence folder. No credentials,
raw session tokens, user uploads or local model weights are included in Git.

## Appearance update

The UI update passes 28 frontend tests and the TypeScript/production build.
New checks cover saved appearance settings, system-theme resolution, malformed
storage and theme initialization before React renders. Browser checks cover
light/dark themes, persistence after reload, the 230px Focused sidebar, desktop
and narrow-screen layouts, conversation history, themed DAGs and composer visibility.
Backend code is unchanged by this UI update; the previous 177-test backend result
remains the recorded backend validation.

## Product tools update — 27 October 2026

- **188 backend tests passed** against the isolated PostgreSQL workflow_test
  database. New checks cover paginated previews, stable column IDs in updated
  inputs, calculated exports, recoverable chat controls, feedback, reuse and
  invalidation, due schedules and notification deduplication, document parsers/
  exact source citations, inert injected document text, and team invitations,
  item allowlists, viewer/editor permissions, private copies and revocation.
- **32 frontend tests passed** and the TypeScript/production build succeeded.
  Chart checks cover missing/nonfinite values, signed axes and preview limits;
  conversation checks include pinned and document chats.
- Live Docker smoke checks completed tabular and document workflows through the
  frontend proxy with PostgreSQL and registered workers A, B and C. A follow-up
  reused two unchanged steps; CSV, Excel and parseable PDF downloads succeeded.
  This is a functional smoke check, not evidence that all three workers execute
  a single small workflow simultaneously.
- Browser checks covered dataset previews/distributions, chat controls, results
  charts/exports and version comparisons with the themed UI.

Known scope: documents use extractive keyword retrieval without OCR or semantic
generation; collaboration shares frozen snapshots and private editable copies;
schedule delivery is in-app while services are running. PDF font coverage is
limited. These tests do not constitute an independent security audit or a
performance/generalization benchmark.
