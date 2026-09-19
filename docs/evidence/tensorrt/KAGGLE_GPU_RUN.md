# Kaggle GPU fallback run

Use this only if the Colab notebook (`colab_gpu_verify.ipynb`) cannot install a working
TensorRT for the session. The verification code is identical — this page changes the
*host*, not the harness. Nothing here is a substitute for a real GPU: if TensorRT cannot
be made to work, the harness exits `BLOCKED_BY_NVIDIA_HARDWARE` (code 3) and that exit is
the correct result.

Budget the session at roughly 15 minutes. `nvidia-smi`, `python --version` and
`pip show torch` are inspected **before** anything is installed, so a version conflict is
diagnosed rather than guessed at.

## 1. Create the notebook and attach the bundle

1. `kaggle.com` → **Code** → **New Notebook**.
2. Right sidebar → **Settings**:
   * **Accelerator** → `GPU T4 x2` (a P100 also works; FP16 needs a T4-class card or better).
   * **Internet** → **On** (needed for `pip`).
3. Right sidebar → **Add Input** → **Upload** → **New Dataset** → upload
   `visionops-gpu-bundle.zip`, name it `visionops-gpu-bundle`, **Create**.
   Kaggle extracts archives automatically, so `MANIFEST.json`, `code/`, `model/` and
   `samples/` appear under a read-only input path.
4. Attach that dataset to the notebook (the Add Input panel shows it once created).

Kaggle's input directory is read-only and its name is derived from the dataset slug, so
every path below is resolved in code rather than hardcoded.

## 2. Cell 1 — GPU or stop

```python
import subprocess
print(subprocess.run(["nvidia-smi", "--query-gpu=name,driver_version,memory.total,compute_cap",
                      "--format=csv,noheader"], capture_output=True, text=True).stdout or "NO OUTPUT")
!python --version
!pip show torch | head -5
```

`nvidia-smi` must print a real GPU line. If it does not, the accelerator is not enabled.

## 3. Cell 2 — locate the extracted bundle

```python
import json, pathlib
roots = [p for p in pathlib.Path("/kaggle/input").glob("**/MANIFEST.json")]
assert roots, "bundle not attached: add the visionops-gpu-bundle dataset as an input"
BUNDLE = roots[0].parent
manifest = json.loads((BUNDLE / "MANIFEST.json").read_text())
CODE = BUNDLE / "code"
ONNX = BUNDLE / manifest["onnx"]["archive_path"]
CONTRACT = BUNDLE / manifest["contract_record"]["archive_path"]
SAMPLES = BUNDLE / "samples"
print("bundle    ", BUNDLE)
print("code      ", CODE, CODE.is_dir())
print("onnx      ", ONNX, ONNX.stat().st_size)
print("contract  ", CONTRACT, CONTRACT.is_file())
print("samples   ", SAMPLES, len(list(SAMPLES.glob("*.jpg"))))
print("onnx sha256 (bundle manifest)", manifest["onnx"]["sha256"])
```

## 4. Cell 3 — dependencies

```python
import subprocess, sys, pathlib
requirements = pathlib.Path(CODE) / "requirements-gpu.txt"
args = [sys.executable, "-m", "pip", "install", "-q", "-r", str(requirements)] if requirements.is_file() \
    else [sys.executable, "-m", "pip", "install", "-q", "onnx", "supervision",
          "opencv-python-headless", "tensorrt"]
print("$", " ".join(args))
subprocess.run(args, text=True)
```

`requirements-gpu.txt` deliberately does **not** list `torch` (Kaggle ships a
CUDA-matched build) or `trtexec` (not in the pip wheels — the harness uses the TensorRT
Python API instead). It does list `nvidia-modelopt`, because TensorRT 11 removed
`BuilderFlag.FP16`: on this runtime a true FP16 engine can only be built from a
strongly-typed ONNX graph, which `scripts.convert_fp16_onnx.py` produces and repairs.

If the TensorRT wheel does not match the image's CUDA, record the pip error, then make
**one** alternate attempt and stop. Two reasonable alternates:

```python
# A) TensorRT version matched to the image's CUDA major version
!pip install -q "tensorrt>=10.0,<11"
# B) the image's own NVIDIA CUDA/TensorRT repositories instead of PyPI
!pip install -q nvidia-tensorrt
```

Then confirm a builder actually exists — a successful `import` is not enough:

```python
import tensorrt as trt
builder = trt.Builder(trt.Logger(trt.Logger.WARNING))
print("tensorrt", trt.__version__, "| builder:", builder is not None)
import torch; print("cuda available:", torch.cuda.is_available())
```

## 5. Cell 4 — run the same verification harness

Two runs, because FP32 and true FP16 come from different graphs. The Colab notebook does
both automatically; on Kaggle the equivalent is:

