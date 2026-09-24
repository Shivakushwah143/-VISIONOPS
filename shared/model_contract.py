"""Canonical PPE model contract shared by every inference runtime.

One class mapping, one decode, one threshold interpretation. The CPU ONNX
adapter (`edge/pipeline.py`) and the NVIDIA/DeepStream parser
(`edge/pipeline/deepstream/`) both consume this module so the two paths cannot
silently diverge.

The qualified Hansung model is a real 10-class YOLOv8 PPE detector whose raw
tensor is `[1, 14, 8400]` (4 box values + 10 source class scores). Only three
source classes are canonicalised:

    source 5  Person      -> canonical 0  person
    source 0  Hardhat     -> canonical 1  helmet
    source 2  NO-Hardhat  -> canonical 2  no_helmet

Everything else in the source head is filtered out, never remapped onto a
canonical class. A 3-class export is also supported so the synthetic engineering
fixture and any future subset export keep working.

Canonical object for every runtime: (xyxy pixels, confidence, canonical class id).
"""
import ast, hashlib, json
import numpy as np

CANONICAL_CLASSES = {0: 'person', 1: 'helmet', 2: 'no_helmet'}
# Publishers name the same concept differently; validation compares normalised
# labels from the model's own metadata against these synonym sets.
CANONICAL_LABELS = {0: {'person', 'people'}, 1: {'helmet', 'hardhat'}, 2: {'nohelmet', 'nohardhat'}}
DEFAULT_INPUT = (640, 640)
DEFAULT_SCORE_THRESHOLD = 0.35
DEFAULT_NMS_IOU = 0.5


def normalize_label(value):
    return ''.join(ch for ch in str(value).lower() if ch.isalnum())


class ModelContract:
    """Immutable source->canonical mapping with the semantics both runtimes share."""

    def __init__(self, profile, source_index_to_canonical, source_classes=None,
                 input_shape=DEFAULT_INPUT, score_threshold=DEFAULT_SCORE_THRESHOLD,
                 nms_iou=DEFAULT_NMS_IOU, source_count=None):
        self.profile = profile
        self.mapping = {int(k): int(v) for k, v in source_index_to_canonical.items()}
        self.source_classes = {int(k): v for k, v in (source_classes or {}).items()}
        self.input_shape = tuple(input_shape)
        self.score_threshold = float(score_threshold)
        self.nms_iou = float(nms_iou)
        if set(self.mapping.values()) != set(CANONICAL_CLASSES):
            raise ValueError('class_mapping_incomplete')
        if len(set(self.mapping)) != len(self.mapping):
            raise ValueError('class_mapping_duplicate_source')
        self.sources = tuple(sorted(self.mapping))
        # The *published* score-head width. It is not max(mapped index)+1: the
        # qualified Hansung head has 10 classes while only 3 are canonicalised, and
        # conflating the two is exactly how a 14-channel model gets parsed as a
        # 7-channel one. Defaults to the smallest self-consistent value.
        self.source_count = int(source_count) if source_count else max(self.sources) + 1
        if self.source_count <= max(self.sources):
            raise ValueError('class_mapping_out_of_range')
        if self.source_count < len(self.mapping):
            raise ValueError('source_count_below_mapping')

    def as_dict(self):
        return {'profile': self.profile,
                'source_index_to_canonical': {str(k): v for k, v in sorted(self.mapping.items())},
                'source_classes': {str(k): v for k, v in sorted(self.source_classes.items())},
                'input_shape': list(self.input_shape),
                'source_count': self.source_count,
                'raw_channels': self.channels,
                'score_threshold': self.score_threshold,
                'nms_iou': self.nms_iou,
                'canonical_classes': {str(k): v for k, v in CANONICAL_CLASSES.items()}}

    @classmethod
    def from_dict(cls, raw):
        mapping = raw.get('source_index_to_canonical', raw.get('source_index_to_canonical_id'))
        count = raw.get('source_count')
        if count is None and raw.get('class_scores') is not None:
            count = raw['class_scores']
        return cls(raw['profile'], mapping,
                   raw.get('source_classes') or raw.get('source_class_map'),
                   raw.get('input_shape', DEFAULT_INPUT),
                   raw.get('score_threshold', DEFAULT_SCORE_THRESHOLD),
                   raw.get('nms_iou', DEFAULT_NMS_IOU), count)

    def validate_metadata(self, names):
        """Reject a model whose declared labels contradict this mapping."""
        if not names:
            raise ValueError('model_missing_class_metadata')
        declared = {int(k): v for k, v in names.items()} if isinstance(names, dict) else dict(names)
        for source, canonical in self.mapping.items():
            label = declared.get(source)
            if label is None or normalize_label(label) not in CANONICAL_LABELS[canonical]:
                raise ValueError('taxonomy_metadata_mismatch')
        return True

    @property
    def channels(self):
        """Raw tensor channel count: 4 box values + one score column per source class."""
        return 4 + self.source_count

    @property
    def canonical_count(self):
        return len(CANONICAL_CLASSES)

    def cpp_constants(self):
        """Values the compiled DeepStream parser must embed.

        `VISIONOPS_CANONICAL_CLASS_COUNT` is what the parser may emit;
        `VISIONOPS_SOURCE_COUNT` is the width of the score head it must read.
        """
        return {'VISIONOPS_SOURCE_COUNT': self.source_count,
                'VISIONOPS_CANONICAL_CLASS_COUNT': self.canonical_count,
                'VISIONOPS_RAW_CHANNELS': self.channels,
                'VISIONOPS_BOX_VALUES': 4,
                'VISIONOPS_MAPPED_SOURCE_INDEX': list(self.sources),
                'VISIONOPS_MAPPED_CANONICAL_ID': [self.mapping[s] for s in self.sources],
                'VISIONOPS_SCORE_THRESHOLD': self.score_threshold,
                'VISIONOPS_NMS_IOU': self.nms_iou}

    def nvinfer_classes(self):
        """`num-detected-classes` for nvinfer.txt: the full source score head.

        The raw network head has `source_count` classes; the custom parser maps
        them onto the canonical ids, which are a subset of that range.
        """
        return self.source_count

    def detected_geometry(self, raw):
        """Validate a raw tensor against this contract; return (channels, anchors)."""
        shape = tuple(int(d) for d in np.asarray(raw).shape)
        if len(shape) != 3 or shape[0] != 1:
            raise ValueError('unsupported_output_shape')
        _, first, second = shape
        channels, anchors = (first, second) if first < second else (second, first)
        if channels != self.channels:
            raise ValueError('contract_channel_mismatch')
        return channels, anchors

    @classmethod
    def detect(cls, names):
        """Resolve the single profile whose mapping matches the model's own labels.

        Auto-detection removes the class-mapping CLI argument as a source of
        silent divergence between the CPU adapter and the NVIDIA parser.
        """
        matches = []
        for candidate in PROFILES.values():
            try:
                candidate.validate_metadata(names)
            except (ValueError, KeyError, TypeError):
                continue
            matches.append(candidate)
        if not matches:
            raise ValueError('unrecognised_model_contract_profile')
        if len(matches) > 1:
            raise ValueError('ambiguous_model_contract_profile')
        return matches[0]


