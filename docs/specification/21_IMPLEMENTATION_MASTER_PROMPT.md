# 21 — Ready-to-use implementation master prompt

Use this prompt in a later coding session with the complete `visionops-specification/` directory attached. This file instructs the later implementation agent; it does not authorize implementation during the current specification-only task.

---

You are the responsible senior engineer delivering Industrial VisionOps Platform. Build the specified system end to end and deliver a clean runnable project ZIP. Do not return a tutorial, disconnected scaffolding, architecture redesign or UI-only prototype.

## Read first

Read every file, not just a summary:

1. 01_UNDERSTAND_PROBLEM.md
2. 02_RESEARCH_AND_REUSE_STRATEGY.md
3. 03_PRD.md
4. 04_USER_STORIES.md
5. 05_FRD.md
6. 06_TECHNICAL_DESIGN.md
7. 07_LOCKED_TECH_STACK.md
8. 08_ZERO_ASSUMPTION_USER_JOURNEYS.md
9. 09_DOMAIN_MODEL.md
10. 10_API_CONTRACTS.md
11. 11_EDGE_AGENT_CONTRACT.md
12. 12_OBSERVABILITY_SPEC.md
13. 13_MODEL_EVALUATION_AND_BENCHMARK_PLAN.md
14. 14_DRIFT_AND_RETRAINING_SPEC.md
15. 15_SECURITY_SPEC.md
16. 16_FAILURE_MATRIX.md
17. 17_ACCEPTANCE_CRITERIA.md
18. 18_BUILD_PLAN.md
19. 19_PROJECT_STRUCTURE.md
20. 20_DEMO_PLAN.md
21. This 21_IMPLEMENTATION_MASTER_PROMPT.md
22. ASSUMPTIONS_AND_DECISIONS.md

Reconcile contradictions before edits. Priority: latest explicit user instruction → locked decisions → FRD → journeys → domain/API contracts → acceptance → PRD → stories → visual design → judgment. Fix each affected document, record material interpretation in docs/ASSUMPTIONS_AND_DECISIONS.md, and preserve canonical identifiers. Use the smallest compatible choice when a minor detail is missing; do not repeatedly ask for routine implementation decisions.

## Build objective

Implement R01–R13 as vertical UI→API→domain→database→edge/training→UI slices. Preserve React/Vite/Tailwind, FastAPI/Python, PostgreSQL, DVC/MLflow/PyTorch/YOLO, ONNX Runtime CPU, optional compatible DeepStream/TensorRT, Docker Compose and defined observability. Resolve exact package versions from installation and official compatibility evidence, lock dependencies/digests and keep imported packages declared. Do not add LLMs, RAG, Kafka, Redis, Kubernetes or needless microservices. Use mature decode, inference and tracking infrastructure instead of rebuilding it.

Implement the real CPU person/helmet/no_helmet detector and deterministic temporal safety rule. Obtain permitted data/weights with recorded provenance; a general person model is not PPE. Distinguish occlusion/unknown from visible bare head. Persist actual events via SQLite outbox, HTTP API and PostgreSQL; display real evidence and versions. Do not continuously upload raw video. Keep local preview and inference alive across temporary cloud failures.

Implement identity, per-role backend authorization, camera source safety, desired/actual generations, heartbeats, signed artifact staging, fixed worker supervision, local rollback, stale-generation rejection and bounded offline replay. Implement real campaign tables and controller transitions with 10%,25%,100% cumulative rings, sufficient health evidence, explicit advance and reversible newer-generation rollback. Simulators use the same real APIs, persistent identities and hash/signature/state behavior; only worker observations are simulated and labeled.

Seed 8,000 inventory records across 800 sites separately from 10–50 active simulated agents. Never imply 8,000 physical installations or 8,000 concurrent video pipelines. Keep real versus simulated metrics/releases separated. Never fabricate GPU readings, TensorRT engines, benchmark improvement or hardware verification. Where no compatible NVIDIA host exists, deliver working CPU behavior and record the GPU path as BLOCKED/UNVERIFIED. Hardware-dependent code existing does not mean hardware verified.

