# Face Recognition Platform

Hệ thống quản lý camera, hồ sơ khuôn mặt và điểm danh cho lớp học. Giao diện web dùng React; API dùng FastAPI; PostgreSQL lưu dữ liệu nghiệp vụ; MinIO lưu model và ảnh riêng tư; worker xử lý camera và model được gán.

## Chạy trên Windows

Cài đặt Python 3.11 trở lên, Node.js 20.19 trở lên (hoặc 22.12 trở lên), npm và Docker Desktop có Docker Compose. Mở PowerShell tại thư mục dự án:

```powershell
.\scripts\setup-local.ps1 -DownloadFaceModels
.\scripts\start-local.ps1
```

Lệnh setup tạo `.env` với khóa ngẫu nhiên nếu file chưa có, tạo môi trường Python, cài thư viện Python và web, rồi tải YuNet/SFace chính thức và kiểm tra SHA-256. File `.env` có thông tin đăng nhập cục bộ; script không ghi đè file đã tồn tại và Git bỏ qua file này. `data/` chứa model và dữ liệu cục bộ cũng không được đưa lên Git.

`start-local.ps1` khởi động PostgreSQL, Redis, MinIO, API và web trong các cửa sổ riêng. Truy cập:

- Web: <http://127.0.0.1:5173>
- API health: <http://127.0.0.1:8000/health>
- MinIO console: <http://127.0.0.1:59001> (thông tin đăng nhập nằm trong `.env`)

Để chạy worker cùng lúc, cài GStreamer và Python GI bindings theo [hướng dẫn worker](infrastructure/local/README.md), sau đó chạy:

```powershell
.\scripts\start-local.ps1 -WithWorker
```

Hoặc chạy riêng các tiến trình bằng `scripts/run-api.ps1`, `scripts/run-web.ps1` và `scripts/run-worker.ps1`. Dừng API, web và worker bằng `Ctrl+C` trong cửa sổ tương ứng; dừng dịch vụ Docker bằng:

```powershell
.\scripts\stop-local.ps1
```

> Lệnh stop giữ nguyên volume PostgreSQL, Redis và MinIO. Không dùng `docker compose down -v` nếu cần giữ dữ liệu.

## Tạo tài khoản quản trị

Không có tài khoản hay mật khẩu mặc định. Sau khi dịch vụ cơ sở dữ liệu chạy, tạo admin bằng lệnh tương tác:

```powershell
.\.venv\Scripts\python.exe -m control_api.auth_cli
```

Nhập email và mật khẩu admin khi được hỏi. Admin tạo tài khoản giáo viên/học sinh, lớp học và phân quyền; giáo viên quản lý lớp được giao; học sinh đăng ký ảnh và xem điểm danh của mình. Ảnh đăng ký chờ giáo viên hoặc admin duyệt trước khi được dùng để so sánh.

## Camera và nhận diện

Thêm camera trong mục **Cameras** với một trong ba nguồn: RTSP, webcam trên máy worker hoặc webcam trình duyệt. Thiết lập ROI Polygon trên khung hình để giới hạn vùng theo dõi. API cần chạy một tiến trình để giữ session webcam trình duyệt.

YuNet/SFace được dùng để phát hiện và so sánh mặt với ảnh đăng ký đã duyệt. Chưa có ảnh tham chiếu vẫn có thể phát hiện mặt và vẽ bbox; danh tính chưa thể xác minh. Trong **Models**, tải model Person Detection phù hợp để bật bbox người và tracking. Model YOLOX, SCRFD và AdaFace do người vận hành cung cấp; repo không chứa model huấn luyện hoặc thông tin camera thật.

Worker cần GStreamer và Python GI bindings cho các nguồn video được hỗ trợ. Camera thực tế và tốc độ xử lý phụ thuộc model, thiết bị, cấu hình sampling FPS và nguồn video. Kết quả test tích hợp không khẳng định độ chính xác thực tế của model.

## Kiểm tra trước khi push

Sau khi chạy setup, thực hiện đầy đủ kiểm tra backend, worker và web:

```powershell
.\scripts\verify-local.ps1
```

Lệnh này chạy pytest, Ruff, mypy, web typecheck, test và production build. Có thể chạy riêng frontend trong `apps/web` bằng `npm run dev`, `npm run test` hoặc `npm run build`.

Trước khi commit, xem lại `git status --short` và chỉ thêm file nguồn, cấu hình mẫu, migration, tài liệu và skill dự án cần thiết. Không thêm `.env`, `apps/web/.env.local`, `data/`, model người dùng, ảnh khuôn mặt, log hay file tạm. Không commit mật khẩu thật, token, URL RTSP có thông tin đăng nhập hoặc dữ liệu sinh viên.

## Cấu trúc

- `apps/control-api`: API quản trị, xác thực, hồ sơ khuôn mặt và điểm danh.
- `apps/web`: giao diện Admin, giáo viên, học sinh, camera, model và lịch sử.
- `workers/vision-worker`: đồng bộ cấu hình, đọc camera, phát hiện người, tracking và pipeline model.
- `packages/python`: domain và hợp đồng dùng chung.
- `infrastructure/local`: Compose cho PostgreSQL, Redis, MinIO và khởi tạo bucket.
- `docs/`: thiết kế, giới hạn vận hành, quyền riêng tư, migration và nghiệm thu.

Xem [hướng dẫn dịch vụ local](infrastructure/local/README.md), [vận hành và bảo mật](docs/security/role-based-attendance-operations.md), [bằng chứng so sánh YuNet/SFace](docs/acceptance/2026-10-19-opencv-face-comparison.md) và [model manifests](model-manifests/examples/README.md) để biết thêm chi tiết.
