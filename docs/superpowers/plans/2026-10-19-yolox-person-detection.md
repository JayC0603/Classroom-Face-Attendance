# YOLOX person detection upload and live overlay

User will upload `yolox.onnx`, expected input height608/width1088. Add person
detection beside face comparison without changing its identity semantics.

1. Support explicit YOLOX raw-grid and decoded-dense profiles in the existing CPU
   detector adapter. Follow official BGR float32 0..255, top-left resize/padding,
   strides8/16/32, objectness*class and NMS semantics. Test non-square coordinates.
2. Add upload inspection deriving actual tensor names/shapes and class count.
   Register a draft version after validation; activate/warmup and assign through
   the existing model lifecycle. Reject incompatible exports with actionable errors.
3. Publish worker-processed RGB frames plus normalized person boxes as one bounded
   preview unit, including model deployment/session provenance. Support native,
   RTSP and browser sources; face comparison reads that same preview image.
4. Add an upload/configuration card in Models and person boxes/count/confidence in
   live camera view. User supplies weights; no fake production detections or model
   downloads/automatic assignment of test graphs.
5. Validate with CPU fixtures/integration tests, frontend behavior/build/type checks,
   real-browser UI checks and live service health. Actual supplied-model acceptance
   remains pending until the user's file is uploaded.

Official references: Megvii YOLOX ONNXRuntime demo, data_augment.preproc,
demo_utils.demo_postprocess, and tools/export_onnx.py at github.com/Megvii-BaseDetection/YOLOX.
