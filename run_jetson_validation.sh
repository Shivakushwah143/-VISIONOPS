#!/usr/bin/env bash
# One command on a physical NVIDIA Jetson:
#
#     unzip visionops-jetson-validation.zip
#     cd visionops-jetson-validation
#     ./run_jetson_validation.sh
#
# It checks the host, installs ONLY application-level Python dependencies, and runs
# scripts/verify_physical_jetson.py. It deliberately never touches the NVIDIA stack:
# CUDA, cuDNN and TensorRT on a Jetson come from JetPack and are built against that
# device's L4T kernel. A pip "upgrade" of any of them is how a working JetPack gets
# broken, so a missing NVIDIA import is reported as a blocker instead.
#
# Override anything by exporting it first, e.g.
#     SUSTAINED_SECONDS=60 ITERATIONS=20 ./run_jetson_validation.sh
#     RTSP_URL=rtsp://192.168.1.20:8554/ppe ./run_jetson_validation.sh
set -euo pipefail

BUNDLE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$BUNDLE_ROOT"
CODE_ROOT="$BUNDLE_ROOT/code"
OUT="${OUT:-$BUNDLE_ROOT/evidence/jetson}"
PYTHON="${PYTHON:-python3}"
ITERATIONS="${ITERATIONS:-100}"
WARMUP="${WARMUP:-20}"
PRECISIONS="${PRECISIONS:-fp32,fp16}"
SUSTAINED_SECONDS="${SUSTAINED_SECONDS:-300}"
VIDEO_SECONDS="${VIDEO_SECONDS:-60}"
BACKEND="${BACKEND:-auto}"
RUNTIME="${RUNTIME:-auto}"
RTSP_URL="${RTSP_URL:-}"

log() { printf '\n=== %s ===\n' "$*"; }

# Exit codes match scripts/verify_physical_jetson.py so a caller can treat the wrapper
# and the module identically: 3 = this is not a qualified physical Jetson,
# 2 = it appears to be one but the environment is incomplete.
fail() { printf '\nBLOCKED: %s\n' "$2" >&2; exit "$1"; }
ENVIRONMENT_INCOMPLETE=2
NOT_A_PHYSICAL_JETSON=3

log "host"
uname -a || true
printf 'python: '; "$PYTHON" --version || fail "$ENVIRONMENT_INCOMPLETE" "python3 not found: install it from the JetPack image"

log "architecture"
ARCH="$(uname -m)"
if [ "$ARCH" != "aarch64" ]; then
  fail "$NOT_A_PHYSICAL_JETSON" "this host is $ARCH, not aarch64. A physical-Jetson qualification cannot run here.
The x86 TensorRT verification on the Tesla T4 is a separate, already-recorded result.
Ship this bundle to a real Jetson (Orin/Xavier) and run it there."
fi

log "Jetson identity"
JETSON_MARKER_MISSING=0
for marker in /etc/nv_tegra_release /etc/nv_boot_control.conf /opt/nvidia/jetson; do
  if [ -e "$marker" ]; then echo "found: $marker"; else echo "absent: $marker"; JETSON_MARKER_MISSING=$((JETSON_MARKER_MISSING + 1)); fi
done
if [ "$JETSON_MARKER_MISSING" -gt 0 ]; then
  echo "warning: some Tegra markers are absent; the detector decides, not this script"
fi
command -v tegrastats >/dev/null 2>&1 && tegrastats --interval 1000 2>/dev/null | head -1 || true

log "JetPack / CUDA / TensorRT (read-only; nothing will be installed or upgraded)"
if command -v dpkg-query >/dev/null 2>&1; then
  dpkg-query --show nvidia-jetpack 2>/dev/null || echo "nvidia-jetpack: not reported by dpkg"
fi
if command -v nvcc >/dev/null 2>&1; then nvcc --version | tail -2 || true; else echo "nvcc: absent"; fi
"$PYTHON" - <<'PY' || true
import json
probe = {}
for name in ('tensorrt', 'torch', 'onnxruntime', 'cv2', 'numpy', 'onnx', 'supervision'):
    try:
        module = __import__(name)
        probe[name] = getattr(module, '__version__', 'present')
    except Exception as error:
        probe[name] = f'MISSING ({type(error).__name__})'
print(json.dumps({'python_modules': probe}, indent=2))
PY

log "TensorRT guard"
"$PYTHON" - <<'PY' || fail "$ENVIRONMENT_INCOMPLETE" "TensorRT is not importable. Do NOT run 'pip install tensorrt' on a Jetson:
JetPack provides it against the L4T kernel. Check the JetPack installation instead."
import tensorrt
print('tensorrt', tensorrt.__version__)
PY

