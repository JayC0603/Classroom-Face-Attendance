"""Inspect explicit YOLOX profiles; tensor names and dimensions come from ONNX."""

from importlib import import_module
from typing import Any

from fr_domain.model_management import ModelManifest


def has_xyxy_score_class_output(model: Any) -> bool:
    """Recognize the export wrapper from graph operations, never tensor shape alone."""
    nodes = {output: node for node in model.graph.node for output in node.output}
    final = nodes.get(model.graph.output[0].name)
    if final is None or final.op_type != "Concat" or len(final.input) != 3:
        return False
    boxes, score, label = (nodes.get(name) for name in final.input)
    if (
        boxes is None
        or boxes.op_type != "MatMul"
        or len(boxes.input) != 2
        or score is None
        or score.op_type != "Mul"
        or label is None
        or label.op_type != "Cast"
    ):
        return False
    argmax = nodes.get(label.input[0])
    reductions = [nodes.get(name) for name in score.input]
    if (
        argmax is None
        or argmax.op_type != "ArgMax"
        or not any(
            node is not None and node.op_type == "ReduceMax" and node.input[0] == argmax.input[0]
            for node in reductions
        )
    ):
        return False
    matrix = next(
        (value for value in model.graph.initializer if value.name == boxes.input[1]), None
    )
    if matrix is None:
        return False
    values = import_module("onnx.numpy_helper").to_array(matrix).tolist()
    return bool(values == [[1, 0, 1, 0], [0, 1, 0, 1], [-0.5, 0, 0.5, 0], [0, -0.5, 0, 0.5]])


def inspect_yolox_manifest(
    payload: bytes, mode: str, person_class_id: int, confidence: float
) -> ModelManifest:
    if mode not in {"auto", "yolox_raw", "yolox_decoded", "yolox_xyxy"} or not 0 <= confidence <= 1:
        raise ValueError("INVALID_YOLOX_PROFILE")
    try:
        onnx = import_module("onnx")
        model = onnx.load_model_from_string(payload)
        onnx.checker.check_model(model)
    except Exception as error:
        raise ValueError("INVALID_MODEL_FORMAT") from error

    def tensor(item: Any) -> dict[str, Any]:
        t = item.type.tensor_type
        if t.elem_type != 1:
            raise ValueError("YOLOX_REQUIRES_FP32")
        return {
            "name": item.name,
            "shape": [
                int(d.dim_value) if d.HasField("dim_value") else d.dim_param or "dynamic"
                for d in t.shape.dim
            ],
            "dtype": "float32",
        }

    if len(model.graph.input) != 1 or len(model.graph.output) != 1:
        raise ValueError("YOLOX_REQUIRES_SINGLE_INPUT_OUTPUT")
    xyxy_export = has_xyxy_score_class_output(model)
    if mode == "auto":
        if not xyxy_export:
            raise ValueError("YOLOX_OUTPUT_MODE_REQUIRED")
        mode = "yolox_xyxy"
    if xyxy_export and mode != "yolox_xyxy":
        raise ValueError("YOLOX_XYXY_SCORE_CLASS_EXPORT_SELECT_MODE")
    inputs, outputs = [tensor(model.graph.input[0])], [tensor(model.graph.output[0])]
    shape, out = inputs[0]["shape"], outputs[0]["shape"]
    if len(shape) != 4 or shape[1] != 3 or (shape[0] != 1 and not isinstance(shape[0], str)):
        raise ValueError("YOLOX_REQUIRES_NCHW_BATCH1")
    height, width = shape[2:]
    if not all(type(v) is int and 32 <= v <= 2048 and v % 32 == 0 for v in (height, width)):
        raise ValueError("YOLOX_REQUIRES_STATIC_HEIGHT_WIDTH")
    expected = sum((height // s) * (width // s) for s in (8, 16, 32))
    if (
        len(out) != 3
        or (out[0] != 1 and not isinstance(out[0], str))
        or (isinstance(out[1], int) and out[1] != expected)
    ):
        raise ValueError("YOLOX_REQUIRES_DENSE_BNC_OUTPUT")
    channels = out[2]
    if mode == "yolox_xyxy" and channels != 6:
        raise ValueError("YOLOX_XYXY_REQUIRES_SIX_COLUMNS")
    if (
        type(channels) is not int
        or not 6 <= channels <= 1005
        or not 0 <= person_class_id < (1000 if mode == "yolox_xyxy" else channels - 5)
    ):
        raise ValueError("INVALID_PERSON_CLASS_ID")
    return ModelManifest.model_validate(
        {
            "model_type": "PERSON_DETECTOR",
            "inputs": inputs,
            "outputs": outputs,
            "preprocessing": {
                "layout": "NCHW",
                "color_order": "BGR",
                "normalization": "raw_0_255",
                "resize_mode": "letterbox",
            },
            "metadata": {
                "architecture": "YOLOX",
                "output_semantics": mode,
                "output_layout": "BNC",
                **(
                    {"class_id_limit": 1000}
                    if mode == "yolox_xyxy"
                    else {"class_count": channels - 5}
                ),
                "person_class_id": person_class_id,
                "confidence_threshold": confidence,
                "iou_threshold": 0.45,
                "padding_alignment": "top_left",
                "strides": [8, 16, 32],
            },
            "runtime_compatibility": ["ONNX_CPU"],
        }
    )
