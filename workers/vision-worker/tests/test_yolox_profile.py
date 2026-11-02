import numpy as np
import pytest
from fr_domain.model_management import ModelManifest
from vision_worker.inference.yolo_onnx import YoloContractError, YoloOnnxDetector
from vision_worker.video.frame import VideoFrame


def profile(mode="yolox_raw"):
    return ModelManifest.model_validate(
        {
            "model_type": "PERSON_DETECTOR",
            "inputs": [{"name": "input", "shape": [1, 3, 608, 1088], "dtype": "float32"}],
            "outputs": [{"name": "output", "shape": [1, 13566, 6], "dtype": "float32"}],
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
                "person_class_id": 0,
                "class_count": 1,
                "confidence_threshold": 0.25,
                "iou_threshold": 0.45,
            },
        }
    )


def test_yolox_uses_bgr_raw_pixels_and_top_left_padding():
    detector = YoloOnnxDetector(None, profile())
    frame = VideoFrame("cam", "s", 1, 0, 2, 1, "RGB", bytes([10, 20, 30]) * 2)
    tensor, scale, left, top = detector.preprocess(frame)
    assert tensor.shape == (1, 3, 608, 1088)
    assert tensor.dtype == np.float32 and (left, top) == (0, 0)
    assert tensor[0, :, 0, 0].tolist() == [30, 20, 10]
    assert tensor[0, :, 607, 0].tolist() == [114, 114, 114]
    assert scale == 544


@pytest.mark.parametrize("mode", ["yolox_raw", "yolox_decoded", "yolox_xyxy"])
def test_yolox_decodes_non_square_grid_and_maps_boxes_back_to_original(mode):
    detector = YoloOnnxDetector(None, profile(mode))
    frame = VideoFrame("cam", "s", 1, 0, 544, 304, "RGB", b"")
    output = np.zeros((1, 13566, 6), dtype=np.float32)
    if mode == "yolox_raw":
        output[0, 0] = [10, 20, np.log(5), np.log(10), 0.8, 0.9]
        output[0, 1] = [9, 20, np.log(5), np.log(10), 0.7, 0.9]
    elif mode == "yolox_decoded":
        output[0, 0] = [80, 160, 40, 80, 0.8, 0.9]
        output[0, 1] = [80, 160, 40, 80, 0.7, 0.9]
    else:
        # Export has already multiplied objectness/class score and emitted class ID 0.
        output[0, 0] = [60, 120, 100, 200, 0.72, 0]
        output[0, 1] = [60, 120, 100, 200, 0.63, 0]
    boxes = detector.postprocess(output, frame, 2, 0, 0)
    assert len(boxes) == 1
    box = boxes[0]
    assert [box.x1, box.y1, box.x2, box.y2] == pytest.approx([30, 60, 50, 100])
    assert box.confidence == pytest.approx(0.72)


def test_yolox_rejects_wrong_grid_length_instead_of_returning_no_people():
    detector = YoloOnnxDetector(None, profile())
    with pytest.raises(YoloContractError):
        detector.postprocess(
            np.zeros((1, 100, 6)), VideoFrame("c", "s", 1, 0, 10, 10, "RGB", b""), 1, 0, 0
        )


def test_xyxy_filters_class_ids_without_inferring_class_count_from_six_columns():
    manifest = profile("yolox_xyxy")
    manifest = manifest.model_copy(update={"metadata": {**manifest.metadata, "person_class_id": 2}})
    detector = YoloOnnxDetector(None, manifest)
    output = np.zeros((1, 13566, 6), dtype=np.float32)
    output[0, 0] = [10, 10, 50, 80, 0.8, 2]
    output[0, 1] = [10, 10, 50, 80, 0.9, 0]
    boxes = detector.postprocess(output, VideoFrame("c", "s", 1, 0, 100, 100, "RGB", b""), 1, 0, 0)
    assert len(boxes) == 1
    assert boxes[0].confidence == pytest.approx(0.8)
