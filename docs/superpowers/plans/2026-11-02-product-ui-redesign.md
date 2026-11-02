# Kế hoạch thiết kế lại giao diện thành sản phẩm vận hành thực tế

Ngày lập: 02/11/2026, múi giờ Asia/Saigon.

Trạng thái: đề xuất chi tiết để triển khai; chưa thay đổi giao diện hay cấu hình vận hành.
Phạm vi: sản phẩm quản lý camera, nhận diện khuôn mặt, cảnh báo và điểm danh.

## 1. Skill đã bổ sung và căn cứ lập kế hoạch

- Đã cài `interface-design` vào `.agents/skills/interface-design` bằng skill-installer.
- Nguồn: https://github.com/Dammyjay93/interface-design
- Commit ghim: `2f9be3206855bcb2d1d0af262c8bae25cba6658d`.
- Entrypoint: `.agents/skills/interface-design/SKILL.md`; giữ metadata và giấy phép nguồn.
- Dùng cùng `clear-ui-design`, `premium-ui-design`, `face-recognition-ui-engineering`.
  Các hợp đồng nhận diện, cảnh báo, quyền và cấu hình của dự án vẫn là căn cứ nghiệp vụ.
- Skill mới được discovery tự động từ lượt làm việc tiếp theo. Trong lượt này đã đọc
  hướng dẫn và dùng để xây dựng đề xuất.

Đã đọc mã nguồn App, Sidebar, camera, model, lịch sử, quản trị, giáo viên, sinh viên,
các API camera/model và CSS hiện tại. Chưa hoàn thành kiểm tra hình ảnh trực tiếp:
Computer Use đã dừng vì không xác định được URL hiện tại đủ tin cậy. Do đó, phát hiện
dưới đây là phát hiện từ mã nguồn; các kết luận về bố cục thực tế phải được xác nhận
trong giai đoạn khảo sát trực quan.

## 2. Mục tiêu sản phẩm và người sử dụng

Thiết kế cho người vận hành biết camera nào đang làm việc, có chuyện gì vừa xảy ra,
và hành động tiếp theo là gì; người cấu hình hoàn thành thiết lập mà không cần hiểu
tensor, manifest hoặc ID nội bộ.

| Người sử dụng | Việc chính | Màn hình cần ưu tiên |
|---|---|---|
| Quản trị/vận hành, quyền ADMIN hiện tại | Xem camera, kiểm tra cảnh báo, xử lý sự cố | Giám sát, cảnh báo, camera |
| Người cấu hình hệ thống, cũng dùng ADMIN | Thêm nguồn hình, ROI, model, chính sách | Cấu hình camera, mô hình, hệ thống |
| Giáo viên | Chọn lớp, tạo buổi, duyệt ảnh, xem điểm danh | Lớp của tôi, buổi học, hàng đợi duyệt |
| Sinh viên | Đăng ký khuôn mặt và xem kết quả của mình | Hồ sơ, đăng ký, điểm danh |

Không tạo thêm quyền OPERATOR ở frontend khi backend chưa hỗ trợ.

Các tình huống nghiệm thu quan trọng:

1. Người vận hành mở trang và tìm camera cần xem trong tối đa ba thao tác.
2. Người cấu hình thêm camera theo trình hướng dẫn, nhìn thấy hình kiểm tra trước khi áp dụng.
3. Sửa ngưỡng cảnh báo 5 thành 8 frame: thấy giá trị đang dùng, bản nháp, thay đổi,
   và xác nhận worker đã áp dụng; không tự coi lưu nháp là đã chạy.
4. Khi có cảnh báo, xem được camera, thời điểm, căn cứ đủ frame và trạng thái snapshot.
5. Giáo viên phân công/ghi danh bằng tìm tên, mã sinh viên; không phải nhớ UUID.
6. Sinh viên biết ảnh đang chờ duyệt, được duyệt hay bị từ chối và cần làm gì tiếp.

## 3. Những điểm cần sửa trước từ mã nguồn

