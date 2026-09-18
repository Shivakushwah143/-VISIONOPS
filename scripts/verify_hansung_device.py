"""Drive the released Hansung artifact onto a device and prove reconciliation.

Exercises the existing APIs and the real edge agent:
  site -> device (simulated) -> camera -> video source -> enrollment token
  -> agent enrolls, fetches the signed manifest, downloads and hash-verifies the
  model artifact, commits actual state -> heartbeat reports the applied version.

Then it proves the agent's downloaded artifact IS the qualified Hansung artifact
by comparing SHA-256 of the slot file against the release manifest.

The device must be mode='simulated' because the release carries an explicitly
simulation-labelled evaluation record (see scripts/release_hansung.py).
"""
import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEVICE_NAME = "hansung-qualification-device"
SITE_NAME = "Hansung qualification site"
CAMERA_NAME = "hansung qualification camera"
AGENT_STATE = ROOT / "var/simulation/hansung-agent"


def credentials():
    path = ROOT / "var/hansung-sprint.env"
    env = dict(line.strip().split("=", 1) for line in path.read_text().splitlines()
               if "=" in line)
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


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://localhost:8080")
    p.add_argument("--release-file", default="var/hansung-release.json")
    p.add_argument("--output", default="var/hansung-device.json")
    p.add_argument("--agent-timeout", type=int, default=150)
    a = p.parse_args()

    import getpass
    email, password = credentials()
    getpass.getpass = lambda prompt="": password
    from scripts.client import Client

    release = json.loads((ROOT / a.release_file).read_text())
    c = Client(a.url, email)

    sites = {s["name"]: s for s in rows(c, "/sites")}
    site = sites.get(SITE_NAME) or c.post("/sites", {"name": SITE_NAME, "timezone": "UTC"})
    devices = [d for d in rows(c, "/devices?mode=simulated") if d["name"] == DEVICE_NAME]
    if devices:
        device = devices[0]
        print("reusing device", device["device_id"])
    else:
        device = c.post("/devices", {
            "site_id": site["site_id"], "name": DEVICE_NAME, "mode": "simulated",
            "hardware_profile": release["hardware_profile"],
            "release_id": release["release_id"]})
        print("created device", device["device_id"])

    cameras = [camera for camera in rows(c, "/cameras") if camera["name"] == CAMERA_NAME]
    if cameras:
        camera = cameras[0]
    else:
        camera = c.post("/cameras", {"site_id": site["site_id"],
                                     "device_id": device["device_id"], "name": CAMERA_NAME})
    sources = rows(c, "/video-sources")
    source = next((s for s in sources if s["camera_id"] == camera["camera_id"]), None)
    if not source:
        source = c.post("/video-sources", {"camera_id": camera["camera_id"], "kind": "file",
                                           "locator": "/media/ppe-2.mp4", "enabled": True,
                                           "loop": False})["video_source"]

    token = c.post("/devices/%s/enrollment-tokens" % device["device_id"],
                   {"reason": "hansung qualification sprint"})["token"]

    AGENT_STATE.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "edge.agent", "--state", str(AGENT_STATE),
           "--url", a.url, "--trust", str(ROOT / "var/trusted_keys"),
           "--mode", "simulated", "--enrollment-token", token]
    print("starting agent:", " ".join(cmd[1:]))
    log = (ROOT / "var/hansung-agent.log").open("w")
    agent = subprocess.Popen(cmd, cwd=str(ROOT), stdout=log, stderr=subprocess.STDOUT)

    deadline = time.monotonic() + a.agent_timeout
    detail, committed = None, False
    try:
        while time.monotonic() < deadline:
            detail = c.get("/devices/" + device["device_id"])
            dev = detail["device"]
            if dev["actual_release_id"] and dev["applied_generation"] > 0:
                committed = True
                break
            time.sleep(5)
    finally:
        agent.terminate()
        try:
            agent.wait(timeout=15)
        except subprocess.TimeoutExpired:
            agent.kill()
        log.close()

    detail = c.get("/devices/" + device["device_id"])
    dev = detail["device"]
    hb = detail["last_heartbeat"] or {}
    desired = detail["desired_state"]
    print("desired generation=%s release=%s config=%s" % (
        desired["generation"], desired["release_id"], desired["config_version_id"]))
    print("actual  generation=%s release=%s config=%s state=%s health=%s" % (
        dev["applied_generation"], dev["actual_release_id"],
        dev["actual_config_version_id"], dev["agent_state"], dev["health_status"]))

    # Prove the bytes the agent staged are the qualified artifact.
    slots = sorted((AGENT_STATE / "slots").glob("*/model"))
    staged = None
    if slots:
        staged = slots[-1]
    staged_sha = hashlib.sha256(staged.read_bytes()).hexdigest() if staged else None
    print("agent staged %s sha256=%s" % (staged, staged_sha))

    result = {
        "device_id": device["device_id"],
        "site_id": site["site_id"],
        "camera_id": camera["camera_id"],
        "video_source_id": source["video_source_id"],
        "video_source_locator": source["locator"],
        "desired_generation": desired["generation"],
        "desired_release_id": desired["release_id"],
        "actual_generation": dev["applied_generation"],
        "actual_release_id": dev["actual_release_id"],
        "actual_config_version_id": dev["actual_config_version_id"],
        "agent_state": dev["agent_state"],
        "health_status": dev["health_status"],
        "heartbeat_capabilities": hb.get("capabilities"),
        "committed": committed,
        "agent_slot_model_path": str(staged) if staged else None,
        "agent_slot_model_sha256": staged_sha,
        "released_model_sha256": release["model_sha256"],
        "artifact_provenance_verified": staged_sha == release["model_sha256"],
    }
    Path(a.output).write_text(json.dumps(result, indent=2))
    print("artifact_provenance_verified:", result["artifact_provenance_verified"])
    print("output:", a.output)


if __name__ == "__main__":
    main()
