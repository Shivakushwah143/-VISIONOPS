# Local RTSP development environment

Goal: exercise the full `connect → decode → frames → inference → tracking → events →
disconnect → reconnect → recovery` journey without a physical camera.

```text
sample MP4 ──scripts/publish_rtsp.py──▶ MediaMTX ──rtsp://localhost:8554/live──▶ edge worker
```

## Status

| Part | Status |
| --- | --- |
| Reconnect/backoff contract, stream health counters, clean shutdown | **VERIFIED** against an unreachable endpoint (`docs/evidence/edge-platform-runtime.json`) |
| OpenCV/FFmpeg RTSP-consumer code path | **VERIFIED** for file sources; RTSP frames not received (no server) |
| `infrastructure/compose.rtsp.yaml`, `scripts/publish_rtsp.py` | **IMPLEMENTED — NOT VERIFIED**: no RTSP server and no Docker container was started here |
| GStreamer RTSP path | **IMPLEMENTED — NOT VERIFIED**: PyGObject/GStreamer is not installed |

## 1. Start a local RTSP server

```bash
docker compose -f infrastructure/compose.rtsp.yaml up -d mediamtx
```

The service publishes port `8554` on loopback only. `bluenviron/mediamtx:1.11.3` was
**not pulled** in the development environment; no image digest is qualified.

## 2. Publish a sample video as a looping stream

```bash
.venv/bin/python -m scripts.publish_rtsp --video var/media/ppe-2.mp4 \
    --url rtsp://localhost:8554/live
```

Uses PyAV (already a dependency) with `libx264` and `zerolatency`, so no host FFmpeg
binary is needed. Any equivalent publisher works, for example:

```bash
ffmpeg -re -stream_loop -1 -i var/media/ppe-2.mp4 -c:v libx264 -preset veryfast \
    -tune zerolatency -f rtsp -rtsp_transport tcp rtsp://localhost:8554/live
```

## 3. Point a device video source at it

Create the source through the API/console with `kind=rtsp` and locator
`rtsp://localhost:8554/live`. RTSP locators are rejected if they contain credentials.

Backend selection is environment-driven, never a hardcoded path:

```bash
VISIONOPS_VIDEO_BACKEND=opencv      # default
VISIONOPS_VIDEO_BACKEND=gstreamer   # fails fast if the bindings are absent
VISIONOPS_VIDEO_BACKEND=auto        # GStreamer for RTSP when available, otherwise OpenCV
```

## 4. What the worker reports

`Pipeline.status()` (written to the worker status file and forwarded in heartbeats):

| Field | Meaning |
| --- | --- |
| `rtsp_connected` | status is running *and* a frame was decoded within `stale_frame_ms` |
| `rtsp_reconnect_total` | bounded reconnection attempts for this source |
| `input_fps`, `decode_fps` | measured decode rate over a 5 s window (declared rate for files) |
| `processed_fps` | inference frames per second actually achieved |
| `dropped_frames_total` | frames dropped by the bounded queue or by age/throttle |
| `queue_depth`, `queue_capacity` | latest-frame queue occupancy (default 2) |
| `stream_age_seconds`, `last_frame_at` | freshness of the last decoded frame |
| `decode_failures_total` | sessions that opened but never decoded |

`input_fps`/`decode_fps` are *measured*: for a live stream there is no trustworthy
declared rate, so the field reports the measured rate rather than a guess.

## 5. Failure and recovery behaviour

* A disconnect never terminates the worker: the source enters `reconnecting` with
  exponential backoff from 1 s to 30 s, and resets the backoff once a session actually
  decodes frames.
* An unreachable endpoint produces no frames and no events; `dropped_frames_total`
  stays at its previous value and nothing is fabricated.
* A configuration error (an unavailable backend) is *not* retried forever: the
  constructor raises `VideoBackendUnavailable`, so a misconfigured camera is reported
  unhealthy instead of spinning.
* The pipeline also exits if its decoder thread dies unexpectedly, so a camera cannot
  silently stop producing frames while appearing alive.

## 6. Reproduce the verified parts today

```bash
.venv/bin/python -m scripts.verify_edge_platform     # video + RTSP resilience sections
```

No RTSP server is required for that command: it verifies the bounded reconnect
contract against a socket that accepts and immediately closes, and records
`media_mtx_journey: NOT RUN - no RTSP server available`.