| Mức | Phát hiện | Tác động và hướng xử lý |
|---|---|---|
| P0 | CameraPanel chứa tạo nguồn, cấu hình, ROI, trạng thái và video trong cùng luồng dài | Tách giám sát khỏi cấu hình; trang chi tiết theo nhiệm vụ |
| P0 | CameraService.update luôn trả về DRAFT; API dừng preview khi ACTIVE chuyển về nháp | Thiết kế phiên bản đang chạy và bản nháp riêng trước khi hứa sửa cấu hình không gián đoạn |
| P0 | PersonModelsPanel và `/person-models` chỉ tập trung YOLOX | Danh sách model người phải phản ánh cả YOLO26 đã gán qua registry chung |
| P0 | CameraRecognition có thể vẽ kết quả nhận diện lên ảnh preview được cập nhật độc lập | Gắn overlay với đúng frame/session; không chỉ cải thiện hình thức bbox |
| P1 | Hàng đợi ảnh/cảnh báo nằm trong trang lịch sử, thiếu lối vào chuyên biệt | Tạo trang cảnh báo riêng, có trạng thái ảnh và liên kết về camera |
| P1 | Bộ lọc ngày/người của lịch sử không được truyền cho FaceEventsPanel | Mỗi loại lịch sử chỉ hiện bộ lọc API thực sự hỗ trợ; bổ sung truy vấn còn thiếu |
| P1 | Quản trị lớp dùng ID giáo viên và hồ sơ nhập tay | Thay bằng ô tìm kiếm/chọn từ dữ liệu có quyền truy cập |
| P1 | CSS có nhiều lớp ghi đè cùng selector, màu cứng và `!important` | Chuẩn hóa token/component, chuyển từng khu vực, không thêm một lớp theme cuối file |
| P1 | Dữ liệu runtime được giữ nhưng chưa có luồng trình bày cũ/mất tín hiệu rõ ràng | Lấy timestamp/freshness có căn cứ; tách mất kết nối và chưa biết |
| P1 | Trạng thái API ở topbar lấy health lúc vào trang | Không dùng chỉ báo này để đại diện cho camera/model; cập nhật có quản lý |
| P2 | Icon điều hướng hiện dùng cùng một hình cho mọi mục | Dùng icon theo ý nghĩa, giữ nhãn chữ; không chỉ trang trí |

Các file tham chiếu chính: `apps/web/src/app/{App,Sidebar}.tsx`,
`features/cameras/{CameraPanel,CameraRecognition}.tsx`,
`features/models/{ModelsPanel,PersonModelsPanel,AdvancedModelsPanel}.tsx`,
`features/history/{RecognitionHistoryPanel,FaceEventsPanel}.tsx`,
`features/admin/AdminManagement.tsx`, `apps/web/src/styles.css`,
`apps/control-api/src/control_api/{cameras,person_model_api,simple_recognition_api}.py`.

## 4. Hướng thiết kế có chủ đích

**Cảm giác:** bàn điều hành của một cơ sở giáo dục: rõ, điềm tĩnh, có trật tự;
thông tin thời gian thực và việc cần xử lý nổi bật hơn phần cài đặt.

- Thế giới sản phẩm: ca trực, cổng/lớp học, camera, vùng theo dõi, ảnh đăng ký,
  bằng chứng, buổi điểm danh, người phụ trách.
- Thế giới màu: trắng bảng/lớp học, xám thiết bị, màu than màn hình camera,
  cam nhận diện FPT, xanh trạng thái sẵn sàng, hổ phách cần kiểm tra.
- Điểm đặc trưng: **dải trạng thái camera + dòng sự kiện có ảnh** cùng một ngữ cảnh.
  Chọn camera sẽ cập nhật video, cấu hình đang áp dụng và sự kiện tương ứng;
  không bắt người dùng tự đối chiếu ba trang bằng ID.
- Tránh dashboard toàn ô số giống nhau: video là vùng lớn; sự kiện là danh sách;
  cấu hình là form; danh mục là bảng. Mỗi màn hình có một trọng tâm.
- Tránh nút cam cho mọi hành động: cam cho hành động chính; dừng/xóa có ngữ nghĩa
  riêng; trạng thái có chữ đi cùng màu.
- Tránh bảng thông số kỹ thuật dẫn đầu: đưa thông tin triển khai vào chẩn đoán mở rộng.