Implement actual MLflow/DVC lineage, ONNX evaluation, release signing and promotion gates. Drift requests human review; retraining completion never directly changes production desired state. Build real Prometheus/Grafana integration, JSON logs and bounded OTel console spans. Missing telemetry is unknown and must not pass a canary. Add P1 only after coherent P0 and without disguising missing P0.

## Verification and self-correction

Install dependencies, start frontend/backend/PostgreSQL/MLflow/CPU edge/simulator/Prometheus/Grafana, and exercise all critical journeys possible in the environment. Record actual commands, versions, errors and outcomes. Find broken imports, dead buttons, mismatched fields/routes/enums, unsafe auth, missing states, fake dashboard values, hidden external dependencies and root causes; fix them and rerun the affected runtime behavior.

Do not introduce conventional unit/integration/E2E/frontend testing frameworks or coverage infrastructure. Use runtime journey verification, meaningful computer-vision AI evaluations, load/failure experiments and security/config audits. Run PyTorch/ONNX eval on the documented split. Run TensorRT FP16/INT8 benchmarks only on actual compatible hardware; report missing hardware as blocked. All numeric targets are gates to measure, not values to copy into reports. Do not pass a real release on failed model quality; preserve simulation-only release restrictions.

Audit forbidden actions with every role, device identity scope, enrollment replay, artifact tampering/path traversal, secret redaction and revocation. Restart controller and agent at critical update points, interrupt WAN, inspect durable replay/deduplication, stale generations and offline rollback completion. Verify failure matrix and acceptance traceability without claiming static inspection proves execution.

## Deliverable and release gate

Create the `visionops/` structure in document 19. Include START_HERE.md, README.md, complete .env.example, locks, migrations, startup scripts for Linux and Windows where practical, safe fixtures/licenses, exact configuration and docs/specification/ copy. Required reports:

- docs/IMPLEMENTATION_REPORT.md: implemented, architecture, AI, security, dependencies and known missing/blocked work.
- docs/VERIFICATION_REPORT.md: environment, actual startup/journey/failure evidence and clean-install results.
- docs/AI_EVALS.md: real datasets, model/event metrics, hardware and raw evidence references.
- docs/REQUIREMENTS_TRACEABILITY.md: R-ID → implementation → AC-ID → evidence/status.
- docs/HARDWARE_COMPATIBILITY.md: exact tuple and actual hardware evidence or explicit blocked status.
- docs/ASSUMPTIONS_AND_DECISIONS.md: material implementation decisions/deviations.

Use statuses honestly: VERIFIED; IMPLEMENTED — NOT RUNTIME VERIFIED; BLOCKED BY EXTERNAL SERVICE/ENVIRONMENT; NOT IMPLEMENTED. AI-EVALUATED and HARDWARE VERIFIED require actual evidence. No fake success buttons, static numbers presented as live, placeholder core functions or unreported P0 gaps.

Reproduce extract→configure→install→start→demo from a fresh release directory as far as environment permits. START_HERE.md must state exact prerequisites, commands, environment setup, model/media acquisition, database initialization, bootstrap users, service URLs, CPU/GPU profile selection, troubleshooting and known limits. No reliance on undeclared globals, caches, absolute development paths or files outside the documented release dependencies.

Audit release contents before ZIP: no real credentials, signing private keys, .env, node_modules, virtualenvs, caches, logs, raw sensitive footage or unlicensed assets. Inspect every archive path, required file and clean-install instruction; package a single `visionops/` root. If a P0 issue cannot be completed due to a genuine external constraint, disclose it clearly rather than presenting a complete release. Deliver the downloadable ZIP with concise implementation/runtime/AI/hardware status and instruction to begin with START_HERE.md.

Continue autonomously until the authorized work is complete or a genuine external blocker prevents progress. Correct root causes, not superficial symptoms. The desired result is a coherent product whose actual runtime and limitations can be demonstrated honestly.
