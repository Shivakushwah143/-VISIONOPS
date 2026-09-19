"""FP32 ONNX -> ModelOpt AutoCast mixed-FP16 ONNX, with metadata preservation.

TensorRT 11 removed the builder-level `BuilderFlag.FP16`, so precision has to live in
the graph. NVIDIA's `nvidia-modelopt` AutoCast performs that conversion, but it rebuilds
the graph and **drops the Ultralytics metadata** - including `names`, which the VisionOps
canonical contract needs. `edge.pipeline.Detector` refuses such a model with
`model_missing_class_metadata`, so the safety check is never bypassed; the metadata is
restored instead.

This helper does four things and records all of them:

    1. capture the original ONNX metadata (names, task, imgsz, strides, ...)
    2. run ModelOpt AutoCast to produce a strongly-typed mixed-precision graph
    3. restore the metadata and verify the taxonomy survived unchanged
    4. hash both artifacts and write a conversion record next to the output

A successful conversion is **not** evidence that the model still behaves. That claim
requires the parity check in `scripts.verify_tensorrt_gpu --fp16-onnx <out>`, which
compares detections against the FP32 graph through the shared canonical decoder on the
same qualification samples.

    python -m scripts.convert_fp16_onnx \
        --onnx var/model/hansung-p3.onnx \
        --out var/model/hansung-p3-fp16.onnx

Exit codes
    0  converted, metadata restored, taxonomy verified
    2  the conversion tool is unavailable or the conversion failed
    4  the conversion produced a graph the contract cannot accept
"""
import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ONNX TensorProto element types we care about when measuring how mixed the graph is.
ELEM_TYPE_NAMES = {1: 'float32', 2: 'uint8', 3: 'int8', 4: 'uint16', 5: 'int16', 6: 'int32',
                   7: 'int64', 9: 'bool', 10: 'float16', 11: 'float64', 12: 'uint32', 16: 'bfloat16'}

TOOL_UNAVAILABLE_EXIT = 2
CONTRACT_REJECTED_EXIT = 4


def utc():
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_metadata(path):
    """Original metadata, verbatim. This is what AutoCast drops and we restore."""
    import onnx

    model = onnx.load(str(path), load_external_data=False)
    return {prop.key: prop.value for prop in model.metadata_props}


def graph_facts(path):
    """Identity and shape facts of an ONNX graph, read from the file itself."""
    import onnx

    model = onnx.load(str(path), load_external_data=False)
    graph_input, output = model.graph.input[0], model.graph.output[0]
    return {
        'path': str(path), 'bytes': Path(path).stat().st_size, 'sha256': sha256_file(path),
        'opset': [{'domain': item.domain or 'ai.onnx', 'version': item.version}
                  for item in model.opset_import],
        'ir_version': model.ir_version, 'node_count': len(model.graph.node),
        'input_name': graph_input.name,
        'input_dtype': ELEM_TYPE_NAMES.get(graph_input.type.tensor_type.elem_type,
                                           str(graph_input.type.tensor_type.elem_type)),
        'input_shape': [int(dim.dim_value) for dim in graph_input.type.tensor_type.shape.dim],
        'output_name': output.name,
        'output_dtype': ELEM_TYPE_NAMES.get(output.type.tensor_type.elem_type,
                                            str(output.type.tensor_type.elem_type)),
        'output_shape': [int(dim.dim_value) for dim in output.type.tensor_type.shape.dim],
        'producer': f'{model.producer_name} {model.producer_version}'.strip(),
        'declared_names': {prop.key: prop.value for prop in model.metadata_props}.get('names'),
    }


def precision_tally(path):
    """How mixed the graph really is, counted from the file rather than asserted.

    Node "converted to FP16" is not something a caller can take on trust: this counts
    the ONNX value element types so the claim is reproducible.
    """
    import onnx

    model = onnx.load(str(path), load_external_data=False)
    counter = {}
    for value in list(model.graph.input) + list(model.graph.output) + list(model.graph.value_info):
        name = ELEM_TYPE_NAMES.get(value.type.tensor_type.elem_type,
                                   str(value.type.tensor_type.elem_type))
        counter[name] = counter.get(name, 0) + 1
    fp16 = counter.get('float16', 0)
    fp32 = counter.get('float32', 0)
    total = fp16 + fp32
    return {'value_types': counter, 'float16_values': fp16, 'float32_values': fp32,
            'float16_share': round(fp16 / total, 4) if total else None,
            'note': ('counted over graph inputs, outputs and value_info; a mixed graph keeps a '
                     'minority of values in float32 where sensitivity requires it')}