### Ngôn ngữ hình ảnh đề xuất

| Thành phần | Đề xuất |
|---|---|
| Nhận diện | Giữ logo FPT hiện có; không tạo logo mới |
| Nền/surface | Light: nền xám rất nhẹ, vùng làm việc trắng; video giữ nền tối |
| Dark mode | Cùng hệ phân cấp và token; không đổi màu chủ đạo sang một theme khác |
| Màu hành động | Cam đậm đủ tương phản cho nút có chữ trắng; kiểm tra trước khi chốt mã màu |
| Typography | Giữ bộ font hiện có nếu đủ dấu Việt; 14–16 px nội dung, 24–28 px tiêu đề trang |
| Khoảng cách | Đơn vị 4 px; 8/12/16/24/32 cho nhóm và vùng nội dung |
| Nút/ô nhập | 40 px desktop, 44 px vùng chạm; focus rõ, trạng thái chờ giữ nguyên kích thước |
| Bo góc | 6–8 px control, 10–12 px panel; tránh cùng một bo góc lớn cho mọi thứ |
| Độ nổi | Surface và viền nhẹ; shadow chỉ cho menu/dialog/khối nổi |
| Chuyển động | 120–220 ms ở thành phần đổi trạng thái; không animation video/bbox liên tục |

Đây là đề xuất thiết kế, chưa phải bộ token được đo và chốt. Khi triển khai, lưu
quyết định đã xác nhận vào `.interface-design/system.md` để các màn hình dùng thống nhất.

## 5. Cấu trúc điều hướng mới

```text
Vận hành
  Tổng quan
  Giám sát trực tiếp
  Cảnh báo
  Điểm danh & lịch sử
Quản lý
  Camera
  Hồ sơ khuôn mặt
  Lớp học
  Tài khoản
Thiết lập
  Mô hình AI
  Trạng thái hệ thống
```

Tổng quan chỉ tổng hợp dữ liệu thật. Nếu API chưa có tổng số đáng tin cậy thì dùng
liên kết công việc và trạng thái tài nguyên, không tạo KPI giả.
Giáo viên và sinh viên có menu rút gọn theo quyền hiện tại. Các route/hash cũ cần
được ánh xạ để bookmark vẫn dùng được; không bắt buộc đổi routing library ngay.

## 6. Đặc tả từng màn hình

### 6.1 Giám sát trực tiếp — ưu tiên cao nhất

- Thanh trên: chọn camera, trạng thái nguồn hình, thời điểm cập nhật, bật/dừng.
- Vùng chính: video lớn, đúng tỷ lệ; bật/tắt lớp bbox người, mặt và ROI trong UI.
- Cột bên: sự kiện gần đây của camera, ảnh nhỏ, thời gian, lý do cảnh báo.
- Dưới video: tình trạng phát hiện người và so sánh mặt; chẩn đoán FPS/worker trong mục mở rộng.
- Chế độ tập trung một camera; bố cục hai/bốn camera chỉ khi preview còn đủ dùng.
- Trạng thái: chưa cấu hình, đang kết nối, đang nhận hình, mất hình, camera tắt,
  model chưa sẵn sàng, không có ảnh tham chiếu, quyền webcam bị từ chối.
- Overlay phải dùng đúng frame đã xử lý hoặc có hợp đồng đồng bộ đủ an toàn;
  khi đổi session/revision phải bỏ ảnh và overlay cũ cùng nhau.
- Không hiển thị tên như đã xác minh khi backend chỉ trả PENDING.

### 6.2 Camera — danh sách và chi tiết riêng

Danh sách hiển thị tên, loại nguồn, tình trạng vận hành, bản cấu hình và hành động.
Nút chính là **Thêm camera**; mỗi dòng có **Mở giám sát**, **Cấu hình**, menu phụ.

Trình thêm camera gồm bốn bước: chọn nguồn → thông tin kết nối → kiểm tra hình →
đặt tên/xác nhận. Dùng ba tên rõ nghĩa: camera mạng RTSP, webcam máy worker,
webcam trình duyệt. Chỉ nguồn RTSP mới có thông tin kết nối mạng.

