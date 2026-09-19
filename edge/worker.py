"""Signed runtime bundle entrypoint. Candidate warmup produces no safety events.

Reads the released manifest and the signed per-device config, asserts that the
artifact's canonical class mapping matches the one the release pinned, then starts
one Pipeline per enabled camera. Video backend, temporal analyzers and thresholds
come from the signed config, never from constants here.
"""
import argparse
import json
import threading
import time
from pathlib import Path

import cv2

from edge.hardware_telemetry import HardwareTelemetry
from edge.pipeline import Detector, Pipeline, create_source
from edge.state import State
from shared.model_contract import mapping_version

parser = argparse.ArgumentParser()
parser.add_argument('--slot', required=True)
parser.add_argument('--state', required=True)
parser.add_argument('--status', required=True)
arguments = parser.parse_args()

slot = Path(arguments.slot)
manifest = json.loads((slot / 'manifest.json').read_text())
config = json.loads((slot / 'config.json').read_text())
settings = config.get('settings', config)
state = State(arguments.state)
telemetry = HardwareTelemetry()

# The release pins the mapping; a mismatch is a hard failure, not a warning.
detector = Detector(slot / 'model', manifest['model_sha256'], class_mapping=manifest.get('class_mapping'))
released_mapping_version = manifest.get('class_mapping_version')
if released_mapping_version and mapping_version(detector.contract) != released_mapping_version:
    raise ValueError('class_mapping_version_mismatch')

frame = cv2.imread(str(slot / 'bundle' / 'warmup.jpg'))
if frame is None:
    raise ValueError('warmup_image_invalid')
detector.infer(frame)

started = set()
pipelines = []
while True:
    # Activation commit controls event production; candidates only warm up.
    actual = state.get('actual')
    committed = actual and actual['release_id'] == manifest['release_id'] and not state.get('activation')
    if committed:
        for camera in settings['cameras']:
            if not camera['enabled'] or camera['camera_id'] in started:
                continue
            metadata = {'camera_id': camera['camera_id'], 'release_id': manifest['release_id'],
                        'model_version_id': json.loads((slot / 'bundle' / 'version.json').read_text())['model_version_id'],
                        'config_version_id': actual['config_version_id']}
            source = create_source(camera['locator'], camera['loop'], backend=settings.get('video_backend'))
            pipeline = Pipeline(detector, source, metadata, state, settings=settings, telemetry=telemetry)
            threading.Thread(target=pipeline.run, daemon=True).start()
            pipelines.append(pipeline)
            started.add(camera['camera_id'])
    status = {'ready': not pipelines or all(p.source.status == 'running' for p in pipelines),
              'video_backend': settings.get('video_backend') or 'environment',
              'class_mapping_version': released_mapping_version,
              'contract': detector.contract.as_dict(),
              'runtime': detector.runtime_name,
              'hardware': telemetry.sample(),
              'cameras': [p.status() for p in pipelines]}
    target = Path(arguments.status)
    temporary = target.with_suffix('.partial')
    temporary.write_text(json.dumps(status))
    temporary.replace(target)
    time.sleep(2)
