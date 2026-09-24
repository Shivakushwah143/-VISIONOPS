"""Register a release carrying the REAL qualified Hansung PPE ONNX artifact.

This drives the existing release/model/versioning APIs. It does not add a second
release architecture. Two things are deliberately kept apart:

  * the MODEL ARTIFACT is the real qualified artifact
    var/model/hansung-p3.onnx (sha256 pinned, ONNX-checked, PT/ONNX parity OK);
  * the EVALUATION RECORD is explicitly simulation-labelled, because no labeled
    temporal PPE benchmark exists in this environment, so the server-owned real
    promotion gate (training/quality.gate + full benchmark protocol) cannot be
    satisfied honestly. The evaluation therefore reports
    evidence_mode='simulated' and result='passed' under policy 'simulation-only'.

Consequence: the release manifest carries evidence_mode='simulated', so only a
device with mode='simulated' may reconcile to it. That is intentional and is
reported as the exact blocker for a real-quality promotion.

Usage:
  python -m scripts.release_hansung --url http://localhost:8080 --tracking-uri http://localhost:5000
"""
import argparse
import base64
import hashlib
import json
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from shutil import copyfile

ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "var/model/hansung-p3.onnx"
CONTRACT = ROOT / "var/model/hansung-p3.json"
QUALIFICATION = ROOT / "docs/evidence/hansung-onnx-qualification.json"
PRIVATE_KEY = ROOT / "var/keys/qual.key"
VERSION_LABEL = "ppe-hansung-v1"
REGISTERED_NAME = "ppe-hansung"
PROFILE = "cpu_onnx_x86_64"
CLASS_MAP = {"0": "person", "1": "helmet", "2": "no_helmet"}


