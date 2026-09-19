"""PyTorch checkpoint -> ONNX export with mandatory graph-parity qualification.

    checkpoint -> export -> raw PyTorch tensor vs raw ONNX tensor on identical
                        frames and identical preprocessing
                        -> the SAME canonical decoder on both
                        -> structured report -> optional MLflow run

Export succeeding is never treated as qualification. Parity is only `parity_passed`
when every PyTorch detection is matched by an ONNX detection of the same canonical
class at IoU >= 0.5 with |confidence delta| <= 0.05, on every sampled frame, at both
the operating threshold (0.35) and a low stress threshold (0.001).

The report also measures how far the canonical adapter is from the model head's own
argmax, which is the reason the NVIDIA parser must reuse the same subset-argmax rule.

Model *quality* (mAP, temporal event precision/recall) is a separate question and is
recorded as not evaluated here.

    python -m scripts.export_onnx --checkpoint var/tools/hansung-best.pt \
        --output var/model/hansung-export.onnx --report docs/evidence/onnx-export-parity.json
"""
import argparse
import hashlib
import json
import time
import warnings
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(ROOT))

warnings.filterwarnings('ignore')

IOU_MATCH_MINIMUM = 0.5
CONFIDENCE_TOLERANCE = 0.05
OPERATING_THRESHOLD = 0.35
STRESS_THRESHOLD = 0.001


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def iou(box_a, box_b):
    x, y = max(box_a[0], box_b[0]), max(box_a[1], box_b[1])
    X, Y = min(box_a[2], box_b[2]), min(box_a[3], box_b[3])
    intersection = max(0.0, X - x) * max(0.0, Y - y)
    union = ((box_a[2] - box_a[0]) * (box_a[3] - box_a[1])
             + (box_b[2] - box_b[0]) * (box_b[3] - box_b[1]) - intersection)
    return float(intersection / max(union, 1e-12))


def python_preprocess(frame, size=(640, 640)):
    """The same letterbox the ONNX adapter uses, recorded here for transparency."""
    import cv2
    height, width = frame.shape[:2]
    target_w, target_h = size
    scale = min(target_w / width, target_h / height)
    inner_w, inner_h = round(width * scale), round(height * scale)
    left, top = (target_w - inner_w) // 2, (target_h - inner_h) // 2
    canvas = np.full((target_h, target_w, 3), 114, np.uint8)
    canvas[top:top + inner_h, left:left + inner_w] = cv2.resize(frame, (inner_w, inner_h))
    tensor = canvas[:, :, ::-1].transpose(2, 0, 1)[None].astype(np.float32) / 255.0
    geometry = {'scale': scale, 'pad_left': left, 'pad_top': top, 'frame_width': width,
                'frame_height': height, 'input_width': target_w, 'input_height': target_h}
    return np.ascontiguousarray(tensor), geometry


def head_argmax_count(raw, contract):
    """How many boxes the model head would label as a *discarded* source class."""
    predictions = np.squeeze(np.asarray(raw))
    if predictions.shape[0] < predictions.shape[1]:
        predictions = predictions.T
    scores = predictions[:, 4:]
    top = scores.argmax(1)
    return int(np.sum(~np.isin(top, list(contract.sources))))


def match(expected, actual):
    """Greedy best-IoU matching restricted to identical canonical classes."""
    matched, used = [], set()
    for index, box in enumerate(expected[0]):
        best_iou, best_index = 0.0, -1
        for candidate in range(len(actual[0])):
            if candidate in used or int(actual[2][candidate]) != int(expected[2][index]):
                continue
            overlap = iou(box, actual[0][candidate])
            if overlap > best_iou:
                best_iou, best_index = overlap, candidate
        if best_index >= 0 and best_iou >= IOU_MATCH_MINIMUM:
            used.add(best_index)
            matched.append({'iou': float(best_iou),
                            'confidence_delta': float(abs(expected[1][index] - float(actual[1][best_index]))),
                            'class_id': int(expected[2][index])})
        else:
            matched.append({'iou': float(best_iou), 'confidence_delta': None,
                            'class_id': int(expected[2][index]), 'unmatched': True})
    return matched, used