```python
import subprocess, sys
out_dir = pathlib.Path("/kaggle/working/docs/evidence/tensorrt")
RUN = ["--contract-record", str(CONTRACT), "--samples-dir", str(SAMPLES),
       "--warmup", "20", "--iterations", "100"]

def verify(extra, label):
    command = [sys.executable, "-m", "scripts.verify_tensorrt_gpu", *extra, *RUN]
    print("$", " ".join(command), flush=True)
    done = subprocess.run(command, cwd=str(CODE), text=True)
    print(f"[{label}] exit code {done.returncode} (0 = verified, 3 = blocked, 4 = ran but failed)")
    return done.returncode

# 1) FP32: the qualified ONNX, unchanged
verify(["--onnx", str(ONNX), "--precisions", "fp32", "--out", str(out_dir / "fp32")], "fp32")

# 2) true mixed FP16: the graph carries the precision on TensorRT 11+
fp16_onnx = CODE / "var/model/hansung-p3-fp16.onnx"
convert = subprocess.run([sys.executable, "-m", "scripts.convert_fp16_onnx",
                          "--onnx", str(ONNX), "--out", str(fp16_onnx)],
                         cwd=str(CODE), text=True)
print("[convert] exit code", convert.returncode, "(0 = converted, 2 = tool unavailable)")
if convert.returncode == 0:
    verify(["--onnx", str(ONNX), "--fp16-onnx", str(fp16_onnx), "--precisions", "fp16",
            "--out", str(out_dir / "true_fp16")], "true-fp16")
```

Engines are built in a temporary directory and deleted after measuring: they are specific
to the GPU, driver and TensorRT version that built them, so the evidence references them
by SHA-256 instead. Add `--keep-engines` only if you want to inspect their dtype/shape
metadata, and never commit them.

## 6. Cell 5 — read the result, then package it

```python
import json, pathlib, shutil
summary = out_dir / "tensorrt-verification.json"
if summary.exists():
    data = json.loads(summary.read_text())
    print("STATUS:", data["status"])
    for row in data["benchmark_table"]:
        print(" | ".join(str(row.get(k)) for k in
                         ("runtime", "precision", "p50_ms", "p95_ms", "mean_ms", "throughput_fps_model_only")))
    for precision, result in data["precisions"].items():
        parity = result.get("parity")
        if parity:
            print(precision, "parity passed:", parity["passed"], "| matched", parity["matched"],
                  "of", parity["reference_detections"], "| min IoU", parity["min_iou"])
else:
    print((out_dir / "blocked.json").read_text() if (out_dir / "blocked.json").exists()
          else "no evidence written — inspect the previous cell")

shutil.make_archive("/kaggle/working/visionops-tensorrt-evidence", "zip", str(out_dir))
print(sorted(p.name for p in out_dir.iterdir()))
```

Download `visionops-tensorrt-evidence.zip` from the notebook's **Output** panel (it is
the artefact under `/kaggle/working`). It contains the two run directories `fp32/` and
`true_fp16/`, each with `environment.json`, `benchmark.json`, `parity_<precision>.json`,
`contract-semantics.json`, `tensorrt-verification.json`, `status-patch.json`,
`GPU_RUN_RESULT.md` and `samples.json`, plus the conversion record,
`mixed_fp16_graph.json` and `fp32_onnx_vs_mixed_fp16_onnx.json` from the FP16 path, and
`trtexec.log` when the host had `trtexec`.

## 7. What to check before believing a number

* `nvidia-smi` printed a real GPU and `torch.cuda.is_available()` is `True`.
* `environment.json` has a non-null `tensorrt` version and a `driver`/`cuda`/`gpu_names` entry.
* Each `parity_*.json` has `"passed": true` **and** a non-null `min_iou` — a parity file
  with zero reference detections is not a pass.
* `benchmark.json` reports a `timing` field, i.e. synchronised timing, and no cell reads
  `NOT VERIFIED` where a measured value is expected.
* For FP16, `parity_fp16.json → engine.engine_io` must show `DataType.HALF`, and
  `tensorrt-verification.json → precision_policy` must not list `fp16` under
  `excluded_from_authoritative_table`. An FP16 number whose engine declared
  `DataType.FLOAT` is **not** FP16 evidence — that mistake happened once and is preserved
  in `final/superseded/` rather than quietly dropped.
* `fp32_onnx_vs_mixed_fp16_onnx.json` must report `passed: true`. That is what shows the
  optimisation transform kept the model's behaviour; the export succeeding does not.
* `onnx_provider_probes` must show what the CUDA provider actually did. A provider that
  is merely *listed* is `AVAILABLE - NOT QUALIFIED`, not `VERIFIED`.

If a run fails, keep the failure: copy the `blocked.json` (or the failing step's record in
`tensorrt-verification.json → steps`) back into `docs/evidence/tensorrt/`. A recorded
blocker is worth more than a deleted one.
