# 15 — Security specification

## Threat model and boundary

Protect camera credentials, user/device identities, artifact integrity, deployment authority and potentially sensitive people imagery. Threats include stolen bearer credentials, replayed events, cross-device writes, tampered downloads, unsafe runtime bundles, SSRF through RTSP locators and an unauthorized release action. Single-organization P0 has four roles, not enterprise multi-tenancy.

## Authentication and role matrix

Session cookies: HttpOnly, SameSite=Lax, Secure under TLS, 8-hour absolute expiry and server-side revocation. Store random session token hash, not raw token. Passwords Argon2id; bootstrap users through local privileged setup CLI, never public registration or fixed production demo passwords. Rate-limit login to 5 attempts/minute per account+IP; return generic errors. Local loopback HTTP exception is explicit and never a public deployment default.

| Operation | MLOps | CV engineer | Fleet operator | Safety viewer |
| --- | --- | --- | --- | --- |
| Read fleet/events/model/deployment/metrics | Yes | Yes | Yes | Yes |
| Acknowledge safety events | Yes | No | Yes | Yes |
| Create sites/cameras, configure sources | Yes | No | Yes | No |
| Enroll/rotate/revoke devices | Yes | No | Yes | No |
| Dataset/training/artifact/evaluation metadata | Yes | Yes | No | No |
| Propose draft release/default config | Yes | Yes | No | No |
| Upload runtime / approve release / campaign action | Yes | No | No | No |
| Review hard examples and request retraining | Yes | Yes | No | No |
| Local simulation scenario controls | Yes | No | Yes | No |

Backend dependencies enforce every row. Disabled users/sessions fail immediately. CSRF token and Origin check for state-changing cookie requests; same-origin frontend uses relative API. Strict CORS allowlist only if separate development origin is enabled; never wildcard with credentials. No client-controlled role/capability field overrides stored authorization.

## Device identity and TLS

Enrollment binds one device to one site and immutable mode. Token: ≥256 random bits, hashed with separate pepper, one-use, 10-minute expiry. Exchanged bearer credential also ≥256 bits, stored hashed centrally and mode-0600 file edge-local. Device APIs derive ownership from token; compare path IDs, camera assignment and historical release assignment. No device can approve releases or claim real mode by changing payload. Revocation prevents new central requests; an offline edge may continue its last authorized inference because immediate remote revocation is impossible without connectivity. Document this limitation.

P0 uses server-authenticated HTTPS plus per-device bearer credentials. mTLS is P1: private CA, per-device certificate subject binding, short-lived certificate issuance/rotation and revocation policy. Do not call bearer-over-TLS mTLS. Validate CA and hostnames; no global insecure TLS flag in deployment profile.

## Artifact trust and execution

SHA-256 catches corruption, but only a trusted signature establishes publisher authorization. Use Ed25519 signature over exact canonical manifest UTF-8 bytes returned by draft creation. Canonicalization: sorted object keys, compact separators, UTF-8, no NaN/Infinity, no floats in manifest; use integer sizes/version metadata and string hashes. Manifest includes release_id, schema_version, hardware_profile, evidence_mode, model/runtime/config hashes and IDs, compatibility, evaluation_report_id and fixed entrypoint. API and agent independently verify with pinned public key. Private key only in offline release CLI environment; never agent/backend/UI/repository/ZIP. Key rotation with overlapping trust is P1; emergency local re-provisioning is documented P0 recovery.

Approve only ready artifacts with matching hash and passed report/profile. Real devices reject simulated releases. No unsigned artifact fallback. Downloads authorized for device desired/current/rollback references, checked on each request. Reject wrong architecture, TensorRT/runtime tuple, input shape or preprocessing/class map before loading. Do not deserialize untrusted PyTorch pickle in serving backend; P0 serving artifacts are validated ONNX or TensorRT, model training tools operate on trusted controlled inputs.

Runtime archive extraction forbids absolute paths, traversal, symlinks, hardlinks, device files, setuid bits, oversized expanded content (>4 GiB) and unexpected entrypoints. Extract unprivileged into staging; no shell interpolation of manifest/URL values. Fixed worker arguments, read-only model mount, separate writable state volume, no Docker socket or privileged container.

## Camera and evidence security

RTSP URI accepts rtsp/rtsps only, no embedded user/password. Credentials are an edge-local mapping by credential_ref. Allow camera private subnets/hosts explicitly; deny metadata/link-local/loopback/internal control-plane targets unless individually approved in local fixture profile. Resolve and validate all addresses, redirects and reconnections to resist DNS rebinding; central API never fetches arbitrary user-supplied RTSP URLs. File sources are relative to EDGE_MEDIA_ROOT; resolve canonical path and reject escape/symlink traversal.

Snapshots accept JPEG only, decode and re-encode, cap 512 KiB and 1920×1080, strip EXIF; randomized internal storage keys, authenticated reads and Content-Disposition/nosniff. Limit upload time/body size; no raw filename paths. Optional short clips are P1. Retention follows domain model. Exclude credentials and raw imagery from logs, traces, metrics labels and Git. Privacy-oriented crop/blur is P1; limited evidence access and retention are P0.

## Audit and operational controls

In the same database transaction as mutation, append actor, action, target, before/after state, reason and request_id. Deployment history additionally preserves generation and health gate evidence. Audit log application role cannot update/delete rows; privileged retention job may delete expired partitions/rows and logs the operation separately. This is append-only at application level, not tamper-proof against DB administrators.

Secret values originate from environment/files, with complete .env.example placeholders. Separate app, MLflow and backup DB users. Bind DB/MLflow/Prometheus/Grafana privately; protect externally accessible Grafana independently. Same-origin TLS proxy is the only public entrypoint. Add request limits (user 120/minute, device 120/minute, configurable measured limits), sane upload/time limits and Retry-After. Protect backups and periodically exercise restore on disposable data.

## Required runtime security evidence

Exercise all four roles on allowed/denied API operations; replay enrollment; attempt a cross-device heartbeat/event; corrupt model bytes/signature; submit unsafe runtime path; revoke credential; request expired evidence; inspect ZIP for secrets. Record actual outcomes without adding conventional test frameworks. Any bypass prevents release until fixed or explicitly listed as an unresolved P0 defect.

## Derived configuration and local tooling

Signed release config pins inference/rule defaults. Per-device camera overrides are authorized by TLS/device identity plus desired config hash; agent verifies that every non-camera setting still equals signed defaults. The central control plane is trusted to assign camera sources, not to inject arbitrary runtime code or alter signed preprocessing through an override. Private signing key never moves to backend to make source edits convenient.

Local operator simulator CLI uses an authenticated O session (`GET /auth/me`) before changing simulator-owned scenario files and emits an AuditEvent through the existing application service boundary of the local tool. No public arbitrary fault endpoint is exposed. Host administrators inherently can edit local simulation files; RBAC claims apply to application operations, not to defending against root on its own host. Setup-generated private CA/certificates can provide TLS on isolated Compose networking; HTTP exception is only browser/host loopback, not an excuse to disable validation on a public or remote edge path.
