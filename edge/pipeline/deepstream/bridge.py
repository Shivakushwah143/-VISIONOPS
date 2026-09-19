"""NvDsBatchMeta adapter. Imported only inside a qualified DeepStream process.

Class ids arrive already canonicalised by the custom parser built from
`visionops_contract.h`; this module validates against the same generated contract
instead of the previously hardcoded `(0, 1, 2)` literal, so a contract change
cannot leave the bridge filtering the wrong ids.
"""
import json
from pathlib import Path

import pyds

_CONTRACT_PATH = Path(__file__).resolve().parent / 'visionops_contract.json'
CONTRACT = json.loads(_CONTRACT_PATH.read_text()) if _CONTRACT_PATH.exists() else {
    'canonical_class_count': 3, 'profile': 'unknown', 'mapping_version': 'unknown'}
CANONICAL_CLASS_COUNT = int(CONTRACT['canonical_class_count'])


def detections(buffer, coordinate_width=None, coordinate_height=None):
    batch = pyds.gst_buffer_get_nvds_batch_meta(hash(buffer))
    node = batch.frame_meta_list
    while node:
        frame = pyds.NvDsFrameMeta.cast(node.data)
        objects = []
        item = frame.obj_meta_list
        while item:
            obj = pyds.NvDsObjectMeta.cast(item.data)
            rect = obj.rect_params
            if obj.class_id < CANONICAL_CLASS_COUNT:
                width = float(coordinate_width or frame.source_frame_width)
                height = float(coordinate_height or frame.source_frame_height)
                objects.append({'class_id': int(obj.class_id), 'track_id': str(obj.object_id),
                                'confidence': float(obj.confidence),
                                'bbox': [rect.left / width, rect.top / height,
                                         (rect.left + rect.width) / width,
                                         (rect.top + rect.height) / height]})
            try:
                item = item.next
            except StopIteration:
                break
        yield {'source_id': frame.source_id, 'frame_sequence': frame.frame_num,
               'pts': frame.buf_pts, 'detections': objects,
               'contract_profile': CONTRACT.get('profile'),
               'mapping_version': CONTRACT.get('mapping_version')}
        try:
            node = node.next
        except StopIteration:
            break
