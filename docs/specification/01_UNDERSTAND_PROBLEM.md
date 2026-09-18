# 01 — Understand the problem

## The one-minute story

Industrial VisionOps detects people and visible helmet violations beside the camera, records safety events, and shows operators which edge devices are running which approved release. It demonstrates a canary that fails, pauses, and rolls back without manually SSHing into devices. Real video and a small active simulated fleet sit alongside 8,000 explicitly synthetic inventory records.

## First principles and business problem

A detector converts pixels into uncertain observations. A safety rule turns repeated observations into a reviewable event. An operational system keeps that pipeline running, controls what software runs, and exposes failures. Industrial sites otherwise face unreviewable video volumes, alert fatigue, inconsistent model versions, and devices that disappear from central visibility.

| User | Work to support | Value |
| --- | --- | --- |
| Safety / Operations Viewer | Inspect event evidence and acknowledge review | Focus attention on potentially unsafe situations |
| Fleet / Platform Operator | Onboard cameras/devices, inspect freshness and failures | Reduce diagnosis and recovery effort |
| CV / ML Engineer | Curate data, train, evaluate, inspect lineage | Improve evidence-backed detection quality |
| MLOps Engineer | Approve releases, canary, pause, roll back | Limit the impact of a faulty release |

These benefits are intended outcomes, not measured business results. This is an advisory portfolio system, not a certified safety interlock or a substitute for workplace procedures.

## Engineering problem

Separate camera ingestion and inference from unreliable WAN access. Bound queues so old video cannot consume all resources. Keep local inference independent of the control plane. Reconcile monotonic desired generations against reported actual state. Make updates recoverable across process crashes, partial downloads, lost acknowledgements, and device reconnection. Link dataset → training → evaluation → immutable artifacts → deployments → observed events.

## Why MLOps

Correct predictions on one dataset do not establish runtime capacity, stability on new cameras, or safe fleet deployment. Data, model, runtime, hardware compatibility, evaluation, and rollback must be managed together. The model is one replaceable component of camera → inference → deployment → monitoring → recovery.

## Scope and non-goals

Primary behavior: person + explicit no-helmet evidence + tracking + temporal safety event. Track IDs persist within a stream session only. P0 supports one real sample-video stream, optional 1–4 streams as capacity allows, 10 active simulated agents by default (configurable to 50), and 8,000 inventory records. Device count and site count are separate; seed 800 sites with 10 devices each.

No face recognition, employee identification, automated disciplinary action, LLM/RAG agents, continuous central video archive, cross-camera identity, arbitrary remote shell, OS/firmware OTA, Kubernetes, Kafka, or claims of 8,000 physical deployments. Vest, forklift, zones, INT8, mTLS, and central tracing storage are P1.

Read [03_PRD.md](03_PRD.md) for scope and [ASSUMPTIONS_AND_DECISIONS.md](ASSUMPTIONS_AND_DECISIONS.md) for binding choices. All files specify future implementation; none claim software has already been built.
