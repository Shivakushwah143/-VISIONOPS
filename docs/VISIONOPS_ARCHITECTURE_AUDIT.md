# VisionOps — architecture audit

Findings from a full read of the repository plus executed probes on this host.
Severity: **P0** breaks correctness or the demo · **P1** meaningful production issue ·
**P2** optimisation or cleanup.

**Nothing here was fixed except P0-1**, which was a one-token change required to make the
documented `docker compose up --build` path work at all. The rest is reported, not patched,
so the findings stay reproducible.

Architecture context: [VISIONOPS_END_TO_END_ARCHITECTURE.md](VISIONOPS_END_TO_END_ARCHITECTURE.md).
Demo implications: [VISIONOPS_INTERVIEW_DEMO_FLOW.md](VISIONOPS_INTERVIEW_DEMO_FLOW.md).

---

## P0 findings

### P0-1 — The frontend production build fails, so `docker compose up --build` cannot complete — **FIXED**

| | |
| --- | --- |
| **Evidence** | Build log: `src/main.tsx(43,826): error TS2345: Argument of type '(r: string) => JSX.Element' is not assignable to parameter of type '(value: unknown, index: number, array: unknown[]) => Element'`, followed by `[frontend build 6/6] RUN npm run build` → `exit code: 1` and `target frontend: failed to solve`. The whole `up -d --build` aborted, so **no service started**. |
| **Cause** | `frontend/src/main.tsx` line 43: `[...new Set(detail.gate_reasons)]` where `detail` is typed `Record<string, any>`. `new Set(any)` infers `Set<unknown>`, and the callback `(r: string)` is then incompatible. |
| **Impact** | The repository's documented one-command startup produced no console image at all. The `frontend` service — and therefore every UI journey in the demo — was unreachable via `--build`. |
| **Fix applied** | One explicit type argument: `[...new Set<string>(detail.gate_reasons as string[])]`. Verified by rebuilding: `visionops-frontend` rebuilt successfully and the failure disappeared. |
| **Follow-up (not done)** | Type the API rows (`Row = Record<string, any>`) as real interfaces, or derive them from `shared/contracts/openapi.json`. The current `any` is why the compiler could not catch this at the API boundary — and it is the same looseness that makes the stale OpenAPI snapshot (P1-3) dangerous. |

### P0-2 — No `.dockerignore`: the signing private key, `.env` and operator credentials are baked into every image

