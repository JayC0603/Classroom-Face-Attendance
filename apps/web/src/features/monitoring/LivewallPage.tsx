import {useEffect, useRef, useState, type CSSProperties} from 'react';
import {listCameras} from '../../api/client';
import type {CameraPublic, RuntimeStatus} from '../../api/contracts';
import {CameraRecognition} from '../cameras/CameraRecognition';
import {BrowserWebcamLive} from '../cameras/BrowserWebcamLive';
import {cameraRuntimeLabel} from './MonitoringPage';

const selectionKey = 'livewall-cameras';
function savedSelection(): string[] | null {
  try {
    const value: unknown = JSON.parse(sessionStorage.getItem(selectionKey) ?? 'null');
    return Array.isArray(value) && value.every(id => typeof id === 'string') ? value.slice(0, 9) : null;
  } catch {return null;}
}

export function LivewallPage({statuses}: {statuses: RuntimeStatus[]}) {
  const [cameras, setCameras] = useState<CameraPublic[]>([]);
  const [selected, setSelected] = useState<string[] | null>(savedSelection);
  const [columns, setColumns] = useState(2);
  const [focused, setFocused] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [fullscreen, setFullscreen] = useState(false);
  const [fullscreenError, setFullscreenError] = useState('');
  const [, tick] = useState(0);
  const wall = useRef<HTMLElement>(null);

  useEffect(() => {
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        const rows = await listCameras();
        if (disposed) return;
        setCameras(rows); setError('');
        setSelected(current => current === null
          ? rows.filter(camera => camera.enabled && camera.lifecycle === 'ACTIVE').slice(0, 4).map(camera => camera.id)
          : current.filter(id => rows.some(camera => camera.id === id)));
        setFocused(current => rows.some(camera => camera.id === current) ? current : null);
      } catch {if (!disposed) setError('Không tải được danh sách camera. Đang thử kết nối lại…');}
      finally {if (!disposed) {setLoading(false); timer = setTimeout(() => void refresh(), 10000);}}
    }
    void refresh();
    const clock = setInterval(() => tick(value => value + 1), 5000);
    const onFullscreen = () => setFullscreen(document.fullscreenElement === wall.current);
    const onKey = (event: KeyboardEvent) => {if (event.key === 'Escape') setFocused(null);};
    document.addEventListener('fullscreenchange', onFullscreen);
    window.addEventListener('keydown', onKey);
    return () => {
      disposed = true; clearTimeout(timer); clearInterval(clock);
      document.removeEventListener('fullscreenchange', onFullscreen);
      window.removeEventListener('keydown', onKey);
    };
  }, []);
  useEffect(() => {
    if (selected !== null) {
      try {sessionStorage.setItem(selectionKey, JSON.stringify(selected));} catch { /* Session storage can be disabled. */ }
    }
  }, [selected]);

  const visible = (selected ?? []).flatMap(id => {
    const camera = cameras.find(item => item.id === id);
    return camera ? [camera] : [];
  });
  function toggleCamera(id: string) {
    setSelected(current => (current ?? []).includes(id) ? current!.filter(item => item !== id) : [...(current ?? []), id].slice(0, 9));
    if (focused === id) setFocused(null);
  }
  async function toggleFullscreen() {
    setFullscreenError('');
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else if (wall.current?.requestFullscreen) await wall.current.requestFullscreen();
      else setFullscreenError('Trình duyệt này chưa hỗ trợ toàn màn hình.');
    } catch {setFullscreenError('Không mở được toàn màn hình. Bạn có thể phóng lớn từng camera.');}
  }

  return <section className="livewall" ref={wall} aria-label="Livewall nhiều camera">
    <div className="livewall-toolbar">
      <div><strong>{visible.length} camera trên Livewall</strong><p>Khung hình và nhận diện cùng thời điểm · tối đa 9 camera</p></div>
      <div className="livewall-actions">
        <label>Bố cục<select value={columns} onChange={event => {setColumns(Number(event.target.value)); setFocused(null);}}><option value={2}>2 cột · khung hình lớn</option><option value={3}>3 cột · xem nhiều hơn</option></select></label>
        <button className="secondary-button" onClick={() => void toggleFullscreen()}>{fullscreen ? 'Thoát toàn màn hình' : 'Toàn màn hình'}</button>
        <a href="#alerts">Xem cảnh báo →</a>
      </div>
    </div>
    {fullscreenError && <p role="alert" className="form-error">{fullscreenError}</p>}
    {error && <p role="alert" className="form-error">{error}</p>}
    {loading ? <p role="status">Đang tải camera…</p> : cameras.length === 0 ? !error && <article className="empty-state"><h2>Chưa có camera để quan sát</h2><p>Thêm camera và áp dụng cấu hình để đưa hình lên Livewall.</p><a className="primary-action" href="#cameras">Thiết lập camera</a></article> : <>
      <details className="livewall-picker" open={visible.length === 0}>
        <summary>Chọn camera hiển thị ({visible.length}/9)</summary>
        <p className="field-hint">Bỏ chọn chỉ đóng ô xem. Camera do worker xử lý vẫn tiếp tục chạy. Webcam trình duyệt sẽ dừng gửi hình khi đóng ô hoặc rời trang.</p>
        <div className="livewall-camera-options">{cameras.map(camera => <label key={camera.id}>
          <input type="checkbox" checked={(selected ?? []).includes(camera.id)} disabled={visible.length >= 9 && !(selected ?? []).includes(camera.id)} onChange={() => toggleCamera(camera.id)}/>
          <span><strong>{camera.name}</strong><small>{!camera.enabled ? 'Đã tắt' : camera.lifecycle !== 'ACTIVE' ? 'Chưa áp dụng' : camera.source_type === 'webcam' ? 'Webcam trình duyệt · cần mở nguồn' : 'Cấu hình đã áp dụng'}</small></span>
        </label>)}</div>
      </details>
      {!visible.length && <article className="empty-state"><h2>Chọn camera để bắt đầu</h2><p>Đánh dấu các camera muốn quan sát trong danh sách phía trên.</p></article>}
      <div className={`livewall-grid livewall-columns-${columns} ${focused ? 'is-focused' : ''}`} style={{'--wall-rows': focused ? 1 : Math.max(1, Math.ceil(visible.length / columns))} as CSSProperties}>
        {visible.map(camera => {
          const runtime = statuses.find(item => item.camera_id === camera.id);
          return <article className="livewall-tile" key={camera.id} hidden={focused !== null && focused !== camera.id} aria-label={`Ô camera ${camera.name}`}>
            <header><h2>{camera.name}</h2><button className="secondary-button" aria-label={`${focused === camera.id ? 'Thu nhỏ' : 'Phóng lớn'} ${camera.name}`} onClick={() => setFocused(focused === camera.id ? null : camera.id)}>{focused === camera.id ? 'Thu nhỏ' : 'Phóng lớn'}</button></header>
            <div className="livewall-status"><span>{cameraRuntimeLabel(camera, runtime)}</span><small>{runtime?.last_frame_at ? `Hình gần nhất ${new Date(runtime.last_frame_at).toLocaleTimeString('vi-VN')}` : 'Chưa có thời gian nhận hình'}</small></div>
            <div className="livewall-feed"><CameraRecognition camera={camera} compact roiPolygon={camera.roi_polygon}/></div>
            {camera.source_type === 'webcam' && camera.enabled && camera.lifecycle === 'ACTIVE' && <details className="livewall-webcam"><summary>Mở nguồn webcam trình duyệt</summary><BrowserWebcamLive camera={camera}/></details>}
            <footer><span>{runtime && runtime.last_frame_at && Date.now() - Date.parse(runtime.last_frame_at) < 15000 ? `${runtime.processed_fps.toFixed(1)} FPS xử lý` : 'Chưa có tốc độ xử lý mới'}</span><a href="#monitoring" onClick={() => sessionStorage.setItem('monitor-camera', camera.id)}>Xem chi tiết →</a></footer>
          </article>;
        })}
      </div>
    </>}
  </section>;
}