# The qualified Hansung artifact (var/model/hansung-p3.onnx, output [1, 14, 8400]).
HANSUNG_PPE_10 = ModelContract(
    'hansung_ppe_yolov8n_10', {5: 0, 0: 1, 2: 2},
    source_classes={0: 'Hardhat', 1: 'Mask', 2: 'NO-Hardhat', 3: 'NO-Mask', 4: 'NO-Safety Vest',
                    5: 'Person', 6: 'Safety Cone', 7: 'Safety Vest', 8: 'machinery', 9: 'vehicle'},
    source_count=10)
# Legacy 3-class export and the synthetic engineering fixture.
SUBSET_3 = ModelContract('subset_3_class', {0: 0, 1: 1, 2: 2},
                         source_classes={0: 'person', 1: 'helmet', 2: 'no_helmet'}, source_count=3)
PROFILES = {p.profile: p for p in (HANSUNG_PPE_10, SUBSET_3)}
# Historical alias: the HuggingFace baseline manifest records a different source
# head. It is kept as a named profile instead of an undocumented CLI argument.
EXTERNAL_BASELINE_80 = ModelContract('external_baseline_yolov8m', {11: 0, 3: 1, 8: 2},
                                     source_classes={11: 'Person', 3: 'Hardhat', 8: 'NO-Hardhat'},
                                     source_count=80)
PROFILES[EXTERNAL_BASELINE_80.profile] = EXTERNAL_BASELINE_80


def known(name):
    """True when `name` is a declared contract profile.

    `backend/app/lifecycle.py` uses this to reject an artifact that pins an
    unknown profile before any rows are written; it complements `profile()`,
    which raises instead of returning a boolean.
    """
    return name in PROFILES


def profile(name):
    if name not in PROFILES:
        raise ValueError('unknown_model_contract_profile')
    return PROFILES[name]


def mapping_version(contract):
    """Stable identity for a class mapping, recorded in release manifests.

    Two artifacts that disagree on source indices, canonical ids, thresholds or
    raw head width can never share a mapping version, so a release cannot be
    activated against a runtime built for a different interpretation.
    """
    encoded = json.dumps(contract.cpp_constants(), sort_keys=True, separators=(',', ':')).encode()
    return f'{contract.profile}@{hashlib.sha256(encoded).hexdigest()[:16]}'


def parse_names(value):
    """Model metadata stores `names` as a Python or JSON literal dict."""
    if isinstance(value, dict):
        return value
    try:
        parsed = ast.literal_eval(value)
    except (ValueError, SyntaxError):
        parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError('invalid_class_metadata')
    return parsed


