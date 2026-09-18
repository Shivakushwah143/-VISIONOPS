"""Canary rollout -> failure -> pause -> central rollback verification.

Drives the EXISTING control plane only: no new endpoints, no new tables, no
in-memory stand-ins. Postgres stays the source of truth and `controller.tick()`
issues every download permit, performs the failure pause and finalises rollback.

State machine actually used (from backend/app/campaigns.py):
  draft --start--> running --(advance x2)--> completed
  running|paused --rollback--> rolling_back --tick--> rolled_back (or rollback_incomplete)
  running --3 consecutive failed ticks--> paused

Steps, run as two long phases so the real agent fleet stays alive across them:

  phase-a  provision 10 simulated devices on release ppe-hansung-v1, warm the
           baselines, create the canary campaign for ppe-hansung-v2, start it
           (ring 0 = exactly one canary) and verify the healthy canary path.
  phase-b  inject the existing supported offline fault on the canary, prove the
           controller pauses the rollout, prove the other 9 never moved, restart
           the controller to prove persistence, then exercise central rollback and
           verify 10/10 devices are back on ppe-hansung-v1 and healthy.
"""
import argparse
import json
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from edge.agent import Agent

FLEET_FILE = ROOT / "var/hansung-canary-fleet.json"
STATE_ROOT = ROOT / "var/simulation/canary"
V1_FILE = ROOT / "var/hansung-release.json"
V2_FILE = ROOT / "var/hansung-release-v2.json"
SITE_NAME = "Hansung canary rollout site"
DEVICE_PREFIX = "hansung-canary"
FLEET_SIZE = 10
URL = "http://localhost:8080"


def credentials():
    env = dict(line.strip().split("=", 1) for line in
               (ROOT / "var/hansung-sprint.env").read_text().splitlines() if "=" in line)
    return env["HANSUNG_SPRINT_EMAIL"], env["HANSUNG_SPRINT_PASSWORD"]


def make_client():
    import getpass
    email, password = credentials()
    getpass.getpass = lambda prompt="": password
    from scripts.client import Client
    return Client(URL, email)


def request(c, method, path, body=None, params=None):
    """HTTP with the app's idempotency contract, returning (status, payload)."""
    headers = {"Idempotency-Key": str(uuid.uuid4())}
    r = c.http.request(method, path, json=body, params=params, headers=headers)
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, r.text


def rows(c, path):
    out, cursor = [], None
    while True:
        params = {"limit": 200}
        if cursor:
            params["cursor"] = cursor
        r = c.http.get(path, params=params)
        r.raise_for_status()
        body = r.json()
        out += body["data"]
        cursor = body["page"]["next_cursor"]
        if not cursor:
            return out


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def device_row(c, device_id):
    return c.get("/devices/" + device_id)["device"]


# Asks the RUNNING application's own gate why a device is (in)eligible, so the
# baseline requirement is reported by production code rather than a reimplementation.
ELIGIBILITY_PROBE = '''
import json, os
from backend.app.db import Session, Device, Release
from backend.app.campaigns import eligible
ids = json.loads(os.environ["DEVICES"]); rid = os.environ["V1"]
with Session() as db:
    r = db.get(Release, rid)
    out = {}
    for i in ids:
        d = db.get(Device, i)
        reasons, metrics = eligible(db, d, r)
        out[i] = {"reasons": reasons, "aggregate": metrics,
                  "health": d.health_status, "actual_release_id": d.actual_release_id}
    print("PROBE " + json.dumps(out))
'''


def probe_eligibility(device_ids, release_id):
    cmd = ["docker", "compose", "--env-file", ".env", "-f", "infrastructure/compose.yaml",
           "exec", "-T", "-e", "DEVICES=" + json.dumps(device_ids),
           "-e", "V1=" + release_id, "backend", "python", "-"]
    result = subprocess.run(cmd, cwd=str(ROOT), input=ELIGIBILITY_PROBE,
                            capture_output=True, text=True)
    for line in result.stdout.splitlines():
        if line.startswith("PROBE "):
            return json.loads(line[len("PROBE "):])
    return {"probe_error": (result.stderr or result.stdout)[-400:]}

def eligibility_verdict(probe):
    if "probe_error" in probe:
        return False
    return all(not row["reasons"] for row in probe.values())