| | |
| --- | --- |
| **Evidence** | `ls -la .dockerignore` → `No such file or directory`. `infrastructure/compose.yaml` builds four services with `context: ..` (the repository root). `infrastructure/docker/backend.Dockerfile` runs `COPY . /app`. Executed probe of the built artifact: `docker run --rm --entrypoint sh visionops-backend:latest -c "find /app …"` returns **`/app/var/keys/qual.key`** (the Ed25519 **release-signing private key**), `/app/var/keys/qual.pub`, `/app/var/trusted_keys/qual.pub`, **`/app/.env`**, **`/app/var/hansung-sprint.env`** (demo operator email + plaintext password), `/app/.env.example`, release manifests, model artifacts (`/app/var/model/hansung-p3.onnx`), simulation state, logs and downloaded wheels (`/app/var/tools/*.whl`). |
| **Cause** | `.gitignore` lists `.env`, `.venv/`, `var/`, `*.key` — but Docker honours only `.dockerignore`, and none exists. `COPY . /app` therefore copies everything not excluded by a (missing) dockerignore. |
| **Impact** | **(a) The release trust boundary described in §10 of the architecture document is void.** A device accepts any manifest signed by `qual.key` and verified against `qual.pub`. Anyone holding `visionops-backend:latest` (or `-controller`, `-migrate`, `-mlflow` — same image, four tags) holds that key. **(b) Secret leakage**: `TOKEN_PEPPER` (which peppers every session/CSRF/credential hash), the PostgreSQL password and the Grafana admin password are readable from the image. **(c) No audit catches this**: `scripts/make_jetson_bundle.py` and `scripts/package_release.py` secret-scan and exclude `*.key`, so the key is protected in archives but not in images. **(d) Image bloat**: the runtime image carries `var/` (model artifacts, 538 MB of `var/tools` wheels/logs) and, on the next build, `.venv`. **(e) Build cost**: the task's observed symptom — the backend/controller/mlflow build context exceeding 224 MB and still growing — is this; a single build showed 49.57 MB then 56.23 MB transferred per service, and `var/` alone measures **831 MB** today (`var/tools` 538 MB, `var/simulation` 131 MB, `var/model` 123 MB, `var/bundle` 27 MB), before `.venv` (a full PyTorch + ONNX Runtime virtualenv) is counted at all. |
| **Timing detail** | The image's `/app` has no `.venv` because `.venv` was created after that image was built. The next build will include it — which is exactly consistent with the reported ">224 MB and growing". |
| **Safest minimal fix** | Add a root `.dockerignore` (no Dockerfile or Compose change needed): `.venv/`, `var/`, `.git/`, `node_modules/`, `__pycache__/`, `*.pyc`, `.env`, `*.key`, `*.log`, `frontend/dist/`, `docs/`, `*.whl`. Then narrow the copy in `backend.Dockerfile` to `COPY backend edge shared training scripts requirements.lock uv.lock pyproject.toml ./` so a new `var/` file can never leak again. Rotate `POSTGRES_PASSWORD`, `TOKEN_PEPPER`, `GRAFANA_ADMIN_PASSWORD` and regenerate the signing key, because the current values are already inside a distributed artifact. |
| **Can existing images be reused?** | **Yes, for the demo.** The images are content-current: sha256 of `backend/app/main.py`, `backend/app/campaigns.py` and `edge/pipeline.py` inside `visionops-backend:latest` are byte-identical to the working tree (`af203f28…`, `de2d27ea…`, `d196e400…`). So `docker compose up -d` (no `--build`) faithfully runs HEAD. **No**, for any distribution: the key and secrets inside them must not leave this machine. |

---

## P1 findings

### P1-1 — Status documents still report canary and central rollback as unverified, contradicted by retained runtime evidence

| | |
| --- | --- |
| **Evidence (documents)** | `docs/VERIFICATION_REPORT.md`: "Canary campaign execution and central rollback against PostgreSQL \| **BLOCKED**". `docs/KNOWN_LIMITATIONS.md`: "Campaign transactions and central rollback remain unexercised" and, under *Implemented but not runtime verified*, "canary progression and central rollback". `docs/FAILURE_VERIFICATION.md`: "Unhealthy canary / rollback \| **IMPLEMENTED — NOT RUNTIME VERIFIED**". `docs/ROLLBACK_STRATEGY.md`: "Central rollback with a live database \| IMPLEMENTED — NOT VERIFIED". |
| **Evidence (runtime)** | `var/hansung-canary-cleanup.json`: `campaign_id 3d28133e-…`, `status_before "paused"` → `status_after "rolled_back"`, `rolled_back: true`, `distribution {"v1": 10}`, every device `converged: true` and `health_status: healthy`. `var/hansung-canary-campaign.json`: campaign created, ring-0 canary `1c88609a-…` assigned generation 4, 10/10 devices eligible. Fleet state directories under `var/simulation/canary/*` with real `state.sqlite` files corroborate real agent processes. |
| **Impact** | The repository **understates its own strongest verified journey**. An interviewer reading the status docs would conclude the highest-value capability (canary → pause → rollback → recovery over PostgreSQL) is unimplemented, when it has been executed. This is the mirror image of the usual failure mode and is just as misleading. |
| **Fix** | Reconcile the four documents against the evidence. Status must be derived from evidence files, not from the date a document was written. (This audit records the corrected classification: canary ring assignment **VERIFIED**, central rollback + recovery **VERIFIED**, failure→pause **SIMULATED** because the cause chain is not retained — see P1-2.) |