def credentials():
    path = ROOT / "var/hansung-sprint.env"
    env = dict(line.strip().split("=", 1) for line in path.read_text().splitlines()
               if "=" in line)
    return env["HANSUNG_SPRINT_EMAIL"], env["HANSUNG_SPRINT_PASSWORD"]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://localhost:8080")
    p.add_argument("--tracking-uri", default="http://localhost:5000")
    p.add_argument("--output", default="var/hansung-release.json")
    p.add_argument("--version-label", default=VERSION_LABEL)
    p.add_argument("--purpose", default="qualified_model_registration",
                   help="recorded in artifact/evaluation metadata; use rollout_verification "
                        "when the same qualified bytes are reused to exercise rollout mechanics")
    a = p.parse_args()
    version_label = a.version_label
    rollout_only = a.purpose == "rollout_verification"
    if rollout_only:
        print("purpose=rollout_verification: reusing the qualified bytes for %s "
              "(control-plane verification, not a new model)" % version_label)

    import getpass
    email, password = credentials()
    # Client reads the password interactively; supply it for unattended runs.
    getpass.getpass = lambda prompt="": password
    from scripts.client import Client
    from scripts.engineering_fixture import create

    model_bytes = MODEL.read_bytes()
    digest = hashlib.sha256(model_bytes).hexdigest()
    contract = json.loads(CONTRACT.read_text())
    if contract["artifact"]["sha256"] != digest:
        raise SystemExit("contract/artifact hash mismatch; refusing to register")
    # Resolved from the artifact's own declared class labels, never hardcoded, so the
    # release manifest pins the mapping version both runtimes must agree on.
    from shared import model_contract
    contract_profile = model_contract.ModelContract.detect(contract["source_class_map"]).profile
    print("model: %s (%d bytes, sha256 %s, contract %s)"
          % (MODEL, len(model_bytes), digest, contract_profile))

    from scripts.mlflow_rest import MlflowRest
    c = Client(a.url, email)
    tracking = MlflowRest(a.tracking_uri)
    experiment = tracking.experiment_id("visionops-hansung")

    with tempfile.TemporaryDirectory(prefix="visionops-hansung-release-") as temp:
        folder = Path(temp)
        fixture = create(folder / "runtime")
        provenance = {"run_kind": "hansung_qualified_model_registration",
                      "quality_evaluated": False,
                      "artifact_sha256": digest,
                      "taxonomy": CLASS_MAP,
                      "note": "Artifact qualification only; no labeled PPE benchmark."}
        run_id = tracking.start_run(experiment, {
            "run_kind": "hansung_qualified_model_registration",
            "quality_evaluated": "false"})
        # Real model + its contract and qualification evidence travel with the run.
        logged = {
            "model/hansung-p3.onnx": tracking.log_artifact(
                experiment, run_id, "model", MODEL.name, model_bytes),
            "contract/hansung-p3.json": tracking.log_artifact(
                experiment, run_id, "contract", CONTRACT.name, CONTRACT.read_bytes()),
            "evidence/hansung-onnx-qualification.json": tracking.log_artifact(
                experiment, run_id, "evidence", "hansung-onnx-qualification.json",
                QUALIFICATION.read_bytes()),
            "provenance.json": tracking.log_artifact(
                experiment, run_id, "", "provenance.json",
                json.dumps(provenance, indent=2).encode()),
        }
        tracking.finish_run(run_id)
        print("mlflow run %s artifacts logged: %s" % (run_id, logged))
        artifact_uri = tracking.run(run_id)["info"]["artifact_uri"]
        external = tracking.register_model_version(
            REGISTERED_NAME, artifact_uri + "/model/" + MODEL.name, run_id)["model_version"]

        dataset = c.post("/dataset-versions", {
            "name": "Hansung PPE qualification footage (unlabeled) " + version_label,
            "git_commit": "uncommitted-source-release",
            "dvc_hash": digest,
            "dvc_remote_ref": "local:hansung-ppe-qualification",
            "taxonomy": CLASS_MAP,
            "split_manifest_hash": digest,
            "license_ref": "MIT (Hansung-Cho/yolov8-ppe-detection); footage used for qualification only",
            "validation_evidence_ref": "docs/evidence/hansung-onnx-qualification.json",
            "status": "validated",
        })
        stamp = datetime.now(timezone.utc).isoformat()
        training = c.post("/training-runs", {
            "dataset_version_id": dataset["dataset_version_id"],
            "mlflow_run_id": run_id,
            "code_commit": "uncommitted-source-release",
            "parameters": {"run_kind": "external_checkpoint_registration",
                           "pretrained_checkpoint": "Hansung-Cho/yolov8-ppe-detection",
                           "training_performed": False},
            "status": "succeeded",
            "started_at": stamp,
            "finished_at": stamp,
        })
        models = c.get("/models?limit=200")
        model = next((m for m in models if m["name"] == REGISTERED_NAME), None)
        if not model:
            model = c.post("/models", {"name": REGISTERED_NAME, "task": "ppe_detection",
                                       "class_map": CLASS_MAP})
        # (model_id, version_label) is unique server-side, so re-runs reuse the version.
        existing = c.get("/model-versions?model_id=%s&limit=200" % model["model_id"])
        version = next((v for v in existing if v["version_label"] == version_label), None)
        if version:
            print("reusing model version %s (%s)" % (version["model_version_id"], version_label))
        else:
            version = c.post("/model-versions", {
                "model_id": model["model_id"],
                "training_run_id": training["training_run_id"],
                "mlflow_model_name": REGISTERED_NAME,
                "mlflow_model_version": str(external),
                "version_label": version_label,
            })
        with MODEL.open("rb") as f:
            artifact = c.post("/model-artifacts", files={
                "file": (MODEL.name, f, "application/octet-stream")}, data={"metadata": json.dumps({
                    "model_version_id": version["model_version_id"],
                    "format": "onnx",
                    "precision": "fp32",
                    "hardware_profile": PROFILE,
                    "input_shape": [1, 3, 640, 640],
                    "class_map": CLASS_MAP,
                    "model_contract_profile": contract_profile,
                    "compatibility": {
                        "run_kind": "hansung_qualified_model_registration",
                        "purpose": a.purpose,
                        "same_artifact_as": ("ppe-hansung-v1" if rollout_only else None),
                        "output_shape": [1, 14, 8400],
                        "source_class_index_to_canonical_id": contract["source_index_to_canonical_id"],
                        "canonicalization": contract["canonicalization"],
                        "contract": "var/model/hansung-p3.json",
                        "qualification_evidence_ref": "docs/evidence/hansung-onnx-qualification.json",
                    }})})
        if artifact["sha256"] != digest:
            raise SystemExit("uploaded artifact hash differs from qualified artifact")
        copyfile(ROOT / "edge/worker.py", folder / "worker.py")
        (folder / "version.json").write_text(json.dumps({
            "application_version": VERSION_LABEL,
            "model_version_id": version["model_version_id"]}))
        bundle = folder / "runtime.tar"
        with tarfile.open(bundle, "w") as tar:
            for name in ("worker.py", "version.json"):
                tar.add(folder / name, arcname=name)
            tar.add(fixture / "warmup.jpg", arcname="warmup.jpg")
        with bundle.open("rb") as f:
            runtime = c.post("/runtime-artifacts", files={
                "file": ("runtime.tar", f, "application/x-tar")}, data={"metadata": json.dumps({
                "version_label": version_label,
                "hardware_profile": PROFILE,
                "entrypoint": "worker",
                    "compatibility": {"run_kind": "hansung_qualified_model_registration"}})})
        defaults = json.loads((ROOT / "shared/contracts/default-settings.json").read_text())
        config = c.post("/config-versions", {
            "hardware_profile": PROFILE, "schema_version": 1, "settings": defaults})
        report = c.post("/evaluation-reports", {
            "model_version_id": version["model_version_id"],
            "model_artifact_id": artifact["model_artifact_id"],
            "dataset_version_id": dataset["dataset_version_id"],
            "hardware_profile": PROFILE,
            "evidence_mode": "simulated",
            "metrics": {
                "run_kind": "simulation_fixture",
                "quality_evaluated": False,
                "artifact_is_real_qualified_model": True,
                "artifact_sha256": digest,
                "purpose": a.purpose,
                "model_bytes_identical_to_ppe_hansung_v1": rollout_only,
                "note": ("Explicit simulation-only evaluation record used to exercise the "
                         "release transport path. No labeled PPE benchmark, mAP or event "
                         "quality was measured. Not a quality qualification."),
            },
            "gate_policy": {"policy_id": "simulation-only"},
            "result": "passed",
            "evidence_ref": "mlflow:" + run_id + "/provenance.json",
        })
        print("evaluation %s result=%s" % (report["evaluation_report_id"], report["result"]))
        release = c.post("/releases", {
            "model_artifact_id": artifact["model_artifact_id"],
            "runtime_artifact_id": runtime["runtime_artifact_id"],
            "config_version_id": config["config_version_id"],
            "evaluation_report_id": report["evaluation_report_id"],
        })
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        key = Ed25519PrivateKey.from_private_bytes(PRIVATE_KEY.read_bytes())
        manifest = release["manifest"]
        signature = base64.b64encode(key.sign(json.dumps(
            manifest, sort_keys=True, separators=(",", ":")).encode())).decode()
        approved = c.post("/releases/" + release["release_id"] + "/approve", {
            "signature": signature, "key_id": "qual",
            "reason": "Qualified Hansung PPE artifact; simulation-labelled evaluation"})

        out = {
            "release_id": approved["release_id"],
            "version_label": version_label,
            "purpose": a.purpose,
            "status": approved["status"],
            "evidence_mode": approved["evidence_mode"],
            "hardware_profile": approved["hardware_profile"],
            "model_artifact_id": artifact["model_artifact_id"],
            "model_sha256": artifact["sha256"],
            "model_size_bytes": artifact["size_bytes"],
            "model_version_id": version["model_version_id"],
            "config_version_id": config["config_version_id"],
            "evaluation_report_id": report["evaluation_report_id"],
            "manifest_sha256": approved["manifest_sha256"],
            "manifest": manifest,
            "mlflow_run_id": run_id,
        }
        Path(a.output).parent.mkdir(parents=True, exist_ok=True)
        Path(a.output).write_text(json.dumps(out, indent=2))
        print("release %s approved" % approved["release_id"])
        print("model artifact %s sha256 %s" % (artifact["model_artifact_id"], artifact["sha256"]))
        print("evidence_mode=%s output=%s" % (approved["evidence_mode"], a.output))


if __name__ == "__main__":
    main()
