"""Verify SafetyEvent transport independent of model semantics.

The real clip correctly produces zero violations, so it cannot exercise transport.
This script therefore builds ONE deterministic SafetyEvent that satisfies the real
SafetyInput schema and drives it through the REAL path:

  edge State outbox -> real edge agent deliver() -> POST /api/v1/device-events
  -> backend validation -> PostgreSQL -> GET /api/v1/safety-events

It is explicitly labelled as synthetic transport verification, not model output:
  * camera name      -> "integration-verification (synthetic transport check; not model output)"
  * track_id         -> "integration-verification"
  * stream_session_id-> fixed sentinel UUID
  * the event lands with evidence_mode='simulated' because the device is simulated.

No model detection is fabricated and no rule threshold is changed.
"""
import argparse
import json
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from edge.state import State

INTEGRATION_TRACK = "integration-verification"
INTEGRATION_SESSION = uuid.UUID("00000000-0000-4000-8000-0000000ff1ce")
CAMERA_NAME = "integration-verification (synthetic transport check; not model output)"


def credentials():
    env = dict(line.strip().split("=", 1) for line in
               (ROOT / "var/hansung-sprint.env").read_text().splitlines() if "=" in line)
    return env["HANSUNG_SPRINT_EMAIL"], env["HANSUNG_SPRINT_PASSWORD"]


def rows(client, path):
    out, cursor = [], None
    while True:
        params = {"limit": 200}
        if cursor:
            params["cursor"] = cursor
        r = client.http.get(path, params=params)
        r.raise_for_status()
        body = r.json()
        out += body["data"]
        cursor = body["page"]["next_cursor"]
        if not cursor:
            return out


def build_event(camera_id, device, release):
    now = datetime.now(timezone.utc)
    return {
        "kind": "safety",
        "safety_event_id": str(uuid.uuid4()),
        "camera_id": camera_id,
        "stream_session_id": str(INTEGRATION_SESSION),
        "track_id": INTEGRATION_TRACK,
        "event_type": "no_helmet_violation",
        "observed_at": now.isoformat(),
        "window_start": (now - timedelta(seconds=3)).isoformat(),
        "window_end": now.isoformat(),
        "supporting_frames": 5,
        "confidence": 0.5,
        "bbox": [0.40, 0.10, 0.55, 0.30],
        "release_id": device["actual_release_id"],
        "model_version_id": release["model_version_id"],
        "config_version_id": device["actual_config_version_id"],
    }


