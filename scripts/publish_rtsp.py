"""Publish a local video file as a looping RTSP stream for local development.

Uses PyAV (already a project dependency) so no host FFmpeg binary is required:
decode the file, re-encode with libx264 at a low latency and mux to RTSP against a
local RTSP server such as MediaMTX.

    python -m scripts.publish_rtsp --video var/media/ppe-2.mp4 --url rtsp://localhost:8554/live

IMPLEMENTED - NOT RUNTIME VERIFIED: no RTSP server was available in the
development environment, so no session was established from this script.
"""
import argparse
import sys
import time
from fractions import Fraction
from pathlib import Path

def _bit_rate(value):
    """Parse '2M'/'800k'/'1500000' into bits per second."""
    text = str(value).strip().lower()
    units = {'k': 1000, 'm': 1000 ** 2, 'g': 1000 ** 3}
    if text and text[-1] in units:
        return int(float(text[:-1]) * units[text[-1]])
    return int(float(text))


parser = argparse.ArgumentParser()
parser.add_argument('--video', required=True)
parser.add_argument('--url', default='rtsp://localhost:8554/live')
parser.add_argument('--fps', type=int, default=0, help='0 keeps the source rate')
parser.add_argument('--bitrate', default='2M')
parser.add_argument('--seconds', type=float, default=0, help='0 streams until interrupted')
parser.add_argument('--transport', default='tcp')
arguments = parser.parse_args()

video = Path(arguments.video)
if not video.is_file():
    raise SystemExit('video file not found: ' + str(video))

try:
    import av
except ImportError:
    raise SystemExit('PyAV is required: python -m scripts.publish_rtsp needs the av package')

source = av.open(str(video))
stream = source.streams.video[0]
stream.thread_type = 'AUTO'
source_rate = stream.average_rate or Fraction(25, 1)
rate = Fraction(arguments.fps, 1) if arguments.fps else source_rate
width, height = stream.codec_context.width, stream.codec_context.height

# Client mode: ANNOUNCE/push to the RTSP server, not listen-as-server.
output = av.open(arguments.url, mode='w', format='rtsp', options={'rtsp_transport': arguments.transport})
target = output.add_stream('libx264', rate=rate)
target.width, target.height = width, height
target.pix_fmt = 'yuv420p'
target.bit_rate = _bit_rate(arguments.bitrate)
target.options = {'preset': 'veryfast', 'tune': 'zerolatency', 'g': '10'}

started = time.monotonic()
frames = 0
try:
    while True:
        for frame in source.decode(stream):
            for packet in target.encode(frame):
                output.mux(packet)
            frames += 1
            if arguments.seconds and time.monotonic() - started >= arguments.seconds:
                raise KeyboardInterrupt
        source.seek(0)
except KeyboardInterrupt:
    pass
finally:
    try:
        for packet in target.encode(None):
            output.mux(packet)
    except Exception:  # noqa: BLE001 - flushing a torn-down socket is not an error here
        pass
    output.close()
    source.close()
print(f'published {frames} frames to {arguments.url} for {time.monotonic() - started:.1f}s', file=sys.stderr)