def convert_to_mixed_precision(onnx_path, output_path, low_precision_type):
    """Call ModelOpt AutoCast, tolerating API drift across modelopt releases.

    Keyword filtering is deliberate: the verification layer is frozen, and a new
    modelopt release adding arguments must not break the only path that can produce a
    true FP16 engine on TensorRT 11.
    """
    import inspect

    import modelopt.onnx.autocast as autocast

    function = getattr(autocast, 'convert_to_mixed_precision', None)
    if function is None:
        raise RuntimeError('modelopt.onnx.autocast.convert_to_mixed_precision is unavailable')
    accepted = set(inspect.signature(function).parameters)
    kwargs = {'low_precision_type': low_precision_type}
    for alias in ('high_precision_type',):
        if alias in accepted:
            kwargs[alias] = 'fp32'
    result = function(str(onnx_path), str(output_path), **kwargs)
    return {'api': 'modelopt.onnx.autocast.convert_to_mixed_precision',
            'kwargs_passed': kwargs, 'returned': result if isinstance(result, (dict, str, list)) else None}


def restore_metadata(original, converted, keys=None):
    """Copy the original metadata back over the converted graph.

    ModelOpt writes its own metadata (its version, its config). Those keys are kept;
    every original key wins, because the canonical contract depends on them.
    """
    import onnx

    model = onnx.load(str(converted), load_external_data=False)
    # What the conversion tool wrote stays, except where the original must win: the
    # canonical contract indexes class order, so a drifted `names` is a wrong model.
    produced = [prop.key for prop in model.metadata_props]
    collisions = [key for key in produced if key in original]
    for prop in list(model.metadata_props):
        if prop.key in original:
            model.metadata_props.remove(prop)
    restored = []
    for key, value in original.items():
        if keys and key not in keys:
            continue
        entry = model.metadata_props.add()
        entry.key, entry.value = key, value
        restored.append(key)
    onnx.save(model, str(converted))
    return {'restored_keys': sorted(restored),
            'conversion_produced_keys': sorted(produced),
            'collisions_overwritten_by_the_original': sorted(collisions),
            'original_keys': len(original)}


