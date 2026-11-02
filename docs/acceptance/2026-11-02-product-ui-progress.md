# Tiến độ triển khai giao diện sản phẩm

## Đã triển khai

- Điều hướng theo vai trò với Tổng quan, Giám sát, Camera, Cảnh báo và các trang nghiệp vụ; giữ các hash route cũ.
- Giám sát một camera được chọn, sự kiện bên cạnh, trạng thái hình mới và phiên bản worker.
- Cấu hình camera tách khỏi giám sát; nhóm nguồn hình, nhận diện, bằng chứng và ROI; lỗi thao tác được giữ trên màn hình.
- Cảnh báo lọc camera/loại/ngày, phân trang server 50 bản ghi; biên ngày theo Asia/Ho_Chi_Minh.
- YOLO26/YOLO/YOLOX cùng hiển thị; dữ liệu model cũ thiếu ngưỡng dùng mặc định 0.25 giống runtime.
- Ảnh nhận diện và bbox dùng cùng response đã xử lý, bỏ việc trộn preview mới với bbox cũ.
- Bộ chọn hồ sơ theo tên/mã sinh viên và giáo viên theo email thay ô nhập UUID.
- API camera hỗ trợ expected_revision; repository kiểm tra phiên bản nguyên tử, PostgreSQL khóa hàng khi cập nhật; lỗi xung đột trả 409.
- Luồng runtime tự reconnect, lấy snapshot sau kết nối lại; bộ nhớ tối đa 256 camera. Health API làm mới mỗi 15 giây.
- Lịch sử hủy request khi đổi bộ lọc và không chạy chồng polling; bộ chọn hồ sơ giữ lựa chọn khi tìm tiếp.
- Token giao diện sáng/tối, responsive, sửa màu chữ/nền được phát hiện qua ảnh chụp.

## Bằng chứng kiểm tra

- Frontend: 46 test qua; build TypeScript/Vite qua. Hai chỉnh sửa cuối về giữ lựa chọn và hủy request lịch sử đã được typecheck/build; chưa có test hành vi riêng.
- Backend: 36 test camera/repository/face-event/YOLOX qua; thêm 1 test model API qua, bao gồm model thiếu ngưỡng.
- Mypy: 5 file backend thay đổi qua.
- Edge headless: 6 trang ở light/dark, thêm Giám sát/Camera/Cảnh báo ở 360/768/1920 px; không có pageerror hoặc cuộn ngang toàn trang. Desktop mặc định 1366 px.
- API thật `/person-models`: HTTP 200, có YOLO26.
- Ảnh và báo cáo cục bộ: `.runtime/ui-redesign/`. Đã trực tiếp kiểm tra ảnh Camera sáng, Model tối và Cảnh báo mobile tối, rồi sửa tương phản.
- Kiểm tra này không thay thế đánh giá tương phản WCAG toàn bộ, thử keyboard toàn bộ hoặc chạy đầy đủ vai trò bằng dữ liệu thật.

## Chưa hoàn thành trong plan

- Tách cấu hình đang chạy khỏi bản nháp bền vững trong database. Hiện lưu nháp vẫn dừng camera; UI đã thông báo rõ.
- Wizard thêm camera từng bước, diff trước áp dụng và rollback phiên bản.
- ROI sửa điểm bằng keyboard/drag; bảo vệ Back/Forward và thay đổi chưa lưu chính xác theo dirty state.
- Hoàn thiện toàn bộ màn giáo viên/sinh viên, các empty/loading/error state và xác nhận vô hiệu hóa tài khoản.
- Chuẩn hóa System status theo freshness; test reconnect snapshot không ghi đè dữ liệu stream mới hơn.
- Nghiệm thu live recognition với camera hoạt động và đủ mọi tình huống lỗi/evidence; chưa khẳng định camera thực tế đang nhận diện chỉ từ việc trang hiển thị.

Chưa đánh dấu toàn bộ kế hoạch A–G hoàn thành. Không có reset dữ liệu, thay model đang gán hoặc thay ngưỡng nhận diện trong lần chỉnh UI này.
