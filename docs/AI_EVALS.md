# AI evaluation status

**BLOCKED:** No trained PPE model was executed. No AP, mAP, precision, recall, event quality or production FPS is reported as achieved.

The source candidate is Hexmon/vyra-yolo-ppe-detection at revision `08895b33d95d2587423ebe4f7c1b9c41beebd642`, artifact `best.onnx`, publisher-declared CC BY 4.0. Published SHA-256: `b99fed37bae1d111ddb622a0ef9077d42918a4daebd4a0bffbd8faaba273f246`. Local bytes and geometry are unverified. Source Person (11), Hardhat (3), NO-Hardhat (8) map to canonical person (0), helmet (1), no_helmet (2). Other classes are discarded. Source metadata: `training/PPE_SOURCE_MANIFEST.json`.

The associated Roboflow Universe Projects PPE Combined Model dataset v4 is publisher-declared CC BY 4.0. No dataset files were downloaded. Its published split is not evidence of the recording/site-disjoint evaluation split required by the specification. Attribution, original hashes and transformation hashes must accompany any derived dataset.

`training/validate_dataset.py` checks supplied provenance, taxonomy, group-disjoint assignments, image/label hashes and normalized YOLO boxes. `training/train.py` uses supplied YOLO11n base weights, not a silent COCO substitute. It records MLflow parameters and exported ONNX bytes. `training/evaluate.py` computes detector metrics from supplied predictions/labels at the configured confidence threshold; it is explicitly insufficient for complete promotion.

`engineering_fixture.py` generates constant-output ONNX and synthetic video solely to verify software plumbing. Its result is VERIFIED for engineering execution and is not an AI-quality result. It is never accepted as real-device evidence.

Raw-evidence import, temporal matching, detector metrics and promotion-policy recomputation were added in this continuation. Known-answer arithmetic passed; no real measurements were made. Full raw parity qualification and automated temporal-video evidence collection remain incomplete. See EVALUATION_EVIDENCE_CONTRACT.md.
