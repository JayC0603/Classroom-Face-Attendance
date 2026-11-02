# Thêm, sửa, xóa camera và model person

- Camera: thêm nguồn hình; sửa cấu hình; xóa với xác nhận và phiên bản đã tải. Xóa dừng preview, thu hồi phiên webcam và loại camera khỏi cấu hình worker. Lịch sử và bằng chứng tiếp tục theo thời hạn lưu. Camera có buổi học liên kết trả lỗi 409 và giữ nguyên dữ liệu.
- Model person: thêm qua form YOLOX hiện có; model YOLO/YOLO26 đã đăng ký cũng có sửa/xóa. Sửa tên phiên bản; sửa ngưỡng trong khoảng 0.01–1 khi chưa có deployment. Với model đã triển khai, đổi ngưỡng/file bằng cách tải phiên bản mới để bảo toàn cấu hình runtime.
- Xóa model chỉ khi mọi deployment không hoạt động/chờ kích hoạt và không còn assignment. Có nút ngừng deployment chưa được chọn. Cần chọn model khác trước khi xóa model đang gán cho camera.
- Xóa phiên bản dọn deployment nháp/ngừng/lỗi trong cùng thao tác repository. Bản ONNX lưu trữ chưa bị xóa vật lý; hành động này xóa phiên bản khỏi danh sách sử dụng.
- PostgreSQL cập nhật cả version_label và JSON khi đổi tên, giữ ràng buộc tên phiên bản không trùng.

Kiểm chứng: 37 test backend liên quan qua; 1 test tích hợp PostgreSQL với schema tạm qua; 9 test CameraPanel và 3 test PersonModelsPanel qua. Build TypeScript/Vite, mypy 5 file backend và Ruff qua.

Trình duyệt Edge đã thực hiện thêm/sửa/hủy xóa/xóa một camera thử nghiệm qua API thật, sau đó mở/hủy form sửa model; không có pageerror. Camera thử nghiệm được xóa sau kiểm tra. Ảnh kiểm tra cục bộ tại `.runtime/ui-redesign/crud-camera.png` và `crud-person-edit.png`.
