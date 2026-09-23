# VisionOps Mermaid Architecture Diagrams

Presentation-ready diagrams derived from
[VISIONOPS_SIMPLE_ARCHITECTURE.md](VISIONOPS_SIMPLE_ARCHITECTURE.md). Every diagram renders
independently in GitHub / Markdown. Nothing here is invented; components, flows and claims come
from that document.

## 1. Full System Architecture

The whole product in one picture: an operator console and control plane on the left, an
outbound-only edge device on the right, and the event path from camera to UI.

```mermaid
flowchart LR
    user["MLOps Engineer"]

    subgraph cplane["CONTROL PLANE"]
        fe["React Frontend"]
        be["FastAPI Backend"]
        pg["PostgreSQL"]
        ctl["Controller"]
        des["Desired State"]
        ml["MLflow"]
        prom["Prometheus"]
        graf["Grafana"]
    end

    subgraph eplane["EDGE PLANE"]
        cam["Camera / Video Source"]
        dec["Decoder"]
        onnx["ONNX Runtime PPE Detector"]
        bt["ByteTrack"]
        rules["Temporal Safety Rules"]
        ev["Safety Event"]
        outbox["SQLite Durable Outbox"]
        agent["Edge Agent"]
    end

    %% CONTROL FLOW (dotted) - the control plane tells the edge WHAT SHOULD RUN
    user -.-> fe
    fe -.-> be
    be -.-> pg
    ctl -.-> pg
    pg -.-> ctl
    be -.->|"control / desired state"| des
    des -.-> agent
    be -.-> ml
    be -.-> prom
    prom -.-> graf

    %% DATA FLOW (solid) - the edge reports WHAT IS ACTUALLY RUNNING and WHAT HAPPENED
    cam --> dec
    dec --> onnx
    onnx --> bt
    bt --> rules
    rules --> ev
    ev --> outbox
    outbox --> agent
    agent -->|"events / heartbeat"| be
    be --> pg
    pg --> fe
```

The edge has no inbound port: the agent connects out to FastAPI for desired state, artifact
download, heartbeats and safety events. Event visibility in the UI travels back through the
FastAPI query API.

## 2. Video Inference Pipeline

One frame from camera to database, with the question each stage answers.

```mermaid
flowchart TD
    cam["Camera / Video"] --> dec["Frame Decode"]
    dec --> q["Latest Frame Queue"]
    q --> drop["Drop Stale Frames"]
    drop --> inf["ONNX Inference"]
    inf -->|"WHAT / WHERE"| dd["Detection Decode"]
    dd --> bt["ByteTrack"]
    bt -->|"WHO over time"| rules["Temporal Safety Rules"]
    rules -->|"WHEN sustained"| ev["Safety Event"]
    ev --> ob["SQLite Outbox"]
    ob --> api["Backend API"]
    api --> pg["PostgreSQL"]
    pg --> ui["Operator UI"]
```

- **WHAT / WHERE** — object detection: what is in the frame and where it is.
- **WHO over time** — tracking: the same person across frames, not a new person per frame.
- **WHEN sustained** — temporal rules: a condition that lasts long enough to be a real event.

Stale frames are dropped so processing stays near real time, and the event is written to the
local outbox before it is sent.

## 3. Model Lifecycle

From training to a health-gated rollout, including the point where the campaign branches.

```mermaid
flowchart TD
    tr["Model / Training"] --> ex["ONNX Export"]
    ex --> cv["Contract Verification"]
    cv --> ml["MLflow"]
    ml --> rel["Release Creation"]
    rel --> sha["SHA-256"]
    sha --> sig["Ed25519 Signature"]
    sig --> camp["Deployment Campaign"]
    camp --> ds["Desired State"]
    ds --> dl["Edge Download"]
    dl --> ver["Verify Hash + Signature"]
    ver --> act["Activate"]
    act --> hb["Heartbeat"]
    hb --> gate{"Health Gate"}
    gate -->|"Healthy"| cont["Continue Rollout"]
    gate -->|"Unhealthy"| pause["Pause"]
    pause --> rb["Rollback"]
```

A release pins the model, runtime and configuration by hash and is signed with a private key
the edge never holds. Devices reject anything whose bytes or signature do not match, so a model
cannot be activated unverified.

## 4. Canary + Rollback

The reliability story: a bad release is contained to one device and then withdrawn.

```mermaid
flowchart TD
    v1["Release v1 (fleet healthy)"] --> v2["Release v2"]
    v2 --> can["Canary Device<br/>(SIMULATED DEVICES)"]
    can --> gate{"Health Gate"}
    gate -->|"Healthy"| ring2["Ring 2"]
    ring2 --> ring3["Ring 3"]
    ring3 --> full["Full Rollout"]
    gate -->|"Unhealthy"| pause["Pause Campaign"]
    pause --> restore["Restore Previous Release (v1)"]
    restore --> rec["Devices Reconcile"]
    rec --> ok["Fleet Healthy Again"]
```

The canary is exactly one device, and expansion is gated on machine-readable health reason
codes rather than human judgement. The verified demo fleet is **simulated** — real agent
processes, real SQLite files, real HTTP and real application logic, but not physical hardware.

## 5. Observability

How health travels from the running system to a dashboard.

```mermaid
flowchart LR
    edge["Edge Runtime"] --> metrics["Metrics"]
    backend["Backend"] --> metrics
    controller["Controller"] --> metrics
    metrics --> prom["Prometheus"]
    prom --> graf["Grafana"]
```

Metric examples (not an exhaustive list):

- device health and heartbeat age
- inference FPS and inference latency, dropped frames
- campaign state
- API latency and database availability

## 6. Storage Architecture

Each store exists for a different failure mode, not by accident.