Chi tiết camera gồm: **Nguồn hình**, **Vùng giám sát**, **Nhận diện & cảnh báo**,
**Ảnh minh chứng**, **Chẩn đoán**. Không hiển thị tất cả form trên danh sách camera.

### 6.3 Cấu hình camera theo nhóm có ý nghĩa

| Nhóm | Trường hiện có | Cách trình bày và lưu ý |
|---|---|---|
| Cơ bản | name, source_type, device_index, codec | Tên dễ nhớ; mô tả worker là máy gắn camera; codec để tự động mặc định |
| Kết nối | rtsp_url | Che thông tin; chỉ thay khi nhập mới; không lưu trong localStorage |
| Video | display_fps, sampling_fps | Tách tốc độ xem và tốc độ xử lý; không hứa máy đạt đúng số đã đặt |
| ROI | roi_polygon | Vẽ trên hình, undo/xóa/khôi phục; điểm chuẩn hóa; kiểm tra hình học |
| Cảnh báo | unknown_alert_frames | Mặc định 5, giới hạn 2–60; giải thích là frame nhận diện khác nhau liên tiếp |
| Điểm danh | attendance_enabled | Nêu rõ chỉ danh tính đã xác minh mới được ghi điểm danh |
| Bằng chứng | evidence_retention_days | Hiện thời hạn theo ngày và ảnh hưởng xóa ảnh hết hạn |
| Vận hành | enabled, lifecycle, revision | Đọc từ backend; không gộp đang bật với đã nhận hình |

Giữ lời giải thích nghiệp vụ: khuôn mặt mới trong ROI được chụp; cảnh báo người
lạ cần đủ chuỗi frame. Đây không phải phép đo hướng đi qua cửa.

Ngưỡng confidence của person detector và ngưỡng so sánh khuôn mặt hiện thuộc
manifest/profile, không phải mọi camera đều có API chỉnh riêng. Đợt đầu hiển thị
giá trị đang dùng và nơi quản lý; chỉ thêm editor khi backend có schema tương ứng.
Không biến các hằng số trong mã thành những ô nhập giả.

### 6.4 Luồng lưu và áp dụng cấu hình

Luồng đích: chỉnh bản nháp → kiểm tra → xem thay đổi → áp dụng → chờ worker xác nhận.

- Form thể hiện rõ **Đang sử dụng** và **Bản nháp**.
- Rời trang khi chưa lưu: có lựa chọn ở lại hoặc bỏ thay đổi.
- Lỗi gắn cạnh trường; lỗi toàn cục có thông điệp và hành động phục hồi.
- Áp dụng hiển thị những gì đổi, camera chịu ảnh hưởng và có thể phải kết nối lại.
- Sau request thành công chỉ hiển thị **Đang áp dụng**. Chỉ ghi **Đã áp dụng** khi
  có revision runtime phù hợp; nếu chưa có dữ liệu thì ghi **Chưa xác nhận**.
- Rollback chỉ xuất hiện khi backend lưu revision cũ và hỗ trợ thao tác đó.
- Để camera tiếp tục chạy khi sửa nháp, cần backend tách active/draft; đây là hạng
  mục API bắt buộc cho trải nghiệm đích. Bản UI chuyển tiếp phải báo rõ lưu nháp
  hiện có thể dừng camera, không che hành vi đó.

### 6.5 Mô hình AI

- Ba vai trò rõ ràng: phát hiện người, phát hiện mặt, so sánh khuôn mặt.
- Hiện đúng mô hình đang thực thi, phiên bản, CPU/GPU thực tế và trạng thái worker.
- YOLO26 và YOLOX cùng nằm dưới **Phát hiện người**. Không gọi mọi model là YOLOX.
- Luồng: tải ONNX → kiểm tra tương thích → chạy kiểm tra/warmup → áp dụng.
- Metadata tensor/manifest ở phần kỹ thuật, không bắt người vận hành nhập JSON.
- Bản đang dùng và bản đã tải lên phải phân biệt; có thông báo khi chưa gán model.
- YuNet/SFace đang dùng và pipeline SCRFD/AdaFace nâng cao phải thể hiện như các
  luồng hỗ trợ khác nhau; không vẽ một pipeline giả như mọi giai đoạn đều đã chạy.
