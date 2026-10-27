# Retail workflow intent LoRA pilot

This is a genuinely trained parameter-efficient adapter for `Qwen/Qwen2.5-0.5B-Instruct`, rather than an API wrapper or a prompt-only change. The base model stays frozen. AdamW updates rank-8 LoRA matrices on all 48 query/value attention projections: **540,672 trainable parameters**. The saved `artifacts/intent-lora/adapter.pt` is 2,192,899 bytes.

The task is narrow: emit the approved retail intent JSON, preserve the IQR value, and serialize supplied contextual sections and product/month scope. Context has already been resolved by the trusted application compiler. This adapter does not independently interpret arbitrary analysis domains, execute code, calculate financial results, select hardware, or guarantee correct answers to every follow-up. Training does not replace strict validation.

## Actual run and artifacts

- Base snapshot: `7ae557604adf67be50417f59c2c2f167def9a775`.
- CPU training with Torch 2.14.1 and Transformers 5.19.0; no GPU, provider calls or paid service.
- 104 available synthetic examples; **80 unique examples actually consumed**, one example per optimizer step. This was a partial pass, not a complete epoch.
- 80 optimizer steps, rank 8, alpha 16, learning rate 0.001, seed 20261008, eight CPU threads.
- Measured optimization time: **164.625 seconds**. Teacher-forced loss decreased from 0.51308 to about 0.00009; this training loss is not a held-out quality score.
- Adapter SHA-256: `e17ab2965f52b215d1ae41db5ed35e5688dd4c5c04594f7ab156d5c985919121`.
- `adapter_config.json` records configuration, loss trace, nonzero learned matrix values, dataset hashes, tokenizer hash and adapter hash. `evaluation.json` binds the measured results to these adapter bytes.

## Held-out results

Ten held-out prompts use distinct wording and unseen product IDs, months and IQR values. Targets are synthetic and supplied context is known. Both models use the same production `app.intent_schema.intent_prompt`, greedy decoding, the same base weights and held-out file. No model correction turn, alias normalization, legacy-key normalization or compiler parameter override contributes to these scores.

| Metric on first response | Frozen base | Trained adapter |
|---|---:|---:|
| Strict raw JSON plus schema and required values | 8/10 | 10/10 |
| Exact parsed target object | 8/10 | 10/10 |
| Production-style parsing allowing outer JSON fences | 9/10 | 10/10 |

One baseline failure added an unsupported `iqr_multiplier` inside `scope`; another wrapped otherwise correct JSON in Markdown fences. All trained responses were bare JSON with the target values. Complete outputs, errors and per-case durations are retained in `before.json` and `after.json`. The early evaluation function allowed outer fences; a subsequent raw-output audit recomputed strict scores without modifying model responses. Future runs evaluate raw JSON directly.

This is a small pilot with two corrected cases. It does not establish a reliable population improvement, broad reasoning ability, or a novel training algorithm. Expanding independently labelled tests and collecting real failure cases is necessary before a research-quality claim. Latency differences in these short CPU runs are not a controlled performance claim. `reload-after.json` and `reload-evaluation.json` independently evaluate the saved weights after loading into a fresh base model.

## Reproduce

Run in the application folder with the workspace Python environment and the already downloaded base snapshot:

```powershell
../.venv/Scripts/python.exe -m training.make_dataset
../.venv/Scripts/python.exe -m training.train_adapter --model-cache ../.local_services/hf_cache/hub/models--Qwen--Qwen2.5-0.5B-Instruct/snapshots/7ae557604adf67be50417f59c2c2f167def9a775 --steps 80 --threads 8
../.venv/Scripts/python.exe -m training.evaluate_adapter --model-cache ../.local_services/hf_cache/hub/models--Qwen--Qwen2.5-0.5B-Instruct/snapshots/7ae557604adf67be50417f59c2c2f167def9a775 --adapter training/artifacts/intent-lora
../.venv/Scripts/python.exe -m pytest training/test_lora.py -q
```

Regenerating datasets intentionally uses the current shared prompt. Changing that prompt changes dataset hashes, so such a run is a new experiment. Floating-point results can vary by CPU/runtime; recorded raw outputs describe this run.

## Inference integration

Set `HF_ADAPTER_PATH` to the artifact directory before starting `app.llm_server`. The server checks that evaluation exists, has no held-out regression, and is bound to the loaded adapter hash. It also checks the base model revision, parameter keys, shapes and finite values. Base-only startup remains possible when the variable is absent. The server health and completion payloads expose `training` with `fine_tuned`, method, steps, adapter SHA and trainable parameter count; the application can persist that record in workflow provenance. No API credentials are required.

The Docker image needs both `app` and the `training.lora` runtime module; weights can be mounted read-only. The adapter weights are separate from the unchanged base checkpoint. Runtime outputs still pass the application schema, context checks, approved catalogue compiler and DAG validator.

## Sources

The method follows the established low-rank update/frozen-base idea in [Hu et al., LoRA: Low-Rank Adaptation of Large Language Models](https://arxiv.org/abs/2106.09685). This implementation applies it specifically to Qwen query/value projections and does not reproduce the paper's large-scale benchmarks. The [official Qwen model card](https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct) identifies the base model and Apache-2.0 license. Synthetic local labels contain no uploaded customer dataset or personal data.
