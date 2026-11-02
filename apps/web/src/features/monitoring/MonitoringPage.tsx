import {useEffect, useState} from 'react';
import {listCameras} from '../../api/client';
import type {CameraPublic, RuntimeStatus} from '../../api/contracts';
import {BrowserWebcamLive} from '../cameras/BrowserWebcamLive';
import {CameraRecognition} from '../cameras/CameraRecognition';
import {FaceEventsPanel} from '../history/FaceEventsPanel';

export function cameraRuntimeLabel(camera: CameraPublic, runtime?: RuntimeStatus): string {
  if (!camera.enabled) return 'Đã tắt';
  if (camera.lifecycle !== 'ACTIVE') return 'Chưa áp dụng cấu hình';
  if (!runtime) return 'Chưa nhận trạng thái worker';
  if (!runtime.last_frame_at || Date.now() - Date.parse(runtime.last_frame_at) > 15000) return 'Chưa có hình mới';
  if (runtime.active_config_revision !== camera.revision) return 'Đang chờ phiên bản cấu hình';
  return runtime.camera_state === 'CONNECTED' ? 'Đang nhận hình' : 'Kết nối cần kiểm tra';
}

export function MonitoringPage({statuses, overview = false}: {statuses: RuntimeStatus[]; overview?: boolean}) {
  const [cameras, setCameras] = useState<CameraPublic[]>([]);
  const [selected, setSelected] = useState(() => sessionStorage.getItem('monitor-camera') ?? '');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [, tick] = useState(0);
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        const rows = await listCameras();
        if (alive) {
          setCameras(rows); setError('');
          setSelected(current => rows.some(c => c.id === current) ? current : rows.find(c => c.enabled && c.lifecycle === 'ACTIVE')?.id ?? rows[0]?.id ?? '');
        }
      } catch {if (alive) setError('Không tải được camera. Đang thử kết nối lại…');}
      finally {if (alive) {setLoading(false); timer = setTimeout(() => void refresh(), 10000);}}
    }
    void refresh();
    const clock = setInterval(() => tick(value => value + 1), 5000);
    return () => {alive = false; clearTimeout(timer); clearInterval(clock);};
  }, []);
  const camera = cameras.find(item => item.id === selected);
  const runtime = statuses.find(item => item.camera_id === selected);
  return <section className="operations-workspace">
    {error && <p className="form-error" role="alert">{error}</p>}
    {loading ? <p role="status">Đang tải camera…</p> : !cameras.length ? <article className="empty-state"><h2>Bắt đầu với camera đầu tiên</h2><p>Thêm nguồn hình, kiểm tra kết nối rồi áp dụng cấu hình để bắt đầu giám sát.</p><a href="#cameras" className="primary-action">Thiết lập camera</a></article> : <>
      {overview && <div className="operations-summary"><div><span>Camera đã cấu hình</span><strong>{cameras.length}</strong></div><div><span>Cấu hình đã kích hoạt</span><strong>{cameras.filter(c => c.enabled && c.lifecycle === 'ACTIVE').length}</strong></div><div><span>Cần áp dụng cấu hình</span><strong>{cameras.filter(c => c.lifecycle === 'DRAFT').length}</strong></div></div>}
      <div className="operations-layout"><div className="operations-primary">
        <div className="monitor-toolbar"><label>Camera đang xem<select value={selected} onChange={event => setSelected(event.target.value)}>{cameras.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label><a href="#cameras">Cấu hình camera →</a></div>
        {camera && <article className="monitor-stage"><div className="monitor-status"><span className="status-chip">{cameraRuntimeLabel(camera, runtime)}</span><span>{runtime?.last_frame_at ? `Hình gần nhất: ${new Date(runtime.last_frame_at).toLocaleTimeString('vi-VN')}` : 'Chưa có thời gian nhận hình'}</span></div>{camera.source_type === 'webcam' && <BrowserWebcamLive camera={camera}/>}<CameraRecognition key={camera.id} camera={camera} roiPolygon={camera.roi_polygon}/><details className="runtime-details"><summary>Thông số vận hành</summary><dl><dt>Tốc độ xử lý</dt><dd>{runtime ? `${runtime.processed_fps.toFixed(1)} FPS` : 'Chưa có dữ liệu'}</dd><dt>Model người</dt><dd>{runtime?.person_detector_runtime?.model_version ?? 'Chưa có báo cáo'}</dd><dt>Cảnh báo người lạ</dt><dd>{camera.unknown_alert_frames ?? 5} frame liên tiếp</dd><dt>Lưu ảnh minh chứng</dt><dd>{camera.evidence_retention_days ?? 30} ngày</dd></dl></details></article>}
      </div><aside className="operations-events"><div className="section-heading"><h2>Sự kiện camera</h2><a href="#alerts">Xem tất cả</a></div>{camera && <FaceEventsPanel cameraId={camera.id} compact/>}</aside></div>
    </>}
  </section>;
}
