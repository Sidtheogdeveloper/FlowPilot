# Deployment and operations

## Local Docker installation

Copy `.env.example` to `.env` and choose a unique database password. Never commit
`.env`. Run `docker compose up --build -d` and open `http://localhost:8080`.
Create your own account through the registration form. No default administrator
password or account is shipped. Three worker processes run alongside the API,
frontend and PostgreSQL. The default deterministic overview works without a model.
Analytical natural-language requests require a configured compatible local model.

Set `APP_PUBLIC_URL=http://localhost:8080` in this setup. For the existing
verification installation the URL is `http://localhost:18080` and the compose
project is `workflow-phase2-verify`. Do not remove its database/artifact volumes.

## Model configuration

The current verification installation uses the existing Ollama `flowpilot-tables`
model via `compose.stronger.yaml`. That override expects model files under
`../.local_services/gguf` and an initialized Ollama model volume; weights are not
committed. New installations can use `TABLE_INFERENCE_URL`, `TABLE_MODEL` and
`TABLE_JSON_MODE=1` for their own OpenAI-compatible local endpoint. The legacy
retail model and separately trained adapters are supported through Docker profiles
`llm` and `table-llm`; their optional dependencies are in `requirements-llm.txt`.
Uploading datasets never retrains a model automatically.

## Before public hosting

Place the frontend behind an HTTPS reverse proxy with a valid certificate. Keep
the API and database private; expose only the frontend. Set `APP_ENV=production`,
`APP_PUBLIC_URL=https://your-host`, and `ALLOWED_ORIGINS=https://your-host`.
Production startup refuses missing HTTPS/origin/email configuration. Cookies
become Secure, and unverified accounts cannot access analytical endpoints.
Configure SMTP_HOST, SMTP_FROM and either STARTTLS on port 587 or SMTP_SSL=true
on port 465, plus SMTP_USERNAME/SMTP_PASSWORD when required by the provider.
Use a secret manager or a restricted env file; never add mail credentials to Git.
Verify actual email delivery before opening registration to the public.

Verification and password-reset links expire after 30 minutes and are single-use.
Local installations without SMTP show recovery as unavailable; they do not claim
an email was sent. Production SMTP must use TLS. ALLOW_REGISTRATION=false closes
new registrations while existing users can continue to sign in.

The API trusts the direct peer for throttling. Behind a reverse proxy this creates
an installation-wide IP limit as well as email-specific login limits; configure
edge per-client rate limits for a larger public deployment. No X-Forwarded-For
value is blindly trusted for authentication decisions.

## Data and backups

Database records and artifact volumes are persistent. Back up both together with
`pg_dump` and a volume snapshot. Restore to an isolated installation and verify a
known workflow and checksum before relying on the backup. Do not run acceptance
scripts that reset workflow_test against the workflow database.

Pre-account records remain unassigned. A workspace operator must explicitly
approve any migration; do not assign all historical data to the first registrant.
The product currently retains user uploads and versions until an operator applies
the installation's retention policy. It does not promise automatic erasure.

## Verification

Backend tests require a dedicated PostgreSQL database named `workflow_test` and
reset that database only. Run `python -m pytest -q tests` with DATABASE_URL set.
Frontend: `cd frontend`, `npm ci`, `npm test`, `npm run build`.
Live scripts now authenticate: set FLOWPILOT_TEST_EMAIL and FLOWPILOT_TEST_PASSWORD
for an explicit disposable test account, or use their interactive prompt.
`scripts/verify_three_workers.py` checks all three deployed workers.
`scripts/verify_tables_docker.py` kills a worker and restarts local services; run
it only when no other work is executing. Test fixtures contain synthetic data.

The application is a same-host Docker product with tabular-analysis functionality.
Public hosting, an independent penetration test, automatic backups/retention,
document analysis and broader model evaluation are separate deployment or future
work items, not features implied by these release branches.
