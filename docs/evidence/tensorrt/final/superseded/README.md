# SUPERSEDED — logical FP16 label over an FP32 graph

**Status: `SUPERSEDED — LOGICAL FP16 LABEL, FP32 GRAPH/INPUT`. Do not cite these numbers as FP16.**

This directory preserves the first GPU session's FP16 claim and the reason it does not
count. Nothing here was deleted, and nothing here may be quoted as FP16 performance.

## What the run claimed

| Runtime | Label | p50 ms | Mean ms | Model-only FPS |
| --- | --- | ---: | ---: | ---: |
| TensorRT | "fp16" | 4.293 | 4.78 | 209.21 |

## Why it does not count

The engine that produced those numbers was built from the **qualified FP32 ONNX**, and
its own I/O binding says so. From `parity_fp16_logical_label.json` (and from
`../fp32/benchmark.json` under `precisions.fp16.engine.engine_io`):

```json
"engine_io": {
  "inputs":  [{ "name": "images",  "dtype": "DataType.FLOAT", "torch_dtype": "torch.float32" }],
  "outputs": [{ "name": "output0", "dtype": "DataType.FLOAT", "torch_dtype": "torch.float32" }]
}
```

`DataType.FLOAT` in, `DataType.FLOAT` out. The precision was a **requested label passed to
the harness**, not a property of the graph. A caller asking for `--precisions fp16` got a
row labelled `fp16` while the engine ran the FP32 tensor types. The nearest result was
also essentially identical to the FP32 run (4.78 ms vs 4.834 ms mean), which is what a
mislabel looks like rather than what a real precision change looks like.

This is exactly the class of defect the repository's status rule exists to stop:

> never call an engine FP16 merely because the requested precision parameter says fp16.

## What replaced it

TensorRT 11 no longer exposes the old `BuilderFlag.FP16` path, so precision has to live in
the graph. The authoritative FP16 result therefore comes from a **ModelOpt AutoCast
mixed-FP16 ONNX** whose TensorRT engine really declares `DataType.HALF`:

* [`../true_fp16/`](../true_fp16/) — authoritative true-FP16 evidence
* [`../true_fp16/parity_fp16.json`](../true_fp16/parity_fp16.json) — `"dtype": "DataType.HALF"`, `"torch_dtype": "torch.float16"`
* [`../true_fp16/fp32_onnx_vs_mixed_fp16_onnx.json`](../true_fp16/fp32_onnx_vs_mixed_fp16_onnx.json) — the conversion's own parity check
* [`../fp32/`](../fp32/) — authoritative FP32 evidence (IoU 1.0, 4.834 ms mean, 206.87 model-only FPS)

## Guard added to the harness

`scripts/verify_tensorrt_gpu.py` now reads the **engine's declared tensor dtypes** after
`load()` and refuses to record a `VERIFIED` precision unless those dtypes back the claim:

* `precision_label` — what the caller asked for
* `actual_engine_dtypes` — what the engine declares
* a label that is not backed by real dtypes is recorded as
  `NOT VERIFIED - LABEL_NOT_BACKED_BY_ENGINE_DTYPES` and is excluded from the
  authoritative benchmark table

`docs/evidence/tensorrt/final/fp32/` therefore still contains the run-1 `benchmark.json`
verbatim (evidence is never silently edited), with `SUPERSEDED.md` pointing at the exact
key that must not be read as FP16.

## Files here

| File | What it is |
| --- | --- |
| `parity_fp16_logical_label.json` | run 1's `parity_fp16.json`, verbatim — the superseded claim |
| `GPU_RUN_RESULT_run1.md` | run 1's rendered result, verbatim — shows both `fp32` and the mislabelled `fp16` row |