log "dependencies (application level only)"
# requirements-jetson.txt marks its sections; only the application names are installed.
REQUIREMENTS="$BUNDLE_ROOT/requirements/requirements-jetson.txt"
[ -f "$REQUIREMENTS" ] || fail "$ENVIRONMENT_INCOMPLETE" "requirements/requirements-jetson.txt missing from the bundle"
APP_REQUIREMENTS="$(mktemp)"
awk '/^# \[provided-by-jetpack\]/{exit} {print}' "$REQUIREMENTS" \
  | grep -E '^[A-Za-z0-9_.\-]+ *[<>=!~]=?' > "$APP_REQUIREMENTS" || true
echo "installing:"; cat "$APP_REQUIREMENTS"
MISSING=""
while IFS= read -r line; do
  [ -z "$line" ] && continue
  MODULE="$(printf '%s' "$line" | sed -E 's/^([A-Za-z0-9_.\-]+).*/\1/' | tr '-' '_' | tr '.' '_')"
  "$PYTHON" -c "import $MODULE" 2>/dev/null || MISSING="$MISSING $(printf '%s' "$line" | sed -E 's/^([A-Za-z0-9_.\-]+).*/\1/')"
done < "$APP_REQUIREMENTS"
if [ -n "$MISSING" ]; then
  echo "installing missing application packages:$MISSING"
  # --no-deps for the pinned names already present, and never an nvidia-* package:
  # the filter below is a second line of defence behind the awk above.
  if printf '%s' "$MISSING" | grep -qE 'nvidia|torch|onnxruntime'; then
    fail "$ENVIRONMENT_INCOMPLETE" "refusing to pip-install an NVIDIA or framework package:$MISSING
Install it from the JetPack / NVIDIA l4t index as described in requirements-jetson.txt."
  fi
  "$PYTHON" -m pip install --upgrade $MISSING || fail "$ENVIRONMENT_INCOMPLETE" "pip install failed; see requirements-jetson.txt"
fi
rm -f "$APP_REQUIREMENTS"

log "GStreamer / media"
command -v gst-launch-1.0 >/dev/null 2>&1 && gst-launch-1.0 --version || echo "gst-launch-1.0: absent"
command -v gst-inspect-1.0 >/dev/null 2>&1 && gst-inspect-1.0 --version || echo "gst-inspect-1.0: absent"
command -v ffmpeg >/dev/null 2>&1 && ffmpeg -version | head -1 || echo "ffmpeg: absent"
command -v deepstream-app >/dev/null 2>&1 && deepstream-app --version || echo "deepstream-app: absent (optional)"

log "qualification"
cd "$CODE_ROOT"
ARGS=( -m scripts.verify_physical_jetson
  --bundle-root "$BUNDLE_ROOT"
  --samples "$BUNDLE_ROOT/samples"
  --iterations "$ITERATIONS" --warmup "$WARMUP"
  --precisions "$PRECISIONS"
  --sustained-seconds "$SUSTAINED_SECONDS"
  --video-seconds "$VIDEO_SECONDS"
  --backend "$BACKEND" --runtime "$RUNTIME"
  --out "$OUT" )
[ -f "$BUNDLE_ROOT/samples/ppe.mp4" ] && ARGS+=( --video "$BUNDLE_ROOT/samples/ppe.mp4" )
[ -f "$BUNDLE_ROOT/model/hansung-p3.json" ] && ARGS+=( --contract-record "$BUNDLE_ROOT/model/hansung-p3.json" )
[ -f "$BUNDLE_ROOT/model/hansung-p3-fp16.onnx" ] && ARGS+=( --fp16-onnx "$BUNDLE_ROOT/model/hansung-p3-fp16.onnx" )
[ -n "$RTSP_URL" ] && ARGS+=( --rtsp-url "$RTSP_URL" --test-rtsp-reconnect )

set +e
"$PYTHON" "${ARGS[@]}"
STATUS=$?
set -e

log "evidence"
ls -la "$OUT" || true
ZIP="$(dirname "$OUT")/visionops-jetson-evidence.zip"
if [ -f "$ZIP" ]; then
  echo "return this file: $ZIP"
  command -v sha256sum >/dev/null 2>&1 && sha256sum "$ZIP" || true
else
  echo "no evidence ZIP was produced; the phases recorded what happened in $OUT/phases.json"
fi

case "$STATUS" in
  0) echo "QUALIFICATION_COMPLETE" ;;
  2) echo "ENVIRONMENT_INCOMPLETE" ;;
  3) echo "BLOCKED_NOT_PHYSICAL_JETSON" ;;
  4) echo "QUALIFICATION_PARTIAL — see $OUT/phases.json for the failing phase" ;;
  5) echo "PARTIAL_JETSON_ENVIRONMENT — rerun with --allow-partial on the module if intended" ;;
  *) echo "unexpected exit status $STATUS" ;;
esac
exit "$STATUS"