```mermaid
flowchart TD
    sys["VisionOps"]
    sys --> pg["PostgreSQL<br/>users, devices, models,<br/>releases, campaigns,<br/>events, heartbeats"]
    sys --> sq["SQLite on Edge<br/>event outbox, device identity,<br/>activation state"]
    sys --> ml["MLflow<br/>runs, artifacts,<br/>model lineage"]
    sys --> pr["Prometheus<br/>time-series metrics"]
    sys --> fn["Filesystem<br/>model artifacts,<br/>evidence files"]
```

PostgreSQL is the single source of central truth; SQLite keeps the edge durable when it is
offline with no central database at all; MLflow owns lineage; Prometheus owns time series; the
filesystem holds the bytes.

## 7. Security / Trust Boundaries

Three boundaries: a human, a device, and a signed artifact.

```mermaid
flowchart LR
    subgraph human["Human Trust Boundary"]
        op["Operator / MLOps Engineer"] --> session["Session + RBAC + CSRF"]
    end
    subgraph devb["Device Trust Boundary"]
        dev["Edge Device"] --> enrol["Enrollment Token"]
        enrol --> cred["Device Credential"]
    end
    subgraph relb["Release Trust Boundary"]
        art["Release Artifact"] --> sha["SHA-256"]
        sha --> sig["Ed25519 Signature"]
        sig --> ver["Edge Verification"]
        ver --> act["Activation"]
    end
    session --> api["FastAPI"]
    cred --> api
```

Credential types never mix: a browser session cookie cannot post heartbeats, and a device token
cannot call an operator endpoint.  A device only activates a release it has verified.

> [!CAUTION]
> **CURRENT DEVELOPMENT DEFECT:** Current locally-built Docker images must not be distributed
> until the `.dockerignore` / secret-baking issue documented in
> [VISIONOPS_ARCHITECTURE_AUDIT.md](VISIONOPS_ARCHITECTURE_AUDIT.md) is fixed and secrets are
> rotated. Do not push or distribute current images.

## 8. Deployment State Model

Desired state versus actual state, and how generations keep ordering safe.

```mermaid
flowchart TD
    ds["Desired State<br/>generation 4 = candidate"] --> rc["Edge Reconcile"]
    rc --> dl["Download"]
    dl --> ver["Verify"]
    ver --> act["Activate"]
    act --> as["Actual State"]
    as --> hb["Heartbeat"]
    hb --> gate{"Health Gate"}
    gate -->|"Healthy"| conv["Converged"]
    gate -->|"Unhealthy"| rb["Rollback<br/>generation 5 = rollback assignment"]
    as -.->|"Desired != Actual"| rc
```

While desired state and actual state differ, the device keeps reconciling. A rollback is simply
a *newer* assignment pointing at the *previous* release. Generation numbers are monotonic, so a
repeated or out-of-order message cannot move a device backwards. The numbers shown here are
illustrative examples, not runtime claims.

## 9. Real vs Simulated vs Blocked

What is actually verified on this machine, what is simulated, and what still needs hardware.

```mermaid
flowchart LR
    subgraph real["VERIFIED LOCALLY"]
        r1["FastAPI"]
        r2["PostgreSQL"]
        r3["ONNX CPU inference"]
        r4["Video decode"]
        r5["ByteTrack"]
        r6["Temporal rules"]
        r7["SQLite outbox"]
        r8["MLflow"]
        r9["Prometheus"]
        r10["Grafana"]
        r11["Release lifecycle"]
        r12["Rollback"]
    end
    subgraph sim["SIMULATED"]
        s1["Fleet devices"]
        s2["Canary fleet"]
        s3["Failure injection"]
        s4["Engineering fixtures"]
    end
    subgraph ext["EXTERNALLY VERIFIED"]
        e1["TensorRT on Tesla T4"]
    end
    subgraph blocked["BLOCKED (no hardware)"]
        b1["Physical Jetson"]
        b2["JetPack"]
        b3["Jetson TensorRT"]
        b4["Jetson DeepStream"]
        b5["NVDEC on Jetson"]
        b6["ARM64 execution"]
    end
```

TensorRT on a Tesla T4 was verified on an external host and is **not** a Jetson result. Simulated
fleet devices are never physical edge devices. Model accuracy metrics remain unavailable until a
labeled evaluation dataset exists.

## 10. 60-Second Interview Diagram

The single picture to show when time is short.

```mermaid
flowchart LR
    subgraph build["BUILD"]
        m["MODEL"] --> r["RELEASE"]
    end
    subgraph operate["CONTROL PLANE"]
        cp["DESIRED STATE + HEALTH"]
    end
    subgraph runtime["EDGE RUNTIME"]
        e["EDGE"] --> v["VIDEO"]
        v --> i["INFERENCE"]
        i --> t["TRACKING"]
        t --> s["SAFETY EVENT"]
    end
    subgraph ops["OPERATE"]
        mon["MONITOR"] --> can["CANARY"]
        can --> roll["ROLLBACK"]
    end
    r --> cp
    cp --> e
    s --> mon
    roll --> cp
```

Model becomes a signed release, the control plane decides where it runs, the edge turns video
into safety events, and monitoring drives canary rollout or rollback.

## Which Diagram Should I Use?

| Use Case | Diagram |
| --- | --- |
| Interview overview | 60-Second Interview Diagram |
| Technical architecture | Full System Architecture |
| Explain inference | Video Inference Pipeline |
| Explain MLOps | Model Lifecycle |
| Explain reliability | Canary + Rollback |
| Explain deployment control | Deployment State Model |
| Explain monitoring | Observability |
| Explain data ownership | Storage Architecture |
| Explain security | Security / Trust Boundaries |
| Explain honest scope | Real vs Simulated vs Blocked |