- Không cung cấp nút GPU nếu môi trường không hỗ trợ; giải thích khả năng thực tế.

### 6.6 Cảnh báo và ảnh minh chứng

- Trang riêng: lọc camera, loại sự kiện và thời gian khi API hỗ trợ.
- Phân biệt **Ảnh lúc xuất hiện** và **Cảnh báo người lạ**; không tô mọi ảnh vào là cảnh báo.
- Dòng sự kiện: ảnh, camera, thời điểm, loại, số frame căn cứ; mở chi tiết bên cạnh.
- Chi tiết: ảnh crop, trạng thái lưu ảnh, liên kết tới camera, thông tin kỹ thuật mở rộng.
- PENDING: đang lưu; FAILED: cảnh báo còn tồn tại nhưng lưu ảnh lỗi; EXPIRED: ảnh
  hết hạn; không dùng hình placeholder như một bằng chứng thật.
- Tải lại trang vẫn đọc được lịch sử. Reconnect REST/WebSocket không tạo bản sao sự kiện.
- **Đã xem/đã xử lý**, ghi chú và bộ đếm chưa đọc là đợt sau vì hiện chưa có API lưu
  acknowledgment. Không thêm toggle chỉ thay đổi trong bộ nhớ trình duyệt.
- Email/Telegram/push ngoài ứng dụng không thuộc đợt thiết kế này.

### 6.7 Hồ sơ và duyệt ảnh

- Danh sách tìm theo tên/mã sinh viên; trạng thái đã có ảnh được duyệt nhìn được ngay.
- Chi tiết có thông tin, ảnh đăng ký, lịch sử; ảnh không trộn với evidence cảnh báo.
- Trình đăng ký hướng dẫn ảnh đúng/sai bằng hình minh họa trung tính; thông báo lỗi từng file.
- Giáo viên duyệt trong phạm vi được giao; giữ nguyên ranh giới quyền của backend.
- Không tự biến cảnh báo người lạ thành hồ sơ đăng ký hoặc ảnh tham chiếu.

### 6.8 Lớp, tài khoản và điểm danh

- Tách form tạo mới khỏi danh sách đang xem; các thao tác hàng loạt chỉ thêm khi có nhu cầu/API.
- Tìm giáo viên và sinh viên theo tên/email/mã; frontend gửi ID phía sau.
- Tạo buổi học: lớp → camera → lịch → xác nhận; dùng múi giờ Việt Nam rõ ràng.
- Điểm danh theo buổi và sổ theo ngày là hai ngữ cảnh khác nhau, tên và bộ lọc phải phân biệt.
- Vô hiệu hóa tài khoản yêu cầu xác nhận có tên đối tượng và hậu quả.
- Sinh viên chỉ thấy dữ liệu của mình; giáo viên chỉ lớp được phân công.

### 6.9 Hệ thống

- Phần thường dùng: API, worker, nguồn hình, model và nơi lưu ảnh, mỗi mục có căn cứ riêng.
- Đang kết nối, chưa có tín hiệu và dữ liệu cũ có nhãn riêng; không một chấm xanh cho mọi thứ.
- Thông số kỹ thuật/ID/checksum/provider đặt trong mục chẩn đoán.
- Không đưa credential, DSN, endpoint nội bộ hoặc cài đặt chỉ có ở `.env` vào form web.

## 7. Những phần backend cần có hoặc phải kiểm chứng

