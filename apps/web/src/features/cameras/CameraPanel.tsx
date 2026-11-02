import { FormEvent, useEffect, useState } from "react";

import {
  activateCamera,
  createCamera,
  deleteCamera,
  listCameras,
  testCamera,
  updateCamera
} from "../../api/client";
import type { CameraCodec, CameraPublic, CameraSourceType, RuntimeStatus } from "../../api/contracts";
import { CameraRecognition } from "./CameraRecognition";
import {BrowserWebcamLive} from './BrowserWebcamLive';
import {cameraRuntimeLabel} from '../monitoring/MonitoringPage';

interface CameraPanelProps {
  runtimeStatuses: RuntimeStatus[];
}

export function CameraPanel({ runtimeStatuses }: CameraPanelProps) {
  const [items, setItems] = useState<CameraPublic[]>([]);
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [codec, setCodec] = useState<CameraCodec>("AUTO");
  const [source, setSource] = useState<CameraSourceType>("rtsp");
  const [deviceIndex, setDeviceIndex] = useState("0");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editName, setEditName] = useState("");
  const [editUrl, setEditUrl] = useState("");
  const [editCodec, setEditCodec] = useState<CameraCodec>("AUTO");
  const [editSource, setEditSource] = useState<CameraSourceType>("rtsp");
  const [editDeviceIndex, setEditDeviceIndex] = useState("0");
  const [editRoi, setEditRoi] = useState<Array<[number, number]>>([]);
  const [editDisplayFps, setEditDisplayFps] = useState(15);
  const [editInferenceFps, setEditInferenceFps] = useState(5);
  const [editAttendance, setEditAttendance] = useState(true);
  const [editRetentionDays, setEditRetentionDays] = useState(30);
  const [editUnknownFrames, setEditUnknownFrames] = useState(5);
  const [roiEditing, setRoiEditing] = useState(false);
  const [message, setMessage] = useState("");
  const [startingId, setStartingId] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState('all');
  const [adding, setAdding] = useState(false);
  const [editBaseline, setEditBaseline] = useState('');
  const dirty = editingId !== null && editBaseline !== JSON.stringify([editName, editCodec, editSource, editDeviceIndex, editUrl, editRoi, editDisplayFps, editInferenceFps, editAttendance, editRetentionDays, editUnknownFrames]);
  const visibleItems = items.filter(camera => camera.id === editingId || (
    camera.name.toLocaleLowerCase('vi').includes(search.trim().toLocaleLowerCase('vi'))
    && (filter === 'all' || (filter === 'draft' ? camera.lifecycle === 'DRAFT' : !camera.enabled))
  ));
  async function runCameraAction(cameraId: string, action: () => Promise<unknown>, notice: string) {
    setStartingId(cameraId);
    try {await action(); setMessage(notice); await refresh();}
    catch (reason) {setMessage(reason instanceof Error ? reason.message : 'Không thực hiện được thao tác.');}
    finally {setStartingId(null);}
  }
  useEffect(() => {
    if (!dirty) return;
    const beforeUnload = (event: BeforeUnloadEvent) => {event.preventDefault(); event.returnValue = '';};
    const navigate = (event: MouseEvent) => {
      const link = (event.target as Element).closest('a[href]');
      if (link && !window.confirm('Bạn đang sửa cấu hình. Rời trang và bỏ thay đổi chưa lưu?')) event.preventDefault();
    };
    window.addEventListener('beforeunload', beforeUnload);
    document.addEventListener('click', navigate, true);
    return () => {window.removeEventListener('beforeunload', beforeUnload); document.removeEventListener('click', navigate, true);};
  }, [dirty]);

  async function startCamera(camera: CameraPublic) {
    setStartingId(camera.id);
    try {
      if (!camera.enabled) await updateCamera(camera.id, {enabled:true});
      await activateCamera(camera.id);
      setMessage('Đã bật camera. Đang chờ hình nhận diện…');
      await refresh();
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : 'Không bật được camera.');
    } finally {setStartingId(null);}
  }

  async function refresh() {
    try {
      setItems(await listCameras());
    } catch {
      setMessage("Không tải được danh sách camera.");
    } finally {setLoading(false);}
  }

  useEffect(() => {
    void refresh();
  }, []);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    try {
      await createCamera({
        name,
        source_type: source,
        ...(source === "rtsp" ? { rtsp_url: url } : {}),
        ...(source === "local_webcam" ? { device_index: Number(deviceIndex) } : {}),
        codec,
        enabled: true,
        sampling_fps: 5
      });
      setName("");
      setUrl("");
      setAdding(false);
      setSearch(''); setFilter('all');
      setMessage("Đã lưu camera ở dạng bản nháp. Thông tin xác thực không hiển thị lại.");
      await refresh();
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : 'Không lưu được camera. Kiểm tra nguồn hình rồi thử lại.');
    } finally {setSaving(false);}
  }

  function beginEdit(camera: CameraPublic) {
    if (dirty && !window.confirm('Bạn có thay đổi chưa lưu. Bỏ thay đổi để sửa camera khác?')) return false;
    setEditingId(camera.id);
    setEditName(camera.name);
    setEditCodec(camera.codec);
    setEditSource(camera.source_type);
    setEditDeviceIndex(String(camera.device_index ?? 0));
    setEditUrl("");
    setEditRoi(camera.roi_polygon ?? []);
    setEditDisplayFps(camera.display_fps ?? 15);
    setEditInferenceFps(camera.sampling_fps ?? 5);
    setEditAttendance(camera.attendance_enabled ?? true);
    setEditRetentionDays(camera.evidence_retention_days ?? 30);
    setEditUnknownFrames(camera.unknown_alert_frames ?? 5);
    setRoiEditing(false);
    setEditBaseline(JSON.stringify([camera.name, camera.codec, camera.source_type, String(camera.device_index ?? 0), '', camera.roi_polygon ?? [], camera.display_fps ?? 15, camera.sampling_fps ?? 5, camera.attendance_enabled ?? true, camera.evidence_retention_days ?? 30, camera.unknown_alert_frames ?? 5]));
    return true;
  }

  function beginRoiEdit(camera: CameraPublic) {
    if (beginEdit(camera)) setRoiEditing(true);
  }

  async function saveEdit(camera: CameraPublic) {
    if (!Number.isInteger(editUnknownFrames) || editUnknownFrames < 2 || editUnknownFrames > 60) {
      setMessage('Số frame người lạ liên tiếp phải là số nguyên từ 2 đến 60.'); return;
    }
    const update: Partial<{
      name: string;
      rtsp_url: string;
      codec: CameraCodec;
      source_type: CameraSourceType;
      device_index: number;
      display_fps: number;
      sampling_fps: number;
      roi_polygon: Array<[number, number]>;
      attendance_enabled: boolean;
      evidence_retention_days: number;
      unknown_alert_frames: number;
    }> = { name: editName, codec: editCodec, roi_polygon: editRoi, display_fps: editDisplayFps, sampling_fps: editInferenceFps,
      attendance_enabled: editAttendance, evidence_retention_days: editRetentionDays, unknown_alert_frames: editUnknownFrames };
    if (editRoi.length > 0 && editRoi.length < 3) { setMessage("ROI cần ít nhất 3 điểm hoặc xóa toàn bộ điểm để theo dõi toàn khung hình."); return; }
    if (editSource !== camera.source_type) update.source_type = editSource;
    if (editSource === "local_webcam") {
      const index = Number(editDeviceIndex);
      if (!editDeviceIndex.trim() || !Number.isInteger(index) || index < 0 || index > 63) {
        setMessage("Số thiết bị webcam phải là số nguyên từ 0 đến 63."); return;
      }
      if (editSource !== camera.source_type || index !== camera.device_index) update.device_index = index;
    }
    if (editSource === "rtsp" && editUrl) update.rtsp_url = editUrl;
    if (editSource === "rtsp" && camera.source_type !== "rtsp" && !editUrl) {
      setMessage("Nhập RTSP URL khi chuyển từ Webcam sang RTSP."); return;
    }
    try {
      setSaving(true);
      await updateCamera(camera.id, {...update, expected_revision: camera.revision});
      setEditUrl("");
      setEditingId(null);
      setMessage("Đã lưu cấu hình camera ở dạng bản nháp. Hãy áp dụng để gửi phiên bản mới tới worker.");
      await refresh();
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "Camera update failed");
    } finally {setSaving(false);}
  }

  return (
    <section className="camera-workspace" aria-labelledby="camera-heading">
      <div className="section-heading camera-section-heading">
        <div><p className="eyebrow">NGUỒN HÌNH & CHÍNH SÁCH</p><h2 id="camera-heading">Danh sách camera</h2></div>
        <div className="camera-heading-actions">
          <a href="#monitoring">Mở giám sát trực tiếp →</a>
          <button type="button" disabled={editingId !== null} onClick={() => void refresh()}>Tải lại danh sách</button>
        </div>
      </div>
      <div className="usability-guide"><strong>Thiết lập camera</strong><span>1. Thêm nguồn hình</span><span>2. Chỉnh vùng & cảnh báo</span><span>3. Áp dụng và mở giám sát</span></div>
      <details className="camera-add-panel" open={adding} onToggle={event => setAdding(event.currentTarget.open)}>
        <summary>Thêm camera</summary>
        <form className="camera-form" onSubmit={submit}>
          <label>Tên camera<input placeholder="Ví dụ: Cửa vào phòng A101" value={name} onChange={(event) => setName(event.target.value)} required /></label>
          <label>Nguồn<select value={source} onChange={event => { setSource(event.target.value as CameraSourceType); setUrl(""); }}>
            <option value="rtsp">RTSP</option>
            <option value="local_webcam">Webcam trực tiếp trên máy worker</option>
            <option value="webcam">Webcam trình duyệt</option>
          </select></label>
          {source === "rtsp" ? <>
            <label>Địa chỉ RTSP<input type="password" value={url} onChange={(event) => setUrl(event.target.value)} required autoComplete="new-password" /></label>
            <label>Bộ giải mã<select value={codec} onChange={(event) => setCodec(event.target.value as CameraCodec)}>
              <option>AUTO</option><option>H264</option><option>H265</option>
            </select></label>
          </> : source === "local_webcam" ? <>
            <label>Số thiết bị webcam<input type="number" required min={0} max={63} step={1} value={deviceIndex} onChange={event => setDeviceIndex(event.target.value)} /></label>
            <p>Thiết bị 0 thường là webcam mặc định trên máy chạy worker. Camera mở khi được kích hoạt.</p>
          </> : <p>Webcam sẽ mở trong trình duyệt khi bạn kích hoạt camera và bấm Bắt đầu realtime.</p>}
          <button type="submit" disabled={saving}>{saving ? 'Đang lưu…' : 'Lưu camera'}</button>
        </form>
      </details>
      {message && <div className="operation-notice" role="status"><span>{message}</span><button type="button" aria-label="Ẩn thông báo" onClick={() => setMessage('')}>×</button></div>}
      <div className="filter-toolbar camera-list-filters"><label>Tìm camera<input type="search" placeholder="Nhập tên camera…" value={search} onChange={e => setSearch(e.target.value)} /></label><label>Hiển thị<select value={filter} onChange={e => setFilter(e.target.value)}><option value="all">Tất cả camera</option><option value="draft">Chưa áp dụng cấu hình</option><option value="disabled">Đã tắt</option></select></label><span className="field-hint" role="status">{loading ? 'Đang tải…' : `${visibleItems.length} / ${items.length} camera`}</span></div>
      {!loading && items.length === 0 && <div className="empty-state"><h3>Thêm camera đầu tiên</h3><p>Chọn webcam hoặc camera mạng để bắt đầu. Sau khi lưu, bạn có thể chỉnh vùng giám sát và quy tắc cảnh báo.</p><button className="button-primary" onClick={() => setAdding(true)}>Thiết lập camera mới</button></div>}
      {!loading && items.length > 0 && !visibleItems.length && <div className="empty-state"><h3>Không tìm thấy camera phù hợp</h3><button onClick={() => {setSearch('');setFilter('all');}}>Xóa bộ lọc</button></div>}
      <div className="grid">
        {visibleItems.map((camera) => {
          const runtime = runtimeStatuses.find((item) => item.camera_id === camera.id);
          const editing = editingId === camera.id;
          return (
            <article className="camera-card" key={camera.id}>
              {editing ? (
                <>
                  <h3>Cấu hình · {camera.name}</h3>
                  {camera.source_type === 'webcam' && <BrowserWebcamLive camera={camera}/>}
                  <p className="configuration-warning">Lưu bản nháp sẽ tạm dừng cấu hình hiện tại của camera. Kiểm tra các thay đổi rồi áp dụng lại để tiếp tục nhận diện.</p>
                  <fieldset className="configuration-section"><legend>Nguồn hình</legend><div className="configuration-fields">
                  <label>Tên<input value={editName} onChange={(event) => setEditName(event.target.value)} /></label>
                  <label>Nguồn camera<select value={editSource}
                    onChange={event => { setEditSource(event.target.value as CameraSourceType); setEditUrl(""); }}>
                    <option value="rtsp">RTSP</option>
                    <option value="local_webcam">Webcam trực tiếp trên máy worker</option>
                    <option value="webcam">Webcam trình duyệt</option>
                  </select></label>
                  {editSource === "local_webcam" && <label>Số thiết bị webcam (sửa)
                    <input type="number" min={0} max={63} step={1} value={editDeviceIndex}
                      onChange={event => setEditDeviceIndex(event.target.value)} />
                  </label>}
                  {editSource === "rtsp" && <>
                  <label>
                    Địa chỉ RTSP mới (không bắt buộc)
                    <input
                      type="password"
                      value={editUrl}
                      onChange={(event) => setEditUrl(event.target.value)}
                      autoComplete="new-password"
                    />
                  </label>
                  <label>
                    Bộ giải mã
                    <select
                      value={editCodec}
                      onChange={(event) => setEditCodec(event.target.value as CameraCodec)}
                    >
                      <option>AUTO</option><option>H264</option><option>H265</option>
                    </select>
                  </label></>}
                  </div></fieldset>
                  <fieldset className="configuration-section"><legend>Nhận diện & ảnh minh chứng</legend><div className="configuration-fields">
                  <label>FPS hiển thị (1–30)<input type="number" min={1} max={30} step={1} value={editDisplayFps}
                    onChange={event => setEditDisplayFps(Number(event.target.value))} /></label>
                  <label>FPS suy luận (sampling, 1–30)<input type="number" min={1} max={30} step={1} value={editInferenceFps}
                    onChange={event => setEditInferenceFps(Number(event.target.value))} /></label>
                  <label className="inline-toggle"><input type="checkbox" checked={editAttendance} onChange={event=>setEditAttendance(event.target.checked)} /> Điểm danh tự động sau khi xác minh ổn định</label>
                  <label>Giữ ảnh minh chứng (ngày)<input type="number" min={1} max={3650} step={1} value={editRetentionDays}
                    onChange={event=>setEditRetentionDays(Number(event.target.value))} /></label>
                  <label>Số frame người lạ liên tiếp trước khi cảnh báo<input type="number" min={2} max={60} step={1} value={editUnknownFrames}
                    onChange={event=>setEditUnknownFrames(Number(event.target.value))} /></label>
                  <p className="field-hint">Chụp mặt khi mới xuất hiện trong ROI. Chỉ cảnh báo sau đủ frame nhận diện liên tiếp không khớp; nhận ra người quen hoặc mất khuôn mặt sẽ đếm lại.</p>
                  </div></fieldset>
                  <section className="camera-roi-editor" aria-labelledby={`roi-heading-${camera.id}`}>
                    <div className="camera-roi-heading">
                      <div>
                        <p className="eyebrow">KHU VỰC ĐƯỢC THEO DÕI</p>
                        <h3 id={`roi-heading-${camera.id}`}>Vùng giám sát (ROI)</h3>
                      </div>
                      <span className="roi-point-count">{editRoi.length} điểm</span>
                    </div>
                    <p>ROI đang áp dụng: {(camera.roi_polygon?.length ?? 0) >= 3 ? `polygon ${camera.roi_polygon?.length} điểm` : "toàn khung hình"}. Bấm lên ảnh để thêm đỉnh đa giác; cần ít nhất 3 điểm.</p>
                    <div className="camera-actions roi-actions">
                      <button className="button-primary" type="button" onClick={() => setRoiEditing(value => !value)}>
                        {roiEditing ? "Dừng vẽ" : "Vẽ vùng trên camera"}
                      </button>
                      <button type="button" onClick={() => setEditRoi(points => points.slice(0, -1))} disabled={!editRoi.length}>Lùi điểm</button>
                      <button type="button" onClick={() => setEditRoi([])}>Xóa vùng · theo dõi toàn khung hình</button>
                    </div>
                    <p className="field-hint" aria-live="polite">{editRoi.length < 3 ? "Thêm ít nhất 3 điểm trên ảnh để tạo polygon. Lưu thay đổi sẽ lưu thành bản nháp; cần áp dụng cấu hình để gửi tới worker." : `Polygon có ${editRoi.length} điểm. Người được tính khi đáy khung bao đi vào vùng này.`}</p>
                    <CameraRecognition camera={camera} roiPolygon={editRoi} onRoiChange={setEditRoi} roiEditing={roiEditing} />
                  </section>
                  <div className="camera-actions configuration-footer">
                    <span className="field-hint">{dirty ? 'Có thay đổi chưa lưu' : 'Chưa có thay đổi'}</span>
                    <button className="button-primary" disabled={saving} onClick={() => void saveEdit(camera)}>{saving ? 'Đang lưu…' : 'Lưu thay đổi (bản nháp)'}</button>
                    <button disabled={saving} onClick={() => {if (!dirty || window.confirm('Bỏ các thay đổi chưa lưu của camera này?')) {setEditingId(null); setEditUrl('');}}}>Hủy</button>
                  </div>
                </>
              ) : (
                <>
                  <h3>{camera.name}</h3>
                  <p>{camera.source_type === "webcam" ? "Webcam trình duyệt" :
                    camera.source_type === "local_webcam" ? `Webcam trực tiếp trên máy worker · Thiết bị ${camera.device_index ?? 0}` : camera.rtsp_url}</p>
                  <p className="status-chip">{cameraRuntimeLabel(camera, runtime)}</p>
                  <p>{camera.lifecycle === 'ACTIVE' ? 'Đã gửi cấu hình' : 'Bản nháp chưa áp dụng'} · {runtime?.active_config_revision === camera.revision && camera.lifecycle === 'ACTIVE' ? 'Worker đã nhận phiên bản này' : 'Chưa có xác nhận phiên bản từ worker'}</p>
                  <p>{(camera.roi_polygon?.length ?? 0) >= 3 ? `ROI polygon · ${camera.roi_polygon?.length ?? 0} điểm` : "ROI: toàn khung hình"} ·
                    {(camera.attendance_enabled ?? true) ? " điểm danh bật" : " điểm danh tắt"} · giữ ảnh {camera.evidence_retention_days ?? 30} ngày</p>
                  <details className="runtime-details"><summary>Chẩn đoán camera</summary><p>
                    Runtime: {camera.enabled ? (runtime?.camera_state ?? "NOT REPORTED") : "DISABLED"}
                    {runtime ? ` · hiển thị tối đa ${camera.display_fps ?? 15} FPS · nhận ${runtime.input_fps.toFixed(1)} FPS · suy luận ${runtime.processed_fps.toFixed(1)} FPS · ${runtime.active_tracks} track · reconnects ${runtime.reconnect_count}` : ""}
                  </p>
                  {runtime && <p>Model: {runtime.person_detector_runtime?.health ?? "CHƯA CẤU HÌNH"}
                    {runtime.person_detector_runtime?.model_version ? ` · ${runtime.person_detector_runtime.model_version}` : ""}
                    {runtime.latest_processing_latency_ms != null ? ` · trễ xử lý ${runtime.latest_processing_latency_ms.toFixed(0)} ms` : ""}
                    {runtime.person_detector_runtime?.latest_latency_ms != null ? ` · model ${runtime.person_detector_runtime.latest_latency_ms.toFixed(0)} ms` : ""}
                  </p>}
                  {runtime?.error_category && runtime.error_category !== "PERSON_DETECTOR_NOT_CONFIGURED" && <p>Error: {runtime.error_category}</p>}
                  {runtime?.error_category === "PERSON_DETECTOR_NOT_CONFIGURED" && <p>Nguồn hình đang cấp cho bộ so sánh YuNet + SFace bên dưới.</p>}
                  </details>
                  <div className="camera-actions">
                    {camera.source_type !== 'webcam' && (!camera.enabled || camera.lifecycle !== 'ACTIVE') &&
                      <button className="button-primary" type="button" disabled={startingId !== null} onClick={() => void startCamera(camera)}>
                        {startingId === camera.id ? 'Đang bật camera…' : 'Bật camera và nhận diện'}
                      </button>}
                    <button onClick={() => beginEdit(camera)}>Chỉnh sửa</button>
                    <button onClick={() => beginRoiEdit(camera)}>Thiết lập vùng ROI</button>
                    <button disabled={startingId !== null} onClick={() => void runCameraAction(camera.id, () => updateCamera(camera.id, {enabled: !camera.enabled, expected_revision: camera.revision}), 'Đã cập nhật trạng thái camera.')}>
                      {camera.enabled ? "Tắt camera" : "Bật camera"}
                    </button>
                    {camera.source_type === "rtsp" && <button disabled={startingId !== null} onClick={() => void runCameraAction(camera.id, async () => {const result = await testCamera(camera.id); if (result.status !== 'CONNECTED') throw new Error(`Chưa kết nối được camera: ${result.error_category ?? result.status}`);}, 'Đã kết nối được camera.')}>
                      Kiểm tra kết nối
                    </button>}
                    <button className={camera.source_type === 'webcam' && camera.lifecycle !== 'ACTIVE' ? 'button-primary' : undefined} disabled={camera.lifecycle === "ACTIVE" || startingId !== null}
                      onClick={() => void runCameraAction(camera.id, () => activateCamera(camera.id), 'Đã gửi cấu hình. Đang chờ worker xác nhận phiên bản.')}>
                      Áp dụng cấu hình
                    </button>
                    <button className="danger-button" disabled={startingId !== null || saving} onClick={() => {
                      if (window.confirm(`Xóa camera “${camera.name}”? Nguồn hình sẽ dừng. Lịch sử nhận diện và ảnh minh chứng đã ghi vẫn được giữ theo thời hạn lưu.`)) {
                        void runCameraAction(camera.id, () => deleteCamera(camera.id, camera.revision), 'Đã xóa camera.');
                      }
                    }}>Xóa camera</button>
                  </div>
                  {camera.enabled && camera.lifecycle === 'ACTIVE' && <a className="camera-monitor-link" href="#monitoring" onClick={() => sessionStorage.setItem('monitor-camera', camera.id)}>Xem camera này trực tiếp →</a>}
                </>
              )}
            </article>
          );
        })}
      </div>
    </section>
  );
}