def verify_taxonomy(original_metadata, converted_path):
    """The restored taxonomy must be identical, not merely present.

    `edge.pipeline.Detector` reads `names` and refuses a model without it
    (`model_missing_class_metadata`). This proves the restore actually happened and that
    the class order still matches, which is what the source->canonical mapping indexes.
    """
    from shared.model_contract import parse_names

    converted = read_metadata(converted_path)
    record = {'names_present': converted.get('names') is not None,
              'names_identical_to_original': converted.get('names') == original_metadata.get('names'),
              'task_identical': converted.get('task') == original_metadata.get('task')}
    if not record['names_present']:
        raise SystemExit('converted graph has no `names` metadata: Detector would raise '
                         'model_missing_class_metadata')
    if not record['names_identical_to_original']:
        raise SystemExit('converted graph taxonomy differs from the original; the canonical '
                         'source->canonical mapping would be wrong')
    names = parse_names(converted['names'])
    record['source_classes'] = {str(key): value for key, value in names.items()}
    record['source_class_count'] = len(names)
    return record


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--onnx', default='var/model/hansung-p3.onnx')
    parser.add_argument('--out', default=None, help='default: <onnx stem>-fp16.onnx')
    parser.add_argument('--low-precision-type', default='fp16', choices=['fp16', 'bf16'])
    parser.add_argument('--report', default=None, help='default: <out>.conversion.json')
    parser.add_argument('--reuse', action='store_true',
                       help='keep an existing output instead of converting again')
    arguments = parser.parse_args(argv)

    source = Path(arguments.onnx)
    if not source.is_file():
        raise SystemExit(f'FP32 ONNX not found: {source}')
    destination = Path(arguments.out) if arguments.out else source.with_name(source.stem + '-fp16.onnx')
    report_path = Path(arguments.report) if arguments.report \
        else destination.with_suffix(destination.suffix + '.conversion.json')

    original_metadata = read_metadata(source)
    record = {'converted_at': utc(), 'low_precision_type': arguments.low_precision_type,
              'source': graph_facts(source), 'destination': str(destination),
              'metadata_before_conversion': {'keys': sorted(original_metadata),
                                             'names_present': original_metadata.get('names') is not None}}

    try:
        import modelopt  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        record['status'] = 'TOOL_UNAVAILABLE'
        record['reason'] = f'nvidia-modelopt is not importable here ({type(exc).__name__})'
        record['how_to_install'] = 'pip install -r requirements-gpu.txt   (needs an NVIDIA GPU host)'
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(record, indent=2, default=str))
        print(json.dumps({'status': record['status'], 'reason': record['reason']}, indent=2))
        return TOOL_UNAVAILABLE_EXIT

    if not (arguments.reuse and destination.is_file()):
        try:
            record['conversion'] = convert_to_mixed_precision(source, destination,
                                                              arguments.low_precision_type)
        except Exception as exc:  # noqa: BLE001
            record['status'] = 'CONVERSION_FAILED'
            record['reason'] = f'{type(exc).__name__}: {exc}'
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(record, indent=2, default=str))
            print(json.dumps({'status': record['status'], 'reason': record['reason']}, indent=2))
            return TOOL_UNAVAILABLE_EXIT
    else:
        record['conversion'] = {'skipped': 'reused the existing output', 'api': None}

    if not destination.is_file():
        record['status'] = 'CONVERSION_FAILED'
        record['reason'] = f'the conversion reported success but {destination} does not exist'
        path_guard = report_path
        path_guard.parent.mkdir(parents=True, exist_ok=True)
        path_guard.write_text(json.dumps(record, indent=2, default=str))
        print(json.dumps({'status': record['status'], 'reason': record['reason']}, indent=2))
        return TOOL_UNAVAILABLE_EXIT

    # ModelOpt dropped the Ultralytics metadata; restore it before anything reads the graph.
    record['metadata_restore'] = restore_metadata(original_metadata, destination)
    record['destination'] = graph_facts(destination)
    record['precision'] = precision_tally(destination)
    try:
        record['taxonomy'] = verify_taxonomy(original_metadata, destination)
    except SystemExit as exc:
        record['status'] = 'CONTRACT_REJECTED'
        record['reason'] = str(exc)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(record, indent=2, default=str))
        print(json.dumps({'status': record['status'], 'reason': record['reason']}, indent=2))
        return CONTRACT_REJECTED_EXIT

    warnings = []
    if record['destination']['input_shape'] != record['source']['input_shape']:
        warnings.append('input_shape_changed')
    if record['destination']['output_shape'] != record['source']['output_shape']:
        warnings.append('output_shape_changed')
    if record['destination']['input_dtype'] == 'float32':
        warnings.append('input_is_still_float32_so_an_fp16_engine_input_would_not_be_HALF')
    record['warnings'] = warnings
    record['status'] = 'CONVERTED' if not [w for w in warnings if w.endswith('_changed')] else 'CONVERTED_WITH_SHAPE_WARNINGS'
    record['next_step'] = (f'python -m scripts.verify_tensorrt_gpu --onnx {source} '
                           f'--fp16-onnx {destination} --precisions fp16 ...   '
                           '(parity against the FP32 graph is what proves the conversion kept behaviour)')
    record['not_evidence_of'] = ('correctness. The conversion succeeding says nothing about detection '
                                 'agreement; only the FP32-vs-FP16 ONNX parity check in the verifier does.')
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(record, indent=2, default=str))
    print(json.dumps({'status': record['status'], 'source_sha256': record['source']['sha256'],
                      'destination_sha256': record['destination']['sha256'],
                      'input_dtype': record['destination']['input_dtype'],
                      'float16_share': record['precision']['float16_share'],
                      'metadata_restored': record['taxonomy']['names_identical_to_original'],
                      'warnings': warnings, 'report': str(report_path)}, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
