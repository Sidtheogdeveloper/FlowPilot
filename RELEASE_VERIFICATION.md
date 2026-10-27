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
