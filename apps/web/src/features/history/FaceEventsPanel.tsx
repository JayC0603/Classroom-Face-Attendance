import {useEffect, useState} from 'react';
import {apiFetch} from '../../api/client';
import type {FacePresenceEvent} from '../../api/simpleRecognition';

export function FaceEventsPanel({cameraId, kind, compact = false, start, end}: {cameraId?: string; kind?: string; compact?: boolean; start?: string; end?: string}) {
  const [events, setEvents] = useState<FacePresenceEvent[]>([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [offset, setOffset] = useState(0);
  useEffect(() => setOffset(0), [cameraId, kind, start, end]);
  useEffect(() => {
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    setLoading(true);
    setEvents([]);
    async function load() {
      try {
        const params = new URLSearchParams();
        if (cameraId) params.set('camera_id', cameraId);
        if (kind) params.set('kind', kind);
        if (start) params.set('start', start);
        if (end) params.set('end', end);
        if (!compact) {params.set('offset', String(offset)); params.set('limit', '50');}
        const query = params.size ? `?${params}` : '';
        const response = await apiFetch(`/api/v1/recognition/face-events${query}`, {
          signal: controller.signal, cache: 'no-store'
        });
        if (!response.ok) throw new Error('Không tải được ảnh vào và cảnh báo.');
        const rows: FacePresenceEvent[] = await response.json();
        if (!disposed) {setEvents(rows); setError('');}
      } catch (reason) {
        if (!disposed) setError(reason instanceof Error ? reason.message : 'Không tải được cảnh báo.');
      } finally {
        if (!disposed) {setLoading(false); timer = setTimeout(() => void load(), 3000);}
      }
    }
    void load();
    return () => {disposed = true; controller.abort(); clearTimeout(timer);};
  }, [cameraId, kind, start, end, compact, offset]);
  return <section aria-label="Ảnh vào và cảnh báo người lạ">
    {!compact && <p>Ảnh khuôn mặt được chụp khi mới xuất hiện trong ROI. Cảnh báo người lạ chỉ được ghi sau đủ số frame liên tiếp đã cấu hình. Đây là sự kiện giám sát, không phải kết quả điểm danh.</p>}
    {error && <p role="alert">{error}</p>}
    {loading && <p role="status">Đang tải ảnh và cảnh báo…</p>}
    {!loading && !error && events.length === 0 && <p>Chưa có ảnh vào hoặc cảnh báo.</p>}
    <div className="history-list">{(compact ? events.slice(0, 8) : events).map(event => <article className={`history-event ${event.kind === 'UNKNOWN_ALERT' ? 'event-alert' : ''}`} key={event.id}>
      {event.evidence_status === 'READY' && event.evidence_key
        ? <img src={`/api/v1/recognition/evidence/${encodeURIComponent(event.evidence_key)}`} alt={event.kind === 'UNKNOWN_ALERT' ? 'Snapshot cảnh báo người lạ' : 'Khuôn mặt khi vào ROI'} loading="lazy" />
        : <p>{event.evidence_status === 'PENDING' ? 'Đang lưu ảnh…' : event.evidence_status === 'EXPIRED' ? 'Ảnh đã hết hạn lưu.' : 'Không lưu được ảnh; sự kiện vẫn được ghi nhận.'}</p>}
      <div>
        <h3>{event.kind === 'UNKNOWN_ALERT' ? 'Cảnh báo người lạ' : 'Khuôn mặt mới vào ROI'}</h3>
        <p>{event.camera_name} · {new Date(event.recognized_at).toLocaleString('vi-VN')}</p>
        {event.kind === 'UNKNOWN_ALERT' && <p>{event.consecutive_unknown} frame liên tiếp không khớp ảnh đăng ký.</p>}
      </div>
    </article>)}</div>
    {!compact && <div className="camera-actions"><button disabled={loading || offset === 0} onClick={() => setOffset(value => Math.max(0, value - 50))}>Trang trước</button><span>Trang {offset / 50 + 1}</span><button disabled={loading || events.length < 50} onClick={() => setOffset(value => value + 50)}>Trang sau</button></div>}
  </section>;
}