def compare(pytorch, onnx):
    matched, used = match(pytorch, onnx)
    unmatched = sum(1 for item in matched if item.get('unmatched'))
    overlaps = [item['iou'] for item in matched if not item.get('unmatched')]
    deltas = [item['confidence_delta'] for item in matched if not item.get('unmatched')]
    return {'compared': int(len(matched)), 'matched': int(len(matched) - unmatched), 'unmatched': int(unmatched),
            'onnx_only': int(len(onnx[0]) - len(used)),
            'min_iou': round(float(min(overlaps)), 4) if overlaps else None,
            'mean_iou': round(float(np.mean(overlaps)), 4) if overlaps else None,
            'max_confidence_delta': round(float(max(deltas)), 4) if deltas else None,
            'passed': bool(len(matched)) and unmatched == 0 and (min(overlaps) if overlaps else 0) >= IOU_MATCH_MINIMUM
            and (max(deltas) if deltas else 1.0) <= CONFIDENCE_TOLERANCE}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--checkpoint', required=True)
    parser.add_argument('--output', default='var/model/exported.onnx')
    parser.add_argument('--report', default='docs/evidence/onnx-export-parity.json')
    parser.add_argument('--contract-profile')
    parser.add_argument('--video', default='var/media/ppe-2.mp4')
    parser.add_argument('--frames', default='0,25,50,100,150,200')
    parser.add_argument('--opset', type=int, default=17)
    parser.add_argument('--imgsz', type=int, default=640)
    parser.add_argument('--mlflow-tracking-uri')
    parser.add_argument('--mlflow-experiment', default='visionops-model-lifecycle')
    parser.add_argument('--dataset-version', default=None)
    parser.add_argument('--code-commit', default=None)
    arguments = parser.parse_args()

    import cv2
    import onnx
    import torch
    from ultralytics import YOLO

    from edge.pipeline import Detector
    from shared import model_contract
    from shared.model_contract import canonical_detections

    contract = (model_contract.profile(arguments.contract_profile) if arguments.contract_profile
                else model_contract.HANSUNG_PPE_10)
    checkpoint = Path(arguments.checkpoint)
    if not checkpoint.is_file():
        raise SystemExit('checkpoint not found: ' + str(checkpoint))

    model = YOLO(str(checkpoint))
    declared = ({int(k): v for k, v in model.names.items()} if isinstance(model.names, dict)
               else dict(enumerate(model.names)))
    detected = model_contract.ModelContract.detect(declared)
    if detected.mapping != contract.mapping:
        raise SystemExit('checkpoint taxonomy does not match the requested contract profile')

    started = time.monotonic()
    exported = Path(model.export(format='onnx', imgsz=arguments.imgsz, opset=arguments.opset,
                                 dynamic=False, simplify=False))
    export_seconds = time.monotonic() - started
    if not exported.is_file():
        raise SystemExit('export produced no artifact')
    exported.replace(Path(arguments.output))
    artifact = Path(arguments.output)

    detector = Detector(artifact, digest(artifact), contract=contract)
    torch_model = model.model.float().eval()

    frames = [int(value) for value in arguments.frames.split(',') if value.strip()]
    capture = cv2.VideoCapture(arguments.video)
    available = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    operating, stress, per_frame, head_discarded, raw_delta = [], [], [], 0, 0.0
    for index in frames:
        if index >= available:
            continue
        capture.set(cv2.CAP_PROP_POS_FRAMES, index)
        ok, frame = capture.read()
        if not ok:
            continue
        tensor, geometry = python_preprocess(frame, tuple(contract.input_shape))
        with torch.no_grad():
            output = torch_model(torch.from_numpy(tensor))
        raw_pytorch = output[0] if isinstance(output, (list, tuple)) else output
        raw_pytorch = raw_pytorch.detach().cpu().numpy()
        raw_onnx = detector.runtime.infer(tensor)
        raw_delta = max(raw_delta, float(np.abs(np.squeeze(raw_pytorch) - np.squeeze(raw_onnx)).max()))
        head_discarded += head_argmax_count(raw_pytorch, contract)

        pt_boxes, pt_conf, pt_classes = canonical_detections(raw_pytorch, contract, OPERATING_THRESHOLD, geometry)
        onnx_detections, _ = detector.infer(frame, score_threshold=OPERATING_THRESHOLD)
        operating.append(({'frame': index, **compare(
            ([list(b) for b in pt_boxes], [float(c) for c in pt_conf], [int(c) for c in pt_classes]),
            ([[float(v) for v in b] for b in onnx_detections.xyxy],
             [float(v) for v in onnx_detections.confidence], [int(v) for v in onnx_detections.class_id]))}))

        pt_low = canonical_detections(raw_pytorch, contract, STRESS_THRESHOLD, geometry)
        onnx_low, _ = detector.infer(frame, score_threshold=STRESS_THRESHOLD)
        stress.append(({'frame': index, **compare(
            ([list(b) for b in pt_low[0]], [float(c) for c in pt_low[1]], [int(c) for c in pt_low[2]]),
            ([[float(v) for v in b] for b in onnx_low.xyxy],
             [float(v) for v in onnx_low.confidence], [int(v) for v in onnx_low.class_id]))}))
        per_frame.append({'frame': int(index), 'operating_detections': operating[-1]['compared'],
                          'stress_detections': stress[-1]['compared'],
                          'discarded_head_classes': int(head_argmax_count(raw_pytorch, contract))})
    capture.release()

    parity_passed = bool(operating) and all(item['passed'] for item in operating)
    stress_passed = bool(stress) and all(item['passed'] for item in stress)
    qualified = Path('var/model/hansung-p3.json')
    qualified_sha = json.loads(qualified.read_text())['artifact']['sha256'] if qualified.exists() else None

    record = {'status': 'VERIFIED' if parity_passed else 'FAILED',
              'checkpoint': str(checkpoint), 'checkpoint_sha256': digest(checkpoint),
              'artifact': str(artifact), 'artifact_sha256': digest(artifact),
              'artifact_bytes': artifact.stat().st_size,
              'matches_qualified_artifact_bytes': (digest(artifact) == qualified_sha) if qualified_sha else None,
              'ops': {'opset': arguments.opset,
                      'ir_version': onnx.load(str(artifact), load_external_data=False).ir_version,
                      'input_shape': list(contract.input_shape), 'source_classes': contract.source_count,
                      'raw_channels': contract.channels, 'contract_profile': contract.profile,
                      'class_mapping_version': model_contract.mapping_version(contract)},
              'exporter': {'torch': torch.__version__, 'onnx': onnx.__version__,
                           'ultralytics': str(__import__('ultralytics').__version__),
                           'export_seconds': round(float(export_seconds), 2)},
              'parity_method': {'definition': f'same letterbox and normalization; the same canonical decoder on '
                                              f'both raw tensors; match on IoU>={IOU_MATCH_MINIMUM} and same canonical '
                                              f'class and |conf delta|<={CONFIDENCE_TOLERANCE}',
                                'max_raw_tensor_delta': round(float(raw_delta), 6)},
              'parity_operating_threshold': {'threshold': OPERATING_THRESHOLD, 'frames': operating,
                                             'passed': parity_passed},
              'parity_stress_threshold': {'threshold': STRESS_THRESHOLD, 'frames': stress,
                                          'passed': stress_passed},
              'adapter_vs_head_argmax': {'note': 'boxes whose top source class is one this contract discards; the '
                                                 'canonical adapter re-scores the mapped subset, so these must be '
                                                 'handled identically by every runtime. This is why the NVIDIA parser '
                                                 'reuses the generated contract instead of the head argmax.',
                                         'discarded_top_class_boxes': int(head_discarded)},
              'frames': per_frame,
              'qualification': 'parity_passed' if (parity_passed and stress_passed) else 'parity_failed',
              'model_quality': 'NOT EVALUATED - requires a labeled dataset, mAP and temporal event metrics',
              'release_approval': 'NOT GRANTED - a signature and a passing evaluation report are still required',
              'lineage': {'dataset_version': arguments.dataset_version, 'code_commit': arguments.code_commit},
              'measured_at': int(time.time())}
    report_path = Path(arguments.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(record, indent=2))

    if arguments.mlflow_tracking_uri:
        from scripts.mlflow_rest import MlflowRest
        client = MlflowRest(arguments.mlflow_tracking_uri)
        experiment = client.experiment_id(arguments.mlflow_experiment)
        run_id = client.start_run(experiment, tags={'run_kind': 'onnx_export_parity',
                                                    'contract_profile': contract.profile,
                                                    'mapping_version': model_contract.mapping_version(contract),
                                                    'dataset_version': arguments.dataset_version or 'unspecified',
                                                    'code_commit': arguments.code_commit or 'unspecified'})
        logged = client.log_artifact(experiment, run_id, 'export', 'parity-report.json', report_path.read_bytes())
        client.finish_run(run_id, 'FINISHED' if qualified == 'parity_passed' else 'FAILED')
        record['mlflow'] = {'run_id': run_id, 'experiment': arguments.mlflow_experiment,
                            'artifact_verified': bool(logged),
                            'evidence_ref': f'mlflow:{run_id}/export/parity-report.json' if logged else None}
        report_path.write_text(json.dumps(record, indent=2))

    print(json.dumps({k: v for k, v in record.items()
                      if k not in ('parity_operating_threshold', 'parity_stress_threshold', 'frames')}, indent=2))
    print('operating-threshold parity: passed=%s' % parity_passed)
    print('stress-threshold parity:    passed=%s' % stress_passed)
    print('max raw tensor delta:       %s' % round(raw_delta, 6))


if __name__ == '__main__':
    main()
