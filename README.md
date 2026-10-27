# Phase 3: accounts and private workspaces

Public registration and email/password sign-in now protect datasets, workflows and downloads. See [SECURITY.md](SECURITY.md). The account suite adds 10 PostgreSQL-backed security tests; all 160 existing backend tests and 20 frontend tests passed on 27 October 2026.

# FlowPilot

FlowPilot is a local conversational workspace for CSV and Excel analysis. It profiles selected tables, turns supported questions into typed analytical intent, compiles and validates a DAG, and runs approved operations on independent worker processes. Follow-ups create separate workflow versions and preserve previous results.

## Features

- CSV and XLSX ingestion with explicit worksheet selection and versioned column profiles.
- Clarification for ambiguous columns, types, dates and join choices.
- Filters, composite equality joins, restricted arithmetic, grouped and time aggregation, sorting, IQR outliers and narrative reports.
- DAG preview, task history, immutable artifacts and versioned conversations.
- PostgreSQL-backed coordination, resource-aware worker placement, leases, bounded retries and stale-result rejection.
- Greedy, HEFT-inspired and bounded joint-placement policies.
- Local Qwen3 4B planning through Ollama. Separate 0.5B LoRA training and evaluation source is provided under `training/`; generated weights and evaluation artifacts are excluded.



## Quick start with Docker

Requirements: Docker Desktop/Engine with Compose v2, internet for image/model downloads, and sufficient free RAM/disk for a 4B quantized model (allow several GB for weights and additional runtime memory). CPU inference can be slow.

1. Clone this repository and enter its directory.
2. Copy `.env.example` to `.env` and replace `POSTGRES_PASSWORD` with your own local password. Do not commit `.env`.
3. Download the pinned general-table model and prepare its Ollama definition:

   ```sh
   python scripts/download_table_model.py
   ```

   This uses Python's standard library and verifies the saved model SHA-256. The large base-model weights are downloaded separately; they are not included in this repository.

4. Start the model service and import the downloaded model:

   ```sh
   docker compose -f compose.yaml -f compose.stronger.yaml up -d table_ollama
   docker compose -f compose.yaml -f compose.stronger.yaml exec table_ollama ollama create flowpilot-tables -f /models/Modelfile
   ```

5. Build and start the application, database and two workers:

   ```sh
   docker compose -f compose.yaml -f compose.stronger.yaml up -d --build api worker_a worker_b frontend
   ```

6. Open **http://localhost:8080/** and select **General tables & joins**. API documentation is available at http://localhost:8000/docs.

The portable stack uses the regular `Dockerfile`; `compose.tables.yaml` is retained for compatibility and also builds from that file. `compose.verify.yaml` uses alternate ports 18080/18000 for isolated verification. 

### First analysis

Upload your own CSV or XLSX table, inspect its profile, and ask a question using its actual column names. Start with **Give me an overview** in template mode; analytical questions use the local model. Review any clarification and DAG preview before running. Follow up with a changed filter or aggregation; the previous version remains available. Joins require explicit confirmation, including a separate confirmation for many-to-many expansion.

### Stop services

```sh
docker compose -f compose.yaml -f compose.stronger.yaml down
```

Named volumes retain database, model and artifact data. Do not add `-v` unless you intend to delete those stored volumes.

## Local development

Use Python 3.12, Node.js and PostgreSQL 17. Create a virtual environment, install `requirements.txt`, and set `DATABASE_URL` and `ARTIFACT_ROOT` to a development database and an absolute shared directory.

```sh
pip install -r requirements.txt
uvicorn app.api:app --port 8000
```

Run `python -m app.worker` in two separate terminals with distinct `WORKER_ID` values. Set `TABLE_INFERENCE_URL=http://127.0.0.1:8003/v1/chat/completions` and `TABLE_MODEL=flowpilot-tables` for the local Ollama service started above.

```sh
cd frontend
npm ci
npm run dev
```

The development interface is at http://localhost:5173; Vite proxies `/api` to the API. The API documentation lists supported operations and endpoints.

## Tests and reproducibility

**Use a disposable database named `workflow_test`.** Integration and benchmark scripts reset application tables; never point them at retained application data. Set `DATABASE_URL` to that test database and `ARTIFACT_ROOT` to a separate test directory.

```sh
python -m pytest -q
cd frontend
npm ci
npm test
npm run build
```

Without a configured PostgreSQL URL, database cases may skip; skips are not passing integration evidence. Saved verification results are described in the accompanying project report; the repository contains test source rather than workstation evidence.

The saved checkpoint records 160 backend and 20 frontend tests passing. A first synthetic planning probe scored 89/90 supported requests and 10/10 clarifications; the later known-suite recheck scored 88/90 and 10/10. These are separate saved measurements, not a claim that a fresh installation has rerun them. Scheduler measurements used two worker threads in a test harness; the deployed Docker stack uses independent worker processes. Bounded search did not consistently improve total runtime over the compared policies.

## Models and training

- Active general-table runtime: [Qwen3-4B-Instruct-2507](https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507), Q4_K_M, downloaded rather than newly fine-tuned.
- Separate general-table adapter: generated locally under `training/artifacts/table-lora/`, trained from Qwen2.5-0.5B with rank-8 attention LoRA on 200 synthetic cases.
- Retail adapter: generated locally under `training/artifacts/intent-lora/`, kept separate.
- Training and evaluation code: `training/train_tables.py`, `training/verify_table_generalization.py`, and associated evaluation code.
- Uploading datasets triggers profiling and planning, **not automatic retraining**.

For optional small-model serving install `requirements-llm.txt`, configure `HF_ADAPTER_PATH`, `HF_MODEL` and `LLM_PORT`, and run `python -m app.llm_server`. Evaluate the generated adapter before choosing it; its narrow template score does not establish broad unseen-schema accuracy. Third-party model terms remain those supplied by their publishers.

## Repository layout

| Directory | Purpose |
|---|---|
| `app/` | API, ingestion/profiles, intent compiler, DAG validation, execution, scheduling and reports |
| `frontend/` | React/TypeScript interface, tests and production configuration |
| `tests/` | Backend correctness, state, security-contract and integration tests |
| `training/` | Adapter training, synthetic-data generation and evaluation source |
| `scripts/` | Setup, launch and failure/restart exercises |

This public source release excludes uploaded datasets, local credentials, cached dependencies, trained weights and saved runtime/evaluation artifacts. Generate training datasets and weights locally before optional adapter-integrity tests. The project report contains the measured checkpoint results; they have not been rerun merely by publishing this repository.

## Current boundaries

This is a single-host research prototype with independent processes and shared storage. It is not a production multi-tenant service: authentication, authorization, retention controls and multi-machine deployment need additional work. Document analysis, forecasting, fuzzy joins and arbitrary generated code are outside this implementation. Execution uses approved operations rather than generated Python or SQL. Outlier flags describe unusual numeric values, not proven fraud or causality.