def run_sender(seconds, url="http://localhost:8080"):
    """Run the real edge agent so its deliver() drains (or cannot drain) the outbox."""
    cmd = [sys.executable, "-m", "edge.agent",
           "--state", str(ROOT / "var/simulation/hansung-agent"),
           "--url", url,
           "--trust", str(ROOT / "var/trusted_keys"), "--mode", "simulated"]
    log = (ROOT / "var/hansung-sender.log").open("a")
    proc = subprocess.Popen(cmd, cwd=str(ROOT), stdout=log, stderr=subprocess.STDOUT)
    deadline = time.monotonic() + seconds
    try:
        while time.monotonic() < deadline:
            time.sleep(1)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://localhost:8080")
    p.add_argument("--release-file", default="var/hansung-release.json")
    p.add_argument("--device-file", default="var/hansung-device.json")
    p.add_argument("--output", default="var/hansung-transport.json")
    p.add_argument("--sender-seconds", type=int, default=14)
    p.add_argument("--retry", action="store_true",
                   help="also prove the outbox retains events while the backend is unreachable")
    p.add_argument("--offline-seconds", type=int, default=8)
    p.add_argument("--unreachable-url", default="http://localhost:9")
    a = p.parse_args()

    import getpass
    email, password = credentials()
    getpass.getpass = lambda prompt="": password
    from scripts.client import Client

    release = json.loads((ROOT / a.release_file).read_text())
    device = json.loads((ROOT / a.device_file).read_text())
    c = Client(a.url, email)

    cameras = [camera for camera in rows(c, "/cameras") if camera["name"] == CAMERA_NAME]
    if cameras:
        camera = cameras[0]
    else:
        camera = c.post("/cameras", {"site_id": device["site_id"],
                                     "device_id": device["device_id"], "name": CAMERA_NAME})
    print("integration camera %s (%s)" % (camera["camera_id"], camera["name"]))

    agent_state = State(ROOT / "var/simulation/hansung-agent/state.sqlite")
    identity = agent_state.get("identity")

    event = build_event(camera["camera_id"], device, release)
    event_id = event["safety_event_id"]
    agent_state.enqueue(event)
    depth_after_enqueue = agent_state.depth()
    print("enqueued %s; outbox depth=%d" % (event_id, depth_after_enqueue))

    run_sender(a.sender_seconds)
    depth_after_send = agent_state.depth()
    deadletter = agent_state.db.execute("SELECT count(*) FROM deadletter").fetchone()[0]
    print("after real agent deliver(): outbox depth=%d deadletter=%d"
          % (depth_after_send, deadletter))

    # API read-back (reads PostgreSQL).
    fetched = c.get("/safety-events/" + event_id)
    listed = [e for e in rows(c, "/safety-events?camera_id=" + camera["camera_id"])
              if e["safety_event_id"] == event_id]
    print("API GET /safety-events/%s -> review_status=%s evidence_mode=%s"
          % (event_id, fetched["review_status"], fetched["evidence_mode"]))

    # Duplicate resend: identical event id must not create a second row.
    import httpx
    http = httpx.Client(base_url=a.url + "/api/v1", timeout=60,
                        headers={"Authorization": "Bearer " + identity["device_credential"],
                                 "Origin": a.url})
    resend = http.post("/device-events", json={"events": [event]})
    resend.raise_for_status()
    resend_result = resend.json()["data"]["results"]
    print("duplicate resend ->", resend_result)

    retry = None
    if a.retry:
        # A second event queued while the backend is genuinely unreachable must
        # survive in the outbox and be delivered once the backend returns.
        queued = build_event(camera["camera_id"], device, release)
        agent_state.enqueue(queued)
        depth_queued = agent_state.depth()
        run_sender(a.offline_seconds, url=a.unreachable_url)
        depth_while_offline = agent_state.depth()
        run_sender(a.sender_seconds)
        depth_after_recovery = agent_state.depth()
        recovered = c.get("/safety-events/" + queued["safety_event_id"])
        retry = {
            "unreachable_url": a.unreachable_url,
            "safety_event_id": queued["safety_event_id"],
            "outbox_depth_after_enqueue": depth_queued,
            "outbox_depth_while_backend_unreachable": depth_while_offline,
            "outbox_retained_event": depth_while_offline == depth_queued,
            "outbox_depth_after_backend_restored": depth_after_recovery,
            "outbox_drained_after_recovery": depth_after_recovery == 0,
            "event_persisted_after_recovery": bool(recovered),
        }
        print("retry: offline depth=%d -> recovered depth=%d"
              % (depth_while_offline, depth_after_recovery))

    agent_state.db.close()
    out = {
        "retry_recovery": retry,
        "labeled_as": "integration_verification",
        "not_model_output": True,
        "camera_id": camera["camera_id"],
        "camera_name": camera["name"],
        "track_id": INTEGRATION_TRACK,
        "stream_session_id": str(INTEGRATION_SESSION),
        "safety_event_id": event_id,
        "device_id": device["device_id"],
        "release_id": device["actual_release_id"],
        "config_version_id": device["actual_config_version_id"],
        "model_version_id": release["model_version_id"],
        "outbox_depth_after_enqueue": depth_after_enqueue,
        "outbox_depth_after_real_sender": depth_after_send,
        "deadletter_count": deadletter,
        "outbox_drained": depth_after_send == 0,
        "api_event_detail_found": bool(fetched),
        "api_listed_found": bool(listed),
        "api_evidence_mode": fetched["evidence_mode"],
        "api_review_status": fetched["review_status"],
        "api_supporting_frames": fetched["supporting_frames"],
        "duplicate_resend_result": resend_result,
        "duplicate_not_reinserted": resend_result == [
            {"event_id": event_id, "status": "duplicate"}],
    }
    Path(a.output).write_text(json.dumps(out, indent=2))
    print("output:", a.output)


if __name__ == "__main__":
    main()