| Hạng mục | Hiện trạng | Quyết định triển khai |
|---|---|---|
| Active/draft camera độc lập | Chưa có vòng đời lưu nháp không gián đoạn | Bổ sung lưu revision + active pointer trước trải nghiệm đích |
| Xác nhận worker theo revision camera | Cần kiểm tra đầy đủ ý nghĩa trạng thái hiện có | Bổ sung DTO acknowledgment nếu thiếu; không suy từ HTTP 200 |
| Chống ghi đè cấu hình | Chưa có expected_revision trong form hiện tại | API conflict 409 + tải lại/so sánh |
| Model người đa kiến trúc | Registry chung có YOLO; API tiện ích chỉ lọc YOLOX | Dùng registry chung hoặc mở rộng endpoint có thông tin kiến trúc |
| Date range/pagination của face events | Hiện chỉ lọc camera/kind, giới hạn 200 | Bổ sung truy vấn phía server; filter UI phải khớp hợp đồng |
| Acknowledgment cảnh báo | Chưa có | Đưa P2, cần lưu actor/thời điểm/trạng thái riêng |
| Bộ chọn giáo viên/học sinh | Cần xác nhận API danh sách/tìm kiếm/phạm vi quyền | Tái sử dụng API đã có; bổ sung tìm kiếm hạn chế dữ liệu khi cần |
| Health/freshness | Nhiều nguồn trạng thái, chưa có mô hình UI thống nhất | Chuẩn hóa DTO timestamp và ánh xạ thông điệp |
| Đồng bộ bbox/preview | Preview và recognition có nhịp riêng | Hợp đồng frame/session rõ trước khi sửa renderer |
| Tổng quan toàn hệ thống | Chưa có tổng hợp đầy đủ | Chỉ dùng phép tổng hợp hợp lệ; API aggregate là phần bổ sung |

Không đưa vào đợt đầu: đa chi nhánh/tenant, thanh toán, huấn luyện model trên web,
cảnh báo ra dịch vụ ngoài, thay thuật toán nhận diện hoặc tự động thay ngưỡng đã hiệu chuẩn.

## 8. Chia gói công việc có đầu ra và tiêu chí hoàn thành

| Gói | Việc làm | Đầu ra | Nghiệm thu |
|---|---|---|---|
| A — Khảo sát | Chụp các màn chính, desktop/mobile, light/dark; lập ma trận thao tác/API | Audit có ảnh, sitemap, danh sách P0/P1 | Phân biệt điều đã quan sát và giả thuyết; mỗi lỗi có vị trí và cách kiểm tra |
| B — Thiết kế nền | Phác thảo giám sát/camera/model/cảnh báo; chốt token và component | Spec bố cục, bảng token, state matrix | Một hướng nhất quán; đọc tốt tên tiếng Việt dài; không thay logic |
| C — Khung ứng dụng | Navigation, page header, Button/Field/Badge/Dialog/Empty/Error | UI dùng chung và mapping route cũ | Keyboard/focus đầy đủ; không rò màn theo quyền |
| D — Giám sát và camera | Tách trang, wizard, ROI, revision/draft/ack, sửa đồng bộ overlay | Luồng cấu hình và giám sát hoàn chỉnh | Camera có hình; save/apply đúng trạng thái; không render bbox sai frame |
| E — Model và cảnh báo | Hiện YOLO26/YOLOX, wizard upload, timeline ảnh/snapshot | Quản lý model thống nhất và trang cảnh báo | Model đang chạy trùng runtime; 5 frame theo backend; ảnh lỗi không mất event |
| F — Quản lý nghiệp vụ | Hồ sơ, duyệt ảnh, lớp, chọn người, điểm danh | Các vai trò hoàn thành công việc thường ngày | Không nhập UUID; đúng phạm vi dữ liệu; lọc/xuất dữ liệu nhất quán |
| G — Kiểm chứng | Tình huống lỗi, responsive, keyboard, hiệu năng và hồi quy | Bộ ảnh nghiệm thu, test, build, hướng dẫn sử dụng | Không còn P0/P1 trong luồng đã chọn; dữ liệu vận hành không bị mất |

Thứ tự phụ thuộc: A → B → C → D → E → F → G. Hợp đồng API trong D/E phải chốt
trước phần UI phụ thuộc. Không dùng dữ liệu giả để đánh dấu một gói hoàn thành.

Ước lượng để phân chia công việc, chưa phải cam kết lịch: khoảng **14–20 ngày làm
việc cho một lập trình viên**, tùy mức thay đổi active/draft và truy vấn lịch sử.
Bản rút gọn **8–12 ngày** chỉ gồm khung, giám sát, camera, model và cảnh báo với
khả năng API hiện có; phải trình bày trung thực các giới hạn còn lại.