### P1-2 — The canary phase-A / phase-B evidence files are referenced by the script but absent from the tree

| | |
| --- | --- |
| **Evidence** | `scripts/hansung_canary.py::phase_a()` writes `var/hansung-canary-phaseA.json`; `phase_b()` writes `var/hansung-canary-phaseB.json`. `ls var/hansung-canary-phase*` → `No such file or directory`. Only the `cleanup` phase's output (`var/hansung-canary-cleanup.json`) survives. |
| **Impact** | The **causal chain** for the pause is not retained: the reason codes, `failure_windows` count, per-target `gate_signals`, the `controller restart preserved state` assertion, the "no unintended rollout" check for the other nine devices, and the campaign event timeline all lived in those two files. What remains proves *that* the campaign reached `paused` and *that* rollback completed — not *why* it paused. The single most valuable demo journey therefore has an evidence hole exactly where a sceptical reviewer would look. |
| **Fix** | Re-run the two phases and retain both JSON files (they are small), or extend `phase_cleanup()` to record the gate reasons, `failure_windows` and campaign timeline it observes **before** it issues the rollback. Either way, the pause reason must come from the application's own gate output (as `probe_eligibility()` already does via `docker compose exec backend`), never from a reimplementation. |

### P1-3 — The checked-in OpenAPI contract is a stale snapshot, and the route audit silently masks two endpoints

| | |
| --- | --- |
| **Evidence** | `shared/contracts/openapi.json` contains **59** paths and `grep -c hardware-profiles` → **0**, while `backend/app/main.py` defines `@app.get('/api/v1/hardware-profiles')`. `docs/CURRENT_VERIFIED_STATE.md` §6.1 admits the snapshot was not regenerated because `scripts/audit_release.py` imports `backend.app.main` (needs FastAPI). Separately, `docs/evidence/contract-audit.json` reports `expected_method_paths: 72`, `actual_method_paths: 71`, `missing: []` — the empty `missing` is only possible because `audit_release.py` hardcodes `not(m=='GET' and p in ('/model-artifacts/{model_artifact_id}/content','/runtime-artifacts/{runtime_artifact_id}/content'))`, i.e. it excuses two endpoints it knows are merged into one handler. |
| **Impact** | Anyone building a client from the checked-in schema misses a real endpoint and must discover the discrepancy by 404. And the audit's headline "0 missing" overstates coverage: one endpoint is genuinely absent from the spec's expected set and two are hardcoded as acceptable gaps, without that being visible in the report. |
| **Fix** | `docker compose exec backend python -m scripts.audit_release` and commit both regenerated contracts. Make the two exceptions an explicit, named field in the report (e.g. `known_merged_endpoints: [...]`) rather than an inline `not(...)` in the comparison. Note: WebSocket routes never appear in an OpenAPI schema, so `/api/v1/ws/events` being absent is **not** a defect. |

### P1-4 — Table count disagrees between executed reality and three documents (30 vs 31)

| | |
| --- | --- |
| **Evidence** | Executed: `len(Base.metadata.tables)` → **30**. `grep -c "CREATE TABLE" shared/contracts/postgresql-schema.sql` → **30**. `docs/evidence/contract-audit.json` → `postgresql_tables: 30`. But `docs/VERIFICATION_REPORT.md` says "migrations applied, 31 tables present" and `docs/KNOWN_LIMITATIONS.md` says "the 31-table schema is live". |
| **Impact** | Low in isolation, high in context: it is a countable number that a reviewer can check in one command, and it is wrong in two of the three documents that state it. It invites doubt about the other counts (16 metric families, 72 routes, 59 paths). |
| **Fix** | Correct both documents to 30, or add a generated assertion so the number can never be hand-written again. |

### P1-5 — The TensorRT reproduction command references a `samples/` directory that does not exist