def provision(c):
    v1 = json.loads(V1_FILE.read_text())
    sites = {s["name"]: s for s in rows(c, "/sites")}
    site = sites.get(SITE_NAME) or c.post("/sites", {"name": SITE_NAME, "timezone": "UTC"})
    existing = {d["name"]: d for d in rows(c, "/devices?mode=simulated")}
    devices = []
    for i in range(FLEET_SIZE):
        name = "%s-%02d" % (DEVICE_PREFIX, i + 1)
        device = existing.get(name)
        if not device:
            device = c.post("/devices", {
                "site_id": site["site_id"], "name": name, "mode": "simulated",
                "hardware_profile": v1["hardware_profile"],
                "release_id": v1["release_id"]})
            print("created device %s (%s)" % (name, device["device_id"]))
        devices.append(device)
    for device in devices:
        state_dir = STATE_ROOT / device["device_id"]
        agent = Agent(state_dir, URL, ROOT / "var/trusted_keys", "simulated")
        if not agent.state.get("identity"):
            token = c.post("/devices/%s/enrollment-tokens" % device["device_id"],
                           {"reason": "hansung canary rollout verification"})["token"]
            agent.enroll(token)
            print("enrolled %s" % device["device_id"])
        agent.state.db.close()
    fleet = {"site_id": site["site_id"], "baseline_release": v1,
             "devices": [{"device_id": d["device_id"], "name": d["name"],
                          "state_dir": str(STATE_ROOT / d["device_id"])} for d in devices]}
    FLEET_FILE.write_text(json.dumps(fleet, indent=2))
    print("fleet of %d devices provisioned on %s" % (len(devices), v1["version_label"]))
    return fleet


def start_agents(fleet):
    threads = []
    for entry in fleet["devices"]:
        agent = Agent(Path(entry["state_dir"]), URL, ROOT / "var/trusted_keys", "simulated")
        thread = threading.Thread(target=agent.run, daemon=True, name=entry["name"])
        thread.start()
        threads.append((entry, thread))
        print("agent up: %s" % entry["name"])
    return threads