## 9. Kế hoạch thay đổi mã nguồn

Giữ React/Vite và FastAPI hiện tại. Không mặc định chuyển framework/CSS library.
Chỉ thêm primitive UI nếu hành vi phức tạp cần keyboard/focus mà native không đủ.

```text
apps/web/src/
  app/                     khung, menu, ánh xạ trang theo quyền
  ui/                      control dùng chung và trạng thái dữ liệu
  features/monitoring/     chọn camera, video, lớp overlay, sự kiện gần đây
  features/cameras/        danh sách, wizard, các tab cấu hình
  features/alerts/         danh sách, chi tiết, ảnh và trạng thái bằng chứng
  features/models/         model theo vai trò, phiên bản, kiểm tra/áp dụng
  features/registry/       hồ sơ và duyệt ảnh
  features/admin/          lớp/tài khoản và bộ chọn người
  api/                     DTO, ánh xạ lỗi, quản lý request/cancel
  styles/                  token, khung, component, phần đặc thù
```

Các thư mục mới là đề xuất phân tách, không bắt buộc di chuyển hết trong một lần.
Tách component theo trách nhiệm thực tế; giữ lớp API chung; không xây một framework
form/state mới chỉ để phục vụ vài trang.

Backend dự kiến: camera schema/repository/API cho revision, truy vấn model,
face events filter/pagination, runtime acknowledgment. Migration theo thứ tự,
giữ dữ liệu và khả năng chạy với cấu hình hiện có; không reset database/model.

## 10. Kiểm chứng và tiêu chí nghiệm thu cuối

**Khả năng sử dụng:** các chỉ tiêu dưới đây là mục tiêu để đo, chưa phải kết quả đã đạt.

- Người mới hoàn thành thêm camera ở môi trường đã sẵn sàng trong khoảng ba phút
  mà không cần đọc manifest hay hỏi ID. Thử với người đại diện khi có thể.
- Mở giám sát camera mong muốn không quá ba thao tác; nút chính luôn gắn với đối tượng.
- Đổi ngưỡng 5/8 frame và xem đúng phiên bản đã áp dụng, kể cả trường hợp worker offline.
- Lỗi field không xóa form; rời trang có bảo vệ thay đổi chưa lưu.

**Kỹ thuật và nghiệp vụ:**

- Tương thích 360/768/1366/1920 px; không cuộn ngang toàn trang.
- Tương phản mục tiêu WCAG AA; keyboard, focus return, dialog, tooltip và reduced motion.
- Tên Việt dài, nhiều người trong ảnh, camera dọc/ngang và ROI khi resize đều dùng được.
- Kiểm tra quyền ADMIN/TEACHER/STUDENT qua API và UI, không chỉ ẩn menu.
- Không có ảnh tham chiếu, người lạ một/bốn/năm frame, PENDING/UNCERTAIN, nhận ra
  người quen, mất khuôn mặt, reconnect và session mới phải cho đúng hành vi.
- Snapshot PENDING/READY/FAILED/EXPIRED; làm mới lịch sử không tạo cảnh báo trùng.
- Model tải lỗi, sai shape, warmup thất bại, CPU-only; không tuyên bố GPU chỉ vì đã chọn.
- Có kiểm tra cấu hình nháp không áp dụng, conflict, worker chưa xác nhận, rollback nếu có.
- Request có cancel; polling không nhân đôi; event list và collection runtime có giới hạn.
- Chạy frontend typecheck/test/build và backend test cho các API thật sự thay đổi.
- Chụp và kiểm tra trực quan luồng chính ở cả hai theme; build thành công không thay thế bước này.

## 11. Kết quả mong đợi của đợt triển khai đầu

Người vận hành nhìn thấy camera và cảnh báo trước; người cấu hình có luồng thiết
lập rõ ràng, biết điều gì đã lưu và điều gì đang chạy. YOLO26 xuất hiện đúng dưới
vai trò phát hiện người, quy tắc người lạ năm frame và trạng thái snapshot được
giải thích bằng ngôn ngữ sản phẩm. Quản trị, giáo viên và sinh viên có không gian
phù hợp với việc của mình, trong cùng một hệ giao diện nhất quán.