| | |
| --- | --- |
| **Evidence** | `docs/evidence/tensorrt/final/README.md` "Reproduce" block: `python -m scripts.verify_tensorrt_gpu --onnx var/model/hansung-p3.onnx --contract-record var/model/hansung-p3.json --samples-dir samples --precisions fp32 …`. `ls -d samples` → `No such file or directory`. The real qualification frames are `var/evidence/hansung-ppe-frame-{000,050,100,150,200}.png`, driven by `--frames 0,25,50,100,150,200` against `var/media/ppe-2.mp4`; `scripts/make_gpu_bundle.py` is what actually transports them. |
| **Impact** | The most impressive claim in the repository (TensorRT on a real Tesla T4) has a reproduction recipe that fails on its first argument. The evidence itself is sound and byte-for-byte preserved; only the recipe is wrong. |
| **Fix** | Correct the command to point at the frames that exist (or ship a six-frame `samples/` directory, as the Jetson bundle builder already does), and state the exact provenance of those frames. |

### P1-6 — `scripts/detect_environment.py` hardcodes an obsolete PostgreSQL blocker into freshly generated evidence

| | |
| --- | --- |
| **Evidence** | `scripts/detect_environment.py` writes a literal `postgresql_startup` block: `{'status':'BLOCKED','evidence':['…runuser -u oai -- id: cannot set groups: Operation not permitted','CapEff=0000000000000000; seccomp enabled; PostgreSQL requires non-root OS identity'],'fallback':'No central SQLite replacement used'}` — unconditionally, with no probe. The current host runs Docker 29.2.1 and the PostgreSQL 16.11 stack; `docs/KNOWN_LIMITATIONS.md` itself now says "RESOLVED (partially): PostgreSQL runtime. Docker Compose is now available and the stack runs". |
| **Impact** | Re-running the environment probe **reproduces a false blocker in a new evidence file**. That is the single most damaging class of defect in an evidence-driven project: a generated artifact asserting a limitation that no longer exists. |
| **Fix** | Probe reality before recording it — e.g. attempt `docker compose ps postgres` / a real `psycopg` connect, and record `BLOCKED` only on failure, with the probe output attached. |

### P1-7 — The retained release evidence predates `release_identity()`, so the bundle-identity claim is code-verified but evidence-stale

| | |
| --- | --- |
| **Evidence** | `backend/app/lifecycle.py::create_release()` builds the manifest as `dict(… , **release_identity(db,a,r,c,e))`, and `release_identity()` returns `architecture`, `runtime`, `runtime_version`, `model_format`, `model_precision`, `input_shape`, `class_mapping_version`, `model_contract_profile`, `class_mapping`, `model_head_channels`, `dataset_version`, `source_mlflow_run`, `evidence_ref`. The **actual** manifest in `var/hansung-release.json` has exactly **16 keys** — `schema_version`, `release_id`, `hardware_profile`, `evidence_mode`, `model_artifact_id`, `model_sha256`, `model_size_bytes`, `runtime_artifact_id`, `runtime_sha256`, `runtime_size_bytes`, `config_version_id`, `config_sha256`, `evaluation_report_id`, `compatibility`, `entrypoint` — and **no** identity block. `docs/CURRENT_VERIFIED_STATE.md` states the identity fields "are recorded in every signed release manifest". |
| **Cause** | The evidence file was produced on 2026-09-16 by a container built from older code; the release-identity block was added to `lifecycle.py` afterwards. The current image does match HEAD (see P0-2), so this is an **evidence/implementation skew**, not a code defect. |
| **Impact** | A reader who follows the documented verification step will hit `KeyError: 'class_mapping_version'`, and a reviewer cannot confirm from evidence that a release pins the mapping version — which is the very mechanism that is supposed to stop an artifact from being activated against a runtime built for a different interpretation. The code is right; the proof is missing. |
| **Fix** | Create one fresh release with the current image (same qualified bytes, new version label) and retain its JSON, so the identity block is evidenced. Until then, the manifest-identity claim must be labelled code-verified only. **Propagated into** the architecture document (§4) and the demo flow (step 10). |

