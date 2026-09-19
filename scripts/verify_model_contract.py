"""Prove the CPU adapter and the NVIDIA parser share one canonical model contract.

Checks, in order of strength:

1. Generated DeepStream artifacts are not stale and agree with
   `shared/model_contract.py`.
2. `nvinfer.txt` declares the published source head (10), not the canonical
   subset (3), and `parser.cpp` embeds no hardcoded channel/class shape.
3. The contract reproduces the mapping documented in the qualified artifact
   record (`var/model/hansung-p3.json`).
4. The profile is auto-detected from the model's own declared labels, and the
   explicit-mapping callers still get byte-identical detections.
5. An independent numpy reference decoder (written here, not shared code) agrees
   with `Detector.infer` on real frames of the qualified artifact.

Writes `docs/evidence/model-contract-runtime.json`. Nothing here fabricates a
detector-quality result.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(ROOT))

from shared import model_contract  # noqa: E402

DEEPSTREAM = ROOT / 'edge/pipeline/deepstream'
ARTIFACT = ROOT / 'var/model/hansung-p3.onnx'
RECORD = ROOT / 'var/model/hansung-p3.json'
VIDEO = ROOT / 'var/media/ppe-2.mp4'
FORBIDDEN_PARSER_PATTERNS = (r'!=3\b', r'!= 3\b', r'!=7\b', r'!= 7\b', r'numClassesConfigured\s*!=\s*3')


def generated_contract():
    contract = model_contract.HANSUNG_PPE_10
    changed = __import__('scripts.gen_deepstream_contract', fromlist=['write']).write(DEEPSTREAM, contract, check=True)
    header = (DEEPSTREAM / 'visionops_contract.h').read_text()
    nvinfer = (DEEPSTREAM / 'nvinfer.txt').read_text()
    payload = json.loads((DEEPSTREAM / 'visionops_contract.json').read_text())
    constants = contract.cpp_constants()
    classes = int(re.search(r'num-detected-classes=(\d+)', nvinfer).group(1))
    threshold = float(re.search(r'pre-cluster-threshold=([0-9.]+)', nvinfer).group(1))
    expected_macros = [f'#define VISIONOPS_SOURCE_COUNT {constants["VISIONOPS_SOURCE_COUNT"]}',
                       f'#define VISIONOPS_RAW_CHANNELS {constants["VISIONOPS_RAW_CHANNELS"]}',
                       f'#define VISIONOPS_CANONICAL_CLASS_COUNT {constants["VISIONOPS_CANONICAL_CLASS_COUNT"]}',
                       '#define VISIONOPS_MAPPED_COUNT ' + str(len(constants['VISIONOPS_MAPPED_SOURCE_INDEX']))]
    compressed = header.replace(' ', '')
    results = {'stale_generated_files': changed,
               'declared_classes': classes, 'declared_threshold': threshold,
               'raw_channels': payload['raw_channels'], 'mapping_version': payload['mapping_version'],
               'header_matches_contract': all(macro in header for macro in expected_macros),
               'mapped_indices_in_header': ('{' + ','.join(str(v) for v in constants['VISIONOPS_MAPPED_SOURCE_INDEX']) + '}') in compressed,
               'mapped_ids_in_header': ('{' + ','.join(str(v) for v in constants['VISIONOPS_MAPPED_CANONICAL_ID']) + '}') in compressed}
    if changed:
        raise RuntimeError('stale generated DeepStream artifacts: ' + ', '.join(changed))
    if classes != contract.source_count or classes != 10:
        raise RuntimeError('nvinfer class count does not match the source head')
    if classes == 3:
        raise RuntimeError('nvinfer still declares the canonical subset as the source head')
    if abs(threshold - contract.score_threshold) > 1e-9:
        raise RuntimeError('nvinfer threshold differs from the contract')
    if not results['header_matches_contract'] or not results['mapped_indices_in_header'] or not results['mapped_ids_in_header']:
        raise RuntimeError('generated header does not encode the canonical contract')
    return results


def parser_static_check():
    source = (DEEPSTREAM / 'parser.cpp').read_text()
    findings = [pattern for pattern in FORBIDDEN_PARSER_PATTERNS if re.search(pattern, source)]
    if findings:
        raise RuntimeError('parser.cpp still contains hardcoded shape checks: ' + ', '.join(findings))
    for macro in ('VISIONOPS_RAW_CHANNELS', 'VISIONOPS_MAPPED_SOURCE_INDEX', 'VISIONOPS_MAPPED_CANONICAL_ID',
                  'VISIONOPS_BOX_VALUES', 'VISIONOPS_MAPPED_COUNT'):
        if macro not in source:
            raise RuntimeError('parser.cpp does not use ' + macro)
    if 'visionops_contract.h' not in source:
        raise RuntimeError('parser.cpp does not include the generated contract')
    return {'hardcoded_shape_patterns': [], 'macros_used': 5,
            'verdict': 'parser consumes the generated canonical contract'}


def documented_mapping_check():
    if not RECORD.exists():
        return {'status': 'SKIPPED - qualified artifact record absent'}
    record = json.loads(RECORD.read_text())
    documented = {int(k): int(v) for k, v in record['source_index_to_canonical_id'].items()}
    contract = model_contract.HANSUNG_PPE_10
    if documented != contract.mapping:
        raise RuntimeError('shared contract mapping differs from the qualified artifact record')
    if record['output']['channels'] != contract.channels:
        raise RuntimeError('contract channel count differs from the qualified artifact record')
    if record['output']['class_scores'] != contract.source_count:
        raise RuntimeError('contract source count differs from the qualified artifact record')
    return {'status': 'VERIFIED', 'source_index_to_canonical': {str(k): v for k, v in documented.items()},
            'channels': contract.channels, 'source_classes': contract.source_count}


def detection_check(frames=3):
    if not ARTIFACT.exists() or not VIDEO.exists():
        return {'status': 'SKIPPED - qualified artifact or evidence video absent'}
    import cv2

    from edge.pipeline import Detector
    digest = hashlib.sha256(ARTIFACT.read_bytes()).hexdigest()
    auto = Detector(ARTIFACT, digest)
    explicit = Detector(ARTIFACT, digest, class_mapping={5: 0, 0: 1, 2: 2})
    if auto.contract.profile != 'hansung_ppe_yolov8n_10':
        raise RuntimeError('profile was not auto-detected from model metadata')
    if explicit.contract.mapping != auto.contract.mapping:
        raise RuntimeError('explicit mapping callers diverge from auto-detection')
    capture = cv2.VideoCapture(str(VIDEO))
    compared = 0
    max_box_delta = 0.0
    max_confidence_delta = 0.0
    classes_seen = {}
    while compared < frames:
        ok, frame = capture.read()
        if not ok:
            break
        auto_detections, _ = auto.infer(frame)
        explicit_detections, _ = explicit.infer(frame)
        if not np.array_equal(auto_detections.xyxy, explicit_detections.xyxy):
            raise RuntimeError('auto and explicit contract produced different boxes')
        reference = reference_decode(auto, frame)
        if reference[0].shape != auto_detections.xyxy.shape:
            raise RuntimeError('independent reference decoder found a different number of boxes')
        if auto_detections.xyxy.size:
            max_box_delta = max(max_box_delta, float(np.abs(reference[0] - auto_detections.xyxy).max()))
            max_confidence_delta = max(max_confidence_delta, float(np.abs(reference[1] - auto_detections.confidence).max()))
            for class_id in auto_detections.class_id:
                classes_seen[str(int(class_id))] = classes_seen.get(str(int(class_id)), 0) + 1
        compared += 1
    capture.release()
    if compared == 0:
        return {'status': 'SKIPPED - evidence video has no readable frames'}
    if max_box_delta > 1e-3 or max_confidence_delta > 1e-6:
        raise RuntimeError('independent reference decoder disagrees with the canonical decoder')
    return {'status': 'VERIFIED', 'profile': auto.contract.profile, 'runtime': auto.runtime_name,
            'frames_compared': compared, 'boxes_compared': int(sum(classes_seen.values())),
            'classes_seen': classes_seen, 'max_box_delta_pixels': round(max_box_delta, 6),
            'max_confidence_delta': round(max_confidence_delta, 9),
            'reference_implementation': 'independent numpy decode in scripts/verify_model_contract.py'}


def reference_decode(detector, frame):
    """Independent decode of the same raw tensor; deliberately does not import shared code."""
    height, width = frame.shape[:2]
    scale = min(640 / width, 640 / height)
    inner_w, inner_h = round(width * scale), round(height * scale)
    left, top = (640 - inner_w) // 2, (640 - inner_h) // 2
    import cv2
    canvas = np.full((640, 640, 3), 114, np.uint8)
    canvas[top:top + inner_h, left:left + inner_w] = cv2.resize(frame, (inner_w, inner_h))
    tensor = canvas[:, :, ::-1].transpose(2, 0, 1)[None].astype(np.float32) / 255.0
    raw = np.squeeze(np.asarray(detector.runtime.infer(np.ascontiguousarray(tensor))))
    if raw.shape[0] < raw.shape[1]:
        raw = raw.T
    scores = raw[:, 4:]
    sources = [5, 0, 2]
    canonical_for_index = {0: 1, 1: 2, 2: 0}
    subset = scores[:, sources]
    chosen = subset.argmax(1)
    confidence = subset.max(1)
    boxes, kept_confidence, kept_classes = [], [], []
    for row, index, score in zip(raw, chosen, confidence):
        if not np.isfinite(score) or score < 0.35:
            continue
        cx, cy, bw, bh = row[:4]
        box = np.array([(cx - bw / 2 - left) / scale, (cy - bh / 2 - top) / scale,
                        (cx + bw / 2 - left) / scale, (cy + bh / 2 - top) / scale])
        box[[0, 2]] = np.clip(box[[0, 2]], 0, width)
        box[[1, 3]] = np.clip(box[[1, 3]], 0, height)
        if box[2] <= box[0] or box[3] <= box[1]:
            continue
        boxes.append(box)
        kept_confidence.append(float(score))
        kept_classes.append(canonical_for_index[int(index)])
    if not boxes:
        return np.zeros((0, 4), np.float32), np.zeros(0, np.float32), np.zeros(0, np.int64)
    boxes = np.array(boxes, np.float32)
    kept_confidence = np.array(kept_confidence, np.float32)
    kept_classes = np.array(kept_classes, np.int64)
    keep = []
    for class_id in np.unique(kept_classes):
        indices = np.where(kept_classes == class_id)[0]
        order = indices[np.argsort(-kept_confidence[indices])]
        while len(order):
            current = order[0]
            keep.append(int(current))
            if len(order) == 1:
                break
            rest = boxes[order[1:]]
            current_box = boxes[current]
            inter_x = np.clip(np.minimum(current_box[2], rest[:, 2]) - np.maximum(current_box[0], rest[:, 0]), 0, None)
            inter_y = np.clip(np.minimum(current_box[3], rest[:, 3]) - np.maximum(current_box[1], rest[:, 1]), 0, None)
            intersection = inter_x * inter_y
            area = (current_box[2] - current_box[0]) * (current_box[3] - current_box[1])
            other = (rest[:, 2] - rest[:, 0]) * (rest[:, 3] - rest[:, 1])
            iou = intersection / np.maximum(area + other - intersection, 1e-12)
            order = order[1:][iou <= 0.5]
    keep = np.array(sorted(keep), np.int64)
    return boxes[keep], kept_confidence[keep], kept_classes[keep]


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', default='docs/evidence/model-contract-runtime.json')
    parser.add_argument('--frames', type=int, default=3)
    arguments = parser.parse_args()
    report = {'generated_deepstream_contract': generated_contract(),
              'parser_static_check': parser_static_check(),
              'documented_mapping': documented_mapping_check(),
              'detection_equivalence': detection_check(arguments.frames),
              'note': 'Static + CPU inference equivalence only. No NVIDIA runtime, engine or GPU result is claimed.'}
    Path(arguments.output).parent.mkdir(parents=True, exist_ok=True)
    Path(arguments.output).write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