def letterbox(frame, size=None):
    """Return (padded_rgb_chw_float, geometry) for the contract input shape."""
    height, width = frame.shape[:2]
    target_w, target_h = size or DEFAULT_INPUT
    scale = min(target_w / width, target_h / height)
    inner_w, inner_h = round(width * scale), round(height * scale)
    left, top = (target_w - inner_w) // 2, (target_h - inner_h) // 2
    canvas = np.full((target_h, target_w, 3), 114, np.uint8)
    canvas[top:top + inner_h, left:left + inner_w] = _resize(frame, (inner_w, inner_h))
    tensor = canvas[:, :, ::-1].transpose(2, 0, 1)[None].astype(np.float32) / 255.0
    geometry = {'scale': scale, 'pad_left': left, 'pad_top': top,
                'frame_width': width, 'frame_height': height,
                'input_width': target_w, 'input_height': target_h}
    return np.ascontiguousarray(tensor), geometry


def _resize(frame, shape):
    import cv2
    return cv2.resize(frame, shape)


def decode(raw, contract, score_threshold=None, geometry=None):
    """Raw model tensor -> canonical (xyxy pixels, confidence, class id) arrays.

    Identical for ONNX Runtime CPU/CUDA, TensorRT and the DeepStream parser:
    transpose to [anchors, 4 + sources], score only the mapped source columns,
    apply the letterbox inverse, clamp to the frame and drop degenerate boxes.
    """
    predictions = np.squeeze(np.asarray(raw))
    if predictions.ndim != 2:
        raise ValueError('unsupported_output_shape')
    if predictions.shape[0] < predictions.shape[1]:
        predictions = predictions.T
    scores = predictions[:, 4:]
    if scores.shape[1] <= max(contract.sources):
        raise ValueError('unsupported_output_shape')
    threshold = contract.score_threshold if score_threshold is None else float(score_threshold)
    subset = scores[:, list(contract.sources)]
    chosen = subset.argmax(1)
    confidence = subset.max(1)
    geometry = geometry or {'scale': 1.0, 'pad_left': 0, 'pad_top': 0,
                            'frame_width': contract.input_shape[0],
                            'frame_height': contract.input_shape[1]}
    scale, left, top = geometry['scale'], geometry['pad_left'], geometry['pad_top']
    width, height = geometry['frame_width'], geometry['frame_height']
    boxes, confidences, classes = [], [], []
    for prediction, index, score in zip(predictions, chosen, confidence):
        if not np.isfinite(score) or score < threshold:
            continue
        cx, cy, bw, bh = prediction[:4]
        if not all(np.isfinite(v) for v in (cx, cy, bw, bh)) or bw <= 0 or bh <= 0:
            continue
        box = np.array([(cx - bw / 2 - left) / scale, (cy - bh / 2 - top) / scale,
                        (cx + bw / 2 - left) / scale, (cy + bh / 2 - top) / scale])
        box[[0, 2]] = np.clip(box[[0, 2]], 0, width)
        box[[1, 3]] = np.clip(box[[1, 3]], 0, height)
        if box[2] <= box[0] or box[3] <= box[1]:
            continue
        boxes.append(box)
        confidences.append(float(score))
        classes.append(contract.mapping[contract.sources[int(index)]])
    if not boxes:
        return (np.zeros((0, 4), np.float32), np.zeros(0, np.float32), np.zeros(0, np.int64))
    return (np.array(boxes, np.float32), np.array(confidences, np.float32),
            np.array(classes, np.int64))


def nms(xyxy, confidence, class_id, threshold=None, contract=None):
    """Class-aware IoU NMS with the same semantics as supervision's default."""
    iou_threshold = threshold if threshold is not None else (
        contract.nms_iou if contract else DEFAULT_NMS_IOU)
    keep = []
    for cls in np.unique(class_id):
        indices = np.where(class_id == cls)[0]
        order = indices[np.argsort(-confidence[indices])]
        boxes = xyxy[order]
        while len(order):
            current = order[0]
            keep.append(int(current))
            if len(order) == 1:
                break
            rest = boxes[1:]
            area_current = ((boxes[0, 2] - boxes[0, 0]) * (boxes[0, 3] - boxes[0, 1]))
            inter_x = np.clip(np.minimum(boxes[0, 2], rest[:, 2]) - np.maximum(boxes[0, 0], rest[:, 0]), 0, None)
            inter_y = np.clip(np.minimum(boxes[0, 3], rest[:, 3]) - np.maximum(boxes[0, 1], rest[:, 1]), 0, None)
            intersection = inter_x * inter_y
            union = area_current + (rest[:, 2] - rest[:, 0]) * (rest[:, 3] - rest[:, 1]) - intersection
            iou = intersection / np.maximum(union, 1e-12)
            keep_mask = iou <= iou_threshold
            order = order[1:][keep_mask]
            boxes = rest[keep_mask]
    return np.array(sorted(keep), np.int64)


def canonical_detections(raw, contract, score_threshold=None, geometry=None, nms_iou=None):
    """Decode + NMS. Returns (xyxy, confidence, class_id) ready for any runtime."""
    boxes, confidence, class_id = decode(raw, contract, score_threshold, geometry)
    if not len(boxes):
        return boxes, confidence, class_id
    keep = nms(boxes, confidence, class_id, nms_iou, contract)
    return boxes[keep], confidence[keep], class_id[keep]