### P1-8 — A document references an evidence file that does not exist

| | |
| --- | --- |
| **Evidence** | `docs/interview/11_VERIFIED_VS_UNVERIFIED.md` cites `final/fp32/onnx_cuda_baseline.json` for the "ONNX Runtime CUDA baseline \| NOT VERIFIED" row. `find docs/evidence/tensorrt -name "*cuda*"` returns **nothing**; the directory holds `benchmark.json`, `contract-semantics.json`, `environment.json`, `GPU_RUN_RESULT.md`, `parity_fp32.json`, `samples.json`, `status-patch.json`, `SUPERSEDED.md`, `tensorrt-verification.json`. The CUDA facts do exist — inside `fp32/tensorrt-verification.json` (`onnxruntime_providers`, `onnxruntime 1.24.4`, `cuda 12.8`, `cuda_available: true`). |
| **Impact** | This is the failure mode the whole evidence regime exists to prevent: a **status table pointing at a file that is not there**. It is a dangling citation in the document whose entire purpose is trustworthy citation, and the status doc for the GPU work is the one an interviewer is most likely to open. |
| **Fix** | Repoint the row at `fp32/tensorrt-verification.json`, and add a cheap guard — a script (or a CI step) that extracts every ``docs/evidence/**.json`` path mentioned in Markdown and fails when one is absent. That single check would have caught this and would catch its successors. |

---

## P2 findings

### P2-1 — Metric names are declared twice with nothing asserting they agree

`backend/app/pipeline_metrics.py::FIELDS` enumerates the 14 `visionops_camera_<field>`
families; `observability/grafana/dashboards/operations.json` references the same names by
literal string in 16 panels. Nothing links them, so renaming a field in the collector
silently turns a Grafana panel blank — a failure mode this project otherwise treats very
carefully (absent ≠ zero), except here "absent" would be an accident rather than a fact.
**Fix:** generate the panel expressions from the same mapping, or add a verification script
that parses both and fails on divergence.

### P2-2 — Four services share one 1.88 GB image, and the whole repository is copied into it

`backend`, `controller`, `migrate` and `mlflow` are the same build tagged four ways
(`docker images` shows four `visionops-*` tags at 1.88 GB). That is defensible — they share
one dependency set — but it means the MLflow server image also contains the edge agent,
OpenCV, the training code and `docs/`, and that changing any backend file invalidates all
four tags plus the build cache for each. Combined with `COPY . /app` (P0-2), the runtime
image is a copy of the repository.

**Fix:** narrow the `COPY` set; keep the single shared base image (correct) but add a
`target: runtime` stage that installs only what the API needs, and document that MLflow
intentionally reuses the application image so `scripts/mlflow_rest.py`'s absence-of-`mlflow`
rationale stays true.

### P2-3 — Overlapping status documents (four claimants to the same truth)

`docs/CURRENT_VERIFIED_STATE.md`, `docs/VERIFICATION_REPORT.md`,
`docs/KNOWN_LIMITATIONS.md`, `docs/IMPLEMENTATION_REPORT.md`, `docs/FAILURE_VERIFICATION.md`,
`docs/ROLLBACK_STRATEGY.md`, `docs/HARDWARE_COMPATIBILITY.md`, `docs/LOCAL_RTSP.md`,
`docs/DEMO_PLAN.md`, `docs/10K_FLEET_SCALING_REPORT.md` and `docs/ARCHITECTURE.md` each carry
their own status tables, and most now open with a "Continuation N update" pointer to
`CURRENT_VERIFIED_STATE.md`. P1-1 is the direct consequence: stale claims survive in the
older documents because there is no single owner of the status. **Fix:** make
`CURRENT_VERIFIED_STATE.md` the only status document; reduce the others to either evidence
procedures or historical records that carry a superseded banner.

### P2-4 — Verification scripts whose environment no longer matches their evidence

`docs/evidence/service-runtime.json` (liveness 200, `/health/ready` **503**, OpenAPI 59
paths, frontend HTTP 200) was produced when the host venv could import FastAPI. Today
`.venv` has **no** `fastapi`, `sqlalchemy`, `mlflow`, `alembic` or `prometheus_client`
(verified by import), so `scripts/verify_runtime.py`, `scripts/verify_security.py` and
`scripts/audit_release.py` cannot run on the host at all — they need the container. A reader
who assumes those files are reproducible natively will get an ImportError. **Fix:** state
the required environment in each script's docstring and make the script fail with an
actionable message ("run inside the backend container") instead of an opaque import error;
or move them to `docker compose exec backend …` form in the docs.

Related: `scripts/verify_components.py`'s Windows `WinError 32` fix is verified, and the
edge-side scripts (`verify_components`, `verify_model_contract`, `verify_edge_platform`,
`verify_continuation`, `verify_quality_math`, `export_onnx`) **do** run natively — that
split is the useful fact to document.

### P2-5 — `docs/10K_FLEET_SCALING_REPORT.md` is stale in the same way as P1-1

The report states "10K LOGICAL FLEET CONTROL-PLANE SIMULATION: IMPLEMENTED — NOT RUNTIME
VERIFIED / MEASURED LOCAL RESULTS: NONE" and justifies that with "the control plane cannot
be started on this host". PostgreSQL now runs in Docker, so the driver is runnable today at
`--devices 100` and `--devices 1000`. **Fix:** either run it and record real percentiles, or
narrow the claim to "not run in this campaign" and stop asserting an environment blocker
that no longer exists (same root cause as P1-6).

### P2-6 — Duplicated "logical device" concept across two simulators

`simulation/fleet_scale.py::LogicalDevice` and the identity handling in
`simulation/run.py` / `scripts/seed_fleet.py` model the same idea three ways with no shared
definition (`seed_fleet` creates inventory-only rows through the API; `run.py` starts 10–50
real agent threads; `fleet_scale.py` keeps 10 000 in-process records). **Fix:** extract the
record-shape contract so the three agree on what fields a simulated device reports, and so
none of them can accidentally imply a physical device.

---

## Docker build-context investigation (requested in full)

**Symptom.** `compose up --build` shows the backend/controller/mlflow build context
exceeding 224 MB and continuing to grow.

**Actual cause.** There is no `.dockerignore` at the repository root, and the Compose build
context for the backend/migrate/controller/mlflow services is `..` — the repository root
(`infrastructure/compose.yaml`, `build: {context: .., dockerfile: infrastructure/docker/backend.Dockerfile}`).
`backend.Dockerfile` then runs `COPY . /app`. BuildKit transfers the entire context minus
`.dockerignore` rules; with no such file, **nothing is excluded**.

**Largest unnecessary entries entering the context**

| Entry | Size / nature | Why it is sent |
| --- | --- | --- |
| `var/` | **831 MB** total — `var/tools` 538 MB (including `torch-2.14.0+cpu-…whl`, `scipy-1.16.3-…whl`, `.pt` checkpoints, logs), `var/simulation` 131 MB (per-device `state.sqlite` + slots), `var/model` 123 MB, `var/bundle` 27 MB | only `.gitignore` excludes it; Docker does not read `.gitignore` |
| `.venv/` | a full PyTorch 2.14.0 + ONNX Runtime + OpenCV virtualenv (its `Lib/site-packages` holds `torch`, `torchvision`, `onnxruntime`, `av`, …) | same |
| `.git/` | 1.6 MB | same |
| `frontend/node_modules/` | absent on this host but would be included when present | same |
| `.env`, `var/keys/qual.key`, `var/hansung-sprint.env` | secrets (see P0-2) | same |

**Safest minimal fix.** One new file, no Dockerfile or Compose edits required:

```gitignore
# .dockerignore  (new file, repository root)
.venv/
var/
.git/
node_modules/
**/__pycache__/
*.pyc
.env
*.key
*.log
*.whl
frontend/dist/
docs/
```

Then optionally harden the Dockerfile so the class of problem cannot return:
`COPY backend edge shared training scripts requirements.lock uv.lock pyproject.toml ./`.

**Can existing images be reused?** **Yes.** Verified content-current, not guessed: sha256
inside `visionops-backend:latest` equals the working tree for `backend/app/main.py`
(`af203f28828b6a5f206f4dbb7029e658`), `backend/app/campaigns.py`
(`de2d27ea6c304e75e55d2eb53c78ad8f`) and `edge/pipeline.py`
(`d196e400a99eef3897f6fc9e41cd476b`). `docker compose up -d` without `--build` therefore
runs HEAD. **The caveat is not fidelity but custody**: those images contain the `.env`, the
operator credential and the signing private key, so they may be reused locally and must be
rebuilt before they are pushed anywhere or shared.

---

## Documentation reconciliation

This audit is itself part of the reconciliation: it derives status from evidence files
rather than from the documents that describe them. The deltas it recommends applying are:

| Document | Stale claim | Correct position (with evidence) |
| --- | --- | --- |
| `docs/VERIFICATION_REPORT.md` | "Canary campaign execution and central rollback against PostgreSQL \| BLOCKED" | Canary ring-0 assignment **VERIFIED** (`var/hansung-canary-campaign.json`); central rollback + recovery **VERIFIED** (`var/hansung-canary-cleanup.json`); failure→pause **SIMULATED** (mechanism real, cause chain not retained) |
| `docs/KNOWN_LIMITATIONS.md` | "Campaign transactions and central rollback remain unexercised"; "canary progression and central rollback" under NOT RUNTIME VERIFIED | as above |
| `docs/FAILURE_VERIFICATION.md` | "Unhealthy canary / rollback \| IMPLEMENTED — NOT RUNTIME VERIFIED" | Central rollback **VERIFIED**; the *injection→pause* step is **SIMULATED** and its reason-code chain is missing (P1-2) |
| `docs/ROLLBACK_STRATEGY.md` | "Central rollback with a live database \| IMPLEMENTED — NOT VERIFIED" | **VERIFIED** — `paused → rolled_back`, 10/10 devices back on `ppe-hansung-v1`, healthy, converged |
| `docs/10K_FLEET_SCALING_REPORT.md` | "the control plane cannot be started on this host" | PostgreSQL + FastAPI run in Docker; the driver is runnable, and remains **not run** (P2-5) |
| `docs/VERIFICATION_REPORT.md`, `docs/KNOWN_LIMITATIONS.md` | "31 tables" / "31-table schema" | **30** (P1-4) |
| `docs/evidence/service-runtime.json` (file, not doc) | implies a rerunnable native probe | the host venv lacks FastAPI; regenerate inside the container (P2-4) |
| `docs/evidence/environment.json` (file, not doc) | `postgresql_startup: BLOCKED` | obsolete; `detect_environment.py` hardcodes it (P1-6) |
| `docs/interview/11_VERIFIED_VS_UNVERIFIED.md` | cites `final/fp32/onnx_cuda_baseline.json` | file does not exist; the facts live in `fp32/tensorrt-verification.json` (P1-8) |
| `docs/CURRENT_VERIFIED_STATE.md` | "the identity fields … are recorded in every signed release manifest" | true of current code, **not** of the retained evidence manifest (P1-7) |

Claims that are **already correct and must not be weakened**: physical Jetson, JetPack,
Jetson TensorRT, Jetson DeepStream, NVDEC, Jetson power/thermal, ARM64 execution, GPU
utilization/VRAM/power/TOPS, model quality, WebSocket delivery, GStreamer runtime and real
RTSP are all `BLOCKED`/unverified and remain so here. The Tesla T4 result is real and stays
scoped to an external NVIDIA GPU.