def wait_for(fn, timeout, interval=10, label="condition"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = fn()
        if value:
            return value
        time.sleep(interval)
    print("TIMEOUT waiting for %s" % label)
    return None


def campaign_state(c, campaign_id):
    return c.get("/deployments/" + campaign_id)


def phase_a(a):
    c = make_client()
    v1 = json.loads(V1_FILE.read_text())
    v2 = json.loads(V2_FILE.read_text())
    fleet = (json.loads(FLEET_FILE.read_text())
             if FLEET_FILE.exists() and a.resume else provision(c))
    start_agents(fleet)

    # The gate needs >=300s of contiguous telemetry and >=100 latency samples per
    # device. Poll the application's own gate so refusal is observed, not assumed.
    names = {e["device_id"]: e["name"] for e in fleet["devices"]}
    ids = [e["device_id"] for e in fleet["devices"]]
    probe_history = []
    deadline = time.monotonic() + a.warmup
    ready = False
    while time.monotonic() < deadline:
        time.sleep(30)
        snapshot = probe_eligibility(ids, v1["release_id"])
        verdict = eligibility_verdict(snapshot)
        elapsed = int(time.monotonic() - (deadline - a.warmup))
        probe_history.append({"t": elapsed, "all_eligible": verdict,
                              "reasons": {names.get(k, k): val.get("reasons", val)
                                          for k, val in snapshot.items()} if not verdict else {}})
        for k, val in list(snapshot.items())[:1]:
            print("  t=%3ds sample=%s aggregate=%s" % (elapsed, names.get(k, k), val.get("aggregate")))
        print("  t=%3ds all_eligible=%s" % (elapsed, verdict))
        if verdict:
            ready = True
            break
    baselines = probe_eligibility(ids, v1["release_id"])
    print("baseline readiness:", json.dumps({names.get(k, k): v.get("aggregate")
                                             for k, v in baselines.items()}, indent=2))
    if not ready:
        print("WARNING: fleet never became fully eligible; recording and continuing")

    status, payload = request(c, "POST", "/deployments", {
        "release_id": v2["release_id"],
        "target_device_ids": [e["device_id"] for e in fleet["devices"]],
        "reason": "Hansung canary rollout verification: ppe-hansung-v2 candidate"})
    if status != 201:
        raise SystemExit("campaign create failed: %s %s" % (status, payload))
    campaign = payload["data"]["campaign"]
    cid = campaign["deployment_campaign_id"]
    (ROOT / "var/hansung-canary-campaign.json").write_text(json.dumps(
        {"campaign_id": cid, "created_at": now_iso(),
         "eligibility": payload["data"]["eligibility"]}, indent=2))
    print("campaign %s created (draft); eligibility=%s" % (
        cid, json.dumps(payload["data"]["eligibility"])))

    started = None
    deadline = time.monotonic() + a.start_timeout

    def attempt():
        code, body = request(c, "POST", "/deployments/%s/start" % cid,
                             {"reason": "begin canary ring 0", "expected_status": "draft"})
        if code == 200:
            return body
        print("  start not ready yet: %s %s" % (code, json.dumps(body)[:160]))
        return None

    while time.monotonic() < deadline and not started:
        started = attempt()
        if not started:
            time.sleep(20)
    if not started:
        raise SystemExit("campaign start never became eligible")
    print("campaign started")

    targets = c.get("/deployments/%s/targets" % cid)
    canary = [t for t in targets if t["ring"] == 0]
    if len(canary) != 1:
        raise SystemExit("expected exactly one ring-0 canary, got %d" % len(canary))
    canary_id = canary[0]["device_id"]
    print("canary device: %s (assigned generation %s)" % (canary_id, canary[0]["assigned_generation"]))

    def converged():
        d = device_row(c, canary_id)
        return d if (d["actual_release_id"] == v2["release_id"]
                     and d["applied_generation"] == canary[0]["assigned_generation"]
                     and d["health_status"] == "healthy") else None

    ok = wait_for(converged, a.converge_timeout, 10, "canary convergence to v2")
    if not ok:
        raise SystemExit("canary did not converge to v2")

    fleet_now = []
    for entry in fleet["devices"]:
        d = device_row(c, entry["device_id"])
        fleet_now.append({"device_id": entry["device_id"], "name": entry["name"],
                          "actual_release_id": d["actual_release_id"],
                          "desired_release_id": d["desired_release_id"],
                          "applied_generation": d["applied_generation"],
                          "desired_generation": d["desired_generation"],
                          "health_status": d["health_status"],
                          "agent_state": d["agent_state"],
                          "version": ("v2" if d["actual_release_id"] == v2["release_id"] else
                                      "v1" if d["actual_release_id"] == v1["release_id"] else "other")})
    distribution = {}
    for row in fleet_now:
        distribution[row["version"]] = distribution.get(row["version"], 0) + 1
    gate = campaign_state(c, cid)
    out = {
        "phase": "A",
        "recorded_at": now_iso(),
        "baseline_release": {"version": v1["version_label"], "release_id": v1["release_id"],
                             "model_sha256": v1["model_sha256"]},
        "candidate_release": {"version": v2["version_label"], "release_id": v2["release_id"],
                              "purpose": v2.get("purpose"), "model_sha256": v2["model_sha256"]},
        "fleet_size": len(fleet["devices"]),
        "campaign_id": cid,
        "campaign_status": gate["campaign"]["status"],
        "canary_device_id": canary_id,
        "canary_assigned_generation": canary[0]["assigned_generation"],
        "fleet_fully_eligible_before_start": ready,
        "eligibility_probe_history": probe_history,
        "baselines": {e["name"]: baselines.get(e["device_id"], {}).get("aggregate")
                      for e in fleet["devices"]},
        "baseline_reasons": {e["name"]: baselines.get(e["device_id"], {}).get("reasons")
                             for e in fleet["devices"]},
        "fleet_state": fleet_now,
        "distribution": distribution,
        "gate_status": gate["gate_status"],
        "gate_reasons": gate["gate_reasons"],
        "canary_healthy": ok["health_status"] == "healthy",
    }
    (ROOT / "var/hansung-canary-phaseA.json").write_text(json.dumps(out, indent=2))
    print("distribution:", distribution)
    print("gate:", gate["gate_status"], gate["gate_reasons"])
    print("phase A evidence written")


def snapshot(c, v1, v2, fleet, canary_id):
    rows_out = []
    for entry in fleet["devices"]:
        d = device_row(c, entry["device_id"])
        rows_out.append({"name": entry["name"], "device_id": entry["device_id"],
                         "is_canary": entry["device_id"] == canary_id,
                         "desired_release_id": d["desired_release_id"],
                         "actual_release_id": d["actual_release_id"],
                         "version": ("v1" if d["actual_release_id"] == v1["release_id"] else
                                     "v2" if d["actual_release_id"] == v2["release_id"] else "other"),
                         "desired_generation": d["desired_generation"],
                         "applied_generation": d["applied_generation"],
                         "converged": d["desired_generation"] == d["applied_generation"],
                         "health_status": d["health_status"],
                         "agent_state": d["agent_state"]})
    distribution = {}
    for row in rows_out:
        key = row["version"]
        distribution[key] = distribution.get(key, 0) + 1
    return rows_out, distribution


def phase_b(a):
    c = make_client()
    v1 = json.loads(V1_FILE.read_text())
    v2 = json.loads(V2_FILE.read_text())
    fleet = json.loads(FLEET_FILE.read_text())
    cid = a.campaign_id
    if not cid:
        cid = json.loads((ROOT / "var/hansung-canary-campaign.json").read_text())["campaign_id"]
    targets = c.get("/deployments/%s/targets" % cid)
    canary = [t for t in targets if t["ring"] == 0][0]
    canary_id = canary["device_id"]
    assigned = canary["assigned_generation"]
    canary_state = next(e["state_dir"] for e in fleet["devices"] if e["device_id"] == canary_id)
    others = [e for e in fleet["devices"] if e["device_id"] != canary_id]
    print("campaign=%s canary=%s assigned_generation=%s others=%d"
          % (cid, canary_id, assigned, len(others)))

    start_agents(fleet)

    # ---- step 5: prove the healthy canary path BEFORE any failure injection ----
    def canary_healthy_on_v2():
        d = device_row(c, canary_id)
        if (d["actual_release_id"] == v2["release_id"]
                and d["applied_generation"] == assigned
                and d["health_status"] == "healthy"):
            return d
        return None

    healthy = wait_for(canary_healthy_on_v2, a.converge_timeout, 10,
                       "canary healthy on ppe-hansung-v2")
    if not healthy:
        raise SystemExit("canary never became healthy on v2; campaign=%s"
                         % campaign_state(c, cid)["campaign"]["status"])
    print("canary healthy on v2: version=%s generation=%s" % (
        healthy["actual_release_id"], healthy["applied_generation"]))

    pre_rows, pre_distribution = snapshot(c, v1, v2, fleet, canary_id)
    pre_gate = campaign_state(c, cid)
    print("pre-failure distribution: %s" % pre_distribution)
    print("gate: %s %s" % (pre_gate["gate_status"], pre_gate["gate_reasons"]))
    phase_a_evidence = {
        "phase": "A",
        "recorded_at": now_iso(),
        "baseline_release": {"version": v1["version_label"], "release_id": v1["release_id"],
                             "model_sha256": v1["model_sha256"]},
        "candidate_release": {"version": v2["version_label"], "release_id": v2["release_id"],
                              "purpose": v2.get("purpose"),
                              "model_sha256": v2["model_sha256"]},
        "fleet_size": len(fleet["devices"]),
        "campaign_id": cid,
        "campaign_status": pre_gate["campaign"]["status"],
        "canary_device_id": canary_id,
        "canary_assigned_generation": assigned,
        "fleet_state": pre_rows,
        "distribution": pre_distribution,
        "gate_status": pre_gate["gate_status"],
        "gate_reasons": pre_gate["gate_reasons"],
        "canary_healthy": True,
        "ring_0_targets": len([t for t in targets if t["ring"] == 0]),
        "assigned_targets": len([t for t in targets if t["assigned_generation"]]),
    }
    (ROOT / "var/hansung-canary-phaseA.json").write_text(json.dumps(phase_a_evidence, indent=2))
    print("phase A evidence written")

    # ---- step 6: inject the existing supported failure on the canary only ----
    print(subprocess.run([sys.executable, "-m", "scripts.simulate_fault", "--state",
                          canary_state, "--fault", "offline"], cwd=str(ROOT),
                         capture_output=True, text=True).stdout.strip())
    before = device_row(c, canary_id)
    print("canary before failure: actual=%s health=%s" % (
        before["actual_release_id"], before["health_status"]))

    def paused():
        state = campaign_state(c, cid)
        return state if state["campaign"]["status"] == "paused" else None

    def offline():
        return campaign_state(c, cid)

    print("waiting for controller to pause the rollout ...")
    paused_state = wait_for(paused, a.pause_timeout, 10, "campaign pause")
    status_after_failure = campaign_state(c, cid)["campaign"]["status"]
    if not paused_state:
        print("campaign did not pause; status=%s" % status_after_failure)

    # No unintended rollout: the other nine must still be on v1, desired and actual.
    fail_rows, fail_distribution = snapshot(c, v1, v2, fleet, canary_id)
    others_state = [row for row in fail_rows if not row["is_canary"]]
    unintended = [o for o in others_state
                  if o["version"] != "v1" or o["desired_release_id"] != v1["release_id"]]
    print("during-failure distribution: %s" % fail_distribution)
    canary_failed = device_row(c, canary_id)

    # Controller restart must not lose persisted campaign state.
    print("restarting controller to prove persistence ...")
    subprocess.run(["docker", "compose", "--env-file", ".env", "-f",
                    "infrastructure/compose.yaml", "restart", "controller"],
                   cwd=str(ROOT), capture_output=True, text=True)
    time.sleep(20)
    after_restart = campaign_state(c, cid)

    print("issuing central rollback ...")
    code, body = request(c, "POST", "/deployments/%s/rollback" % cid,
                         {"reason": "canary unhealthy; restore ppe-hansung-v1"})
    if code != 200:
        raise SystemExit("rollback failed: %s %s" % (code, body))
    print("rollback accepted; campaign status=%s" % body["data"]["status"])

    print("recovering the canary fault so it can reconcile back to v1 ...")
    print(subprocess.run([sys.executable, "-m", "scripts.simulate_fault", "--state",
                          canary_state, "--fault", "offline", "--recover"], cwd=str(ROOT),
                         capture_output=True, text=True).stdout.strip())

    def rolled_back():
        state = campaign_state(c, cid)
        d = device_row(c, canary_id)
        if state["campaign"]["status"] == "rolled_back" and d["actual_release_id"] == v1["release_id"] \
                and d["health_status"] == "healthy":
            return (state, d)
        return None

    done = wait_for(rolled_back, a.rollback_timeout, 10, "central rollback completion")
    if not done:
        print("rollback not finalised; campaign=%s"
              % campaign_state(c, cid)["campaign"]["status"])

    final, final_distribution = snapshot(c, v1, v2, fleet, canary_id)
    healthy_v1 = sum(1 for row in final
                     if row["version"] == "v1" and row["health_status"] == "healthy")
    campaign_final = campaign_state(c, cid)
    events = c.get("/deployments/%s/events" % cid)
    out = {
        "phase": "B",
        "recorded_at": now_iso(),
        "campaign_id": cid,
        "canary_device_id": canary_id,
        "failure_mechanism": "scripts.simulate_fault --fault offline (existing supported mechanism)",
        "campaign_status_after_failure": status_after_failure,
        "campaign_status_after_controller_restart": after_restart["campaign"]["status"],
        "campaign_status_final": campaign_final["campaign"]["status"],
        "controller_restart_preserved_state": (
            after_restart["campaign"]["status"] == status_after_failure),
        "distribution_before_failure": pre_distribution,
        "distribution_during_failure": fail_distribution,
        "others_state_during_failure": others_state,
        "unintended_rollout": unintended,
        "no_unintended_rollout": not unintended,
        "canary_during_failure": {"actual_release_id": canary_failed["actual_release_id"],
                                  "desired_release_id": canary_failed["desired_release_id"],
                                  "health_status": canary_failed["health_status"],
                                  "agent_state": canary_failed["agent_state"]},
        "final_fleet": final,
        "final_distribution": final_distribution,
        "final_v1_healthy_count": healthy_v1,
        "campaign_timeline": [{"event": e["event_type"], "reason": e["reason"],
                               "created_at": e["created_at"]} for e in events],
        "gate_status_final": campaign_final["gate_status"],
        "rollback_finalised": campaign_final["campaign"]["status"] == "rolled_back",
    }
    (ROOT / "var/hansung-canary-phaseB.json").write_text(json.dumps(out, indent=2))
    print("final distribution:", final_distribution)
    print("campaign final:", campaign_final["campaign"]["status"])
    print("phase B evidence written")


def phase_campaign(a):
    """Create and start a fresh canary campaign on ppe-hansung-v2 (ring 0 only)."""
    c = make_client()
    v1 = json.loads(V1_FILE.read_text())
    v2 = json.loads(V2_FILE.read_text())
    fleet = json.loads(FLEET_FILE.read_text())
    start_agents(fleet)
    ids = [e["device_id"] for e in fleet["devices"]]
    deadline = time.monotonic() + a.warmup
    while time.monotonic() < deadline:
        time.sleep(30)
        snapshot_probe = probe_eligibility(ids, v1["release_id"])
        if eligibility_verdict(snapshot_probe):
            print("fleet eligible at baseline %s" % v1["version_label"])
            sample = next(iter(snapshot_probe.values()))
            print("  gate aggregate: %s" % json.dumps(sample.get("aggregate")))
            break
        first = next(iter(snapshot_probe.values())) if "probe_error" not in snapshot_probe else snapshot_probe
        print("  not eligible yet: %s" % json.dumps(first)[:200])
    else:
        print("WARNING: fleet not fully eligible; attempting start anyway")
    status, payload = request(c, "POST", "/deployments", {
        "release_id": v2["release_id"], "target_device_ids": ids,
        "reason": "Hansung canary rollout verification: ppe-hansung-v2 candidate"})
    if status != 201:
        raise SystemExit("campaign create failed: %s %s" % (status, payload))
    cid = payload["data"]["campaign"]["deployment_campaign_id"]
    print("campaign %s created; eligibility=%s" % (cid, json.dumps(payload["data"]["eligibility"])))
    started = None
    deadline = time.monotonic() + a.start_timeout
    while time.monotonic() < deadline and not started:
        code, body = request(c, "POST", "/deployments/%s/start" % cid,
                            {"reason": "begin canary ring 0", "expected_status": "draft"})
        if code == 200:
            started = body
            break
        print("  start not ready yet: %s %s" % (code, json.dumps(body)[:160]))
        time.sleep(20)
    if not started:
        raise SystemExit("campaign start never became eligible")
    targets = c.get("/deployments/%s/targets" % cid)
    canary = [t for t in targets if t["ring"] == 0]
    print("campaign started; ring0 targets=%d assigned=%d" % (
        len(canary), len([t for t in targets if t["assigned_generation"]])))
    (ROOT / "var/hansung-canary-campaign.json").write_text(json.dumps(
        {"campaign_id": cid, "created_at": now_iso(),
         "canary_device_id": canary[0]["device_id"],
         "canary_assigned_generation": canary[0]["assigned_generation"],
         "eligibility": payload["data"]["eligibility"]}, indent=2))
    print("canary=%s generation=%s" % (canary[0]["device_id"], canary[0]["assigned_generation"]))


def phase_cleanup(a):
    """Roll a stalled campaign back with the real mechanism, freeing the devices."""
    c = make_client()
    v1 = json.loads(V1_FILE.read_text())
    v2 = json.loads(V2_FILE.read_text())
    fleet = json.loads(FLEET_FILE.read_text())
    cid = a.campaign_id
    start_agents(fleet)
    state = campaign_state(c, cid)
    print("campaign %s status before cleanup rollback: %s" % (cid, state["campaign"]["status"]))
    code, body = request(c, "POST", "/deployments/%s/rollback" % cid, {
        "reason": "canary stalled before convergence; restore ppe-hansung-v1"})
    if code != 200:
        raise SystemExit("cleanup rollback rejected: %s %s" % (code, body))
    print("rollback accepted; status=%s" % body["data"]["status"])

    def finished():
        st = campaign_state(c, cid)
        return st if st["campaign"]["status"] == "rolled_back" else None

    done = wait_for(finished, a.rollback_timeout, 10, "cleanup rollback completion")
    rows, distribution = snapshot(c, v1, v2, fleet, "")
    out = {"campaign_id": cid, "recorded_at": now_iso(),
           "status_before": state["campaign"]["status"],
           "status_after": campaign_state(c, cid)["campaign"]["status"],
           "rolled_back": bool(done), "fleet": rows, "distribution": distribution}
    (ROOT / "var/hansung-canary-cleanup.json").write_text(json.dumps(out, indent=2))
    print("cleanup distribution: %s" % distribution)
    if not done:
        raise SystemExit("cleanup rollback did not finalise")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("phase", choices=["a", "b", "provision", "campaign", "cleanup"])
    p.add_argument("--warmup", type=int, default=420,
                   help="max seconds to wait for the gate's baseline window")
    p.add_argument("--start-timeout", type=int, default=300)
    p.add_argument("--converge-timeout", type=int, default=240)
    p.add_argument("--pause-timeout", type=int, default=420)
    p.add_argument("--rollback-timeout", type=int, default=420)
    p.add_argument("--campaign-id", default=None)
    p.add_argument("--resume", action="store_true")
    a = p.parse_args()
    if a.phase == "provision":
        provision(make_client())
    elif a.phase == "a":
        phase_a(a)
    elif a.phase == "campaign":
        phase_campaign(a)
    elif a.phase == "cleanup":
        phase_cleanup(a)
    else:
        phase_b(a)


if __name__ == "__main__":
    main()
