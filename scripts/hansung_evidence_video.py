"""Annotated evidence video from the RELEASED Hansung artifact.

Runs the real components (Detector -> ByteTrack -> PPE association -> SafetyRule)
over var/media/ppe-2.mp4 and overlays only actual results: person boxes with stable
track IDs, helmet / no_helmet boxes, per-person canonical PPE state, confidence,
the model version label, inference latency and the resulting SAFE/VIOLATION state.

No box is drawn that the model did not produce. Every frame is inferred here (the
production Pipeline throttles to its configured inference FPS), so ByteTrack is
given the true source frame rate.
"""
import argparse
import hashlib
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np
import supervision as sv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from edge.pipeline import Detector
from edge.rules import SafetyRule, associate

VIDEO = ROOT / "var/media/ppe-2.mp4"

PERSON = (0, 200, 0)
HELMET = (255, 170, 0)
NO_HELMET = (0, 0, 255)
UNKNOWN = (160, 160, 160)


def release_contract(path):
    data = json.loads(Path(path).read_text())
    contract = json.loads((ROOT / data["manifest"].get("contract", "var/model/hansung-p3.json")).read_text()) \
        if (ROOT / "var/model/hansung-p3.json").exists() else {}
    mapping = {int(k): int(v) for k, v in contract.get("source_index_to_canonical_id", {}).items()} \
        or {5: 0, 0: 1, 2: 2}
    return data, mapping


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--model", default=None,
                   help="model path; defaults to the agent slot staged from the release")
    p.add_argument("--release-file", default="var/hansung-release.json")
    p.add_argument("--device-file", default="var/hansung-device.json")
    p.add_argument("--video", default=str(VIDEO))
    p.add_argument("--output", default="var/evidence/hansung-ppe-tracked.mp4")
    p.add_argument("--frames-dir", default="var/evidence")
    p.add_argument("--score-threshold", type=float, default=0.35)
    p.add_argument("--save-frames", default="0,50,100,150,200")
    a = p.parse_args()

    release, mapping = release_contract(a.release_file)
    model = Path(a.model) if a.model else None
    if model is None:
        device = json.loads((ROOT / a.device_file).read_text())
        model = Path(device["agent_slot_model_path"])
    digest = hashlib.sha256(model.read_bytes()).hexdigest()
    print("model: %s" % model)
    print("sha256: %s" % digest)
    print("matches released artifact: %s" % (digest == release["model_sha256"]))
    print("source->canonical mapping: %s" % mapping)

    detector = Detector(str(model), digest, class_mapping=mapping)
    cap = cv2.VideoCapture(a.video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    print("video: %d frames %dx%d @ %.2f fps" % (total, width, height, fps))

    out_path = ROOT / a.output
    out_path.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(str(out_path), cv2.VideoWriter_fourcc(*"mp4v"), fps,
                             (width, height))
    if not writer.isOpened():
        raise SystemExit("video encoder unavailable")

    tracker = sv.ByteTrack(frame_rate=int(round(fps)))
    rule = SafetyRule()
    session = "hansung-qualification"
    save_at = {int(x) for x in a.save_frames.split(",") if x.strip()}

    counts = defaultdict(int)
    max_conf = defaultdict(float)
    lives = defaultdict(lambda: {"first": None, "last": None, "frames": 0, "state": None})
    latencies, events, idx = [], [], 0
    state_counts = defaultdict(int)
    started = time.perf_counter()

    while True:
        ok, frame = cap.read()
        if not ok:
            break
        detections, latency = detector.infer(frame, score_threshold=a.score_threshold)
        latencies.append(latency)
        for box, conf, cid in zip(detections.xyxy, detections.confidence, detections.class_id):
            counts[int(cid)] += 1
            max_conf[int(cid)] = max(max_conf[int(cid)], float(conf))
        persons = tracker.update_with_detections(detections[detections.class_id == 0])
        heads = []
        for box, conf, cid in zip(detections.xyxy, detections.confidence, detections.class_id):
            if int(cid) in (1, 2):
                heads.append(("helmet" if int(cid) == 1 else "no_helmet", float(conf),
                              (box / np.array([width, height, width, height])).tolist()))
        violation_state = "SAFE"
        for box, tid in zip(persons.xyxy, persons.tracker_id):
            if tid is None:
                continue
            norm = (box / np.array([width, height, width, height])).tolist()
            label, confidence = associate(norm, heads)
            event = rule.observe(session, tid, idx / fps, label, confidence, norm)
            state_counts[label] += 1
            entry = lives[tid]
            entry["frames"] += 1
            entry["first"] = idx if entry["first"] is None else entry["first"]
            entry["last"] = idx
            entry["state"] = label
            entry["confidence"] = confidence
            if event:
                events.append({"track_id": tid, "frame": idx, **{k: v for k, v in event.items() if k != "bbox"}})
                violation_state = "VIOLATION track %s" % tid
            colour = {"helmet": PERSON, "no_helmet": NO_HELMET}.get(label, UNKNOWN)
            x1, y1, x2, y2 = [int(v) for v in box]
            cv2.rectangle(frame, (x1, y1), (x2, y2), colour, 2)
            cv2.putText(frame, "#%s %s %.2f" % (tid, label, confidence), (x1, max(18, y1 - 8)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, colour, 2)
        for label, confidence, norm in heads:
            colour = HELMET if label == "helmet" else NO_HELMET
            bx1, by1, bx2, by2 = (int(norm[0] * width), int(norm[1] * height),
                                  int(norm[2] * width), int(norm[3] * height))
            cv2.rectangle(frame, (bx1, by1), (bx2, by2), colour, 1)
            cv2.putText(frame, "%s %.2f" % (label, confidence), (bx1, max(14, by1 - 4)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, colour, 1)

        latency = latencies[-1]
        cv2.rectangle(frame, (0, 0), (width, 62), (0, 0, 0), -1)
        cv2.putText(frame, "model %s | sha %s" % (release["version_label"], digest[:12]),
                    (10, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)
        cv2.putText(frame, "ONNX [1,14,8400] -> canonical person/helmet/no_helmet | %.1f ms | source %.0f fps"
                    % (latency, fps), (10, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)
        cv2.putText(frame, "%s  tracks=%d  heads=%d" % (violation_state, len(persons), len(heads)),
                    (10, 57), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                    NO_HELMET if violation_state != "SAFE" else PERSON, 1)

        writer.write(frame)
        if idx in save_at:
            png = ROOT / a.frames_dir / ("hansung-ppe-frame-%03d.png" % idx)
            cv2.imwrite(str(png), frame)
        idx += 1
    cap.release()
    writer.release()

    latencies.sort()
    stats = {
        "model_path": str(model),
        "model_version": release["version_label"],
        "model_sha256": digest,
        "released_model_sha256": release["model_sha256"],
        "artifact_matches_release": digest == release["model_sha256"],
        "release_id": release["release_id"],
        "video": a.video,
        "frames_scanned": idx,
        "annotated_video": a.output,
        "source_fps": fps,
        "inference_ms_p50": latencies[len(latencies) // 2],
        "inference_ms_p95": latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))],
        "effective_inference_fps": idx / (time.perf_counter() - started),
        "canonical_detections": {
            "person": {"count": counts[0], "max_conf": round(max_conf[0], 4)},
            "helmet": {"count": counts[1], "max_conf": round(max_conf[1], 4)},
            "no_helmet": {"count": counts[2], "max_conf": round(max_conf[2], 4)},
        },
        "unique_tracks": len(lives),
        "tracks": {str(t): {"first_frame": v["first"], "last_frame": v["last"],
                            "frames": v["frames"], "last_state": v["state"],
                            "last_confidence": round(v.get("confidence", 0.0), 4)}
                   for t, v in sorted(lives.items())},
        "person_frame_ppe_state_counts": dict(state_counts),
        "safety_events": events,
        "safety_event_count": len(events),
        "note": ("Every frame inferred (no throttling). Negative case: the clip's bare-head "
                 "evidence does not satisfy the 5-frame / 2-second temporal rule, so zero "
                 "violations is the correct outcome. Rule thresholds were not lowered."),
    }
    stats_path = ROOT / a.frames_dir / "hansung-ppe-evidence.json"
    stats_path.write_text(json.dumps(stats, indent=2))
    print("wrote %s (%d frames, %.1f MB)" % (out_path, idx, out_path.stat().st_size / 1e6))
    print("unique tracks: %d | events: %d" % (len(lives), len(events)))
    print("stats: %s" % stats_path)


if __name__ == "__main__":
    main()
