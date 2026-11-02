import {useEffect, useState} from 'react';
import {listCameras} from '../../api/client';
import type {CameraPublic} from '../../api/contracts';
import {FaceEventsPanel} from '../history/FaceEventsPanel';

export function AlertsPage() {
  const [cameras, setCameras] = useState<CameraPublic[]>([]);
  const [cameraId, setCameraId] = useState('');
  const [kind, setKind] = useState('UNKNOWN_ALERT');
  const [start, setStart] = useState('');
  const [end, setEnd] = useState('');
  const [error, setError] = useState('');
  useEffect(() => {let alive = true; listCameras().then(rows => {if (alive) setCameras(rows);}).catch(() => {if (alive) setError('Không tải được bộ lọc camera.');}); return () => {alive = false;};}, []);
  return <section className="alerts-workspace">
    <div className="filter-toolbar"><label>Camera<select value={cameraId} onChange={e => setCameraId(e.target.value)}><option value="">Tất cả camera</option>{cameras.map(camera => <option key={camera.id} value={camera.id}>{camera.name}</option>)}</select></label><label>Loại sự kiện<select value={kind} onChange={e => setKind(e.target.value)}><option value="UNKNOWN_ALERT">Cảnh báo người lạ</option><option value="FACE_CAPTURED">Ảnh khuôn mặt mới</option><option value="">Tất cả sự kiện</option></select></label><span className="field-hint">Tự cập nhật mỗi 3 giây · tối đa 50 sự kiện mỗi trang</span></div>
    {error && <p role="alert">{error}</p>}
    <div className="filter-toolbar"><label>Từ ngày<input type="date" value={start} onChange={e => setStart(e.target.value)}/></label><label>Đến ngày<input type="date" value={end} min={start} onChange={e => setEnd(e.target.value)}/></label><span className="field-hint">Theo giờ Việt Nam · 50 sự kiện mỗi trang</span></div>
    {start && end && start > end ? <p role="alert">Ngày kết thúc phải từ ngày bắt đầu trở đi.</p> : <FaceEventsPanel cameraId={cameraId || undefined} kind={kind || undefined} start={start} end={end}/>}
  </section>;
}
