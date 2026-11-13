import {useEffect, useState, type CSSProperties} from 'react';
import type {CameraPublic} from '../../api/contracts';
import {recognizeCamera, type CameraRecognitionResult} from '../../api/simpleRecognition';

const stateLabels: Record<string, string> = {
  NO_FACE: 'Chưa thấy khuôn mặt đủ rõ trong camera.',
  WAITING_FOR_FRAME: 'Đang chờ hình camera để so sánh…',
  NO_REFERENCE_IMAGES: 'Chưa có ảnh đăng ký dùng được. Thêm ảnh rõ một khuôn mặt ở phần hồ sơ.',
  RECOGNITION_BUSY: 'Đang xử lý khuôn mặt…',
  MODELS_MISSING: 'Chưa tải đủ model YuNet và SFace.',
};

export function CameraRecognition({camera, roiPolygon, onRoiChange, roiEditing = false, compact = false}: {
  camera: CameraPublic; roiPolygon?: Array<[number, number]>; onRoiChange?: (points: Array<[number, number]>) => void; roiEditing?: boolean; compact?: boolean;
}) {
  const [result, setResult] = useState<CameraRecognitionResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [frameRatio, setFrameRatio] = useState(16 / 9);
  const active = camera.enabled && camera.lifecycle === 'ACTIVE';

  useEffect(() => {
    setResult(null); setError(null);
    if (!active) return;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let expiry: ReturnType<typeof setTimeout> | undefined;
    let controller: AbortController | null = null;
    async function poll() {
      if (disposed) return;
      const started = performance.now();
      if (document.visibilityState === 'hidden') {
        setResult(null);
        timer = setTimeout(() => void poll(), 1000);
        return;
      }
      controller = new AbortController();
      const timeout = setTimeout(() => controller?.abort(), 2000);
      try {
        const next = await recognizeCamera(camera.id, camera.revision, controller.signal, true);
        if (!disposed) {
          clearTimeout(expiry);
          setResult(next); setError(null);
          if (next.preview) expiry = setTimeout(() => setResult(null), Math.min(2500, Math.max(1, next.preview_remaining_ms ?? 2500)));
        }
      } catch {
        if (!disposed) {setResult(null); setError('Mất kết nối nhận diện. Đang thử lại…');}
      } finally {
        clearTimeout(timeout); controller = null;
        const interval = 1000 / Math.max(1, Math.min(30, camera.sampling_fps ?? 5));
        if (!disposed) timer = setTimeout(() => void poll(), Math.max(0, interval - (performance.now() - started)));
      }
    }
    void poll();
    return () => {disposed = true; clearTimeout(timer); clearTimeout(expiry); controller?.abort();};
  }, [camera.id, camera.revision, camera.sampling_fps, active, compact]);


  if (!active) return <div className="inactive-camera-preview">
    <p>Camera đang tắt hoặc chưa kích hoạt.</p>
    <p>Bật camera để xem hình và ô nhận diện khuôn mặt tại đây.</p>
  </div>;
  return <div className="camera-recognition" aria-label="So sánh khuôn mặt">
    {!compact && <h4>Camera nhận diện trực tiếp</h4>}
    {compact && (result?.alerts ?? []).length > 0 && <div className="stranger-alert" role="alert">Cảnh báo người lạ · đã đủ {result?.unknown_alert_frames ?? 5} frame liên tiếp</div>}
    {error ? <p role="status">{error}</p> : !result ? <p>Đang so sánh…</p> : <>
      {(result.preview) && <figure className="detection-preview" style={compact ? {'--frame-ratio': frameRatio} as CSSProperties : undefined}>
        <div className="detection-frame" key={`${result.stream_session_id}/${result.frame_id}`}>
          <img src={result.preview} alt={`Camera nhận diện ${camera.name}`} onLoad={compact ? event => {const img = event.currentTarget; if (img.naturalHeight) setFrameRatio(img.naturalWidth / img.naturalHeight);} : undefined}/>
          {(roiEditing || (roiPolygon && roiPolygon.length > 0)) && <svg className={`roi-overlay ${roiEditing ? 'editing' : ''}`} viewBox="0 0 1000 1000" preserveAspectRatio="none"
            onClick={event => {
              if (!roiEditing || !onRoiChange) return;
              const bounds = event.currentTarget.getBoundingClientRect();
              const point: [number, number] = [Math.max(0, Math.min(1, (event.clientX - bounds.left) / bounds.width)), Math.max(0, Math.min(1, (event.clientY - bounds.top) / bounds.height))];
              onRoiChange([...(roiPolygon ?? []), point]);
            }}>
            {(roiPolygon?.length ?? 0) >= 3 && <polygon points={roiPolygon!.map(([x,y]) => `${x*1000},${y*1000}`).join(' ')} />}
            {(roiPolygon ?? []).map(([x,y], index) => <circle key={index} cx={x*1000} cy={y*1000} r="8" />)}
          </svg>}
          {(result.persons ?? []).filter(person => person.box.length === 4 && person.box.every(value => Number.isFinite(value) && value >= 0 && value <= 1)).map((person, index) => {
            const [x, y, width, height] = person.box;
            const stableIdentity = person.person_number != null && person.full_name
              ? `${person.full_name} \u00b7 ID ${person.person_number}` : null;
            const label = person.identity_state === 'UNKNOWN' ? 'Ng\u01b0\u1eddi l\u1ea1' :
              person.identity_state === 'VERIFIED' ? stableIdentity ?? '\u0110\u00e3 x\u00e1c minh' :
              person.identity_state === 'PENDING' ? `\u0110ang x\u00e1c minh: ${stableIdentity ?? person.full_name ?? 'khu\u00f4n m\u1eb7t'}` :
              `Track t\u1ea1m #${person.track_id ?? '\u2014'}`;
            return <div key={`person-${index}`} data-testid="person-detection-box" className="person-detection-box"
              style={{left:`${x*100}%`,top:`${y*100}%`,width:`${width*100}%`,height:`${height*100}%`}}>
              <span>{label} · {Math.round(person.confidence * 100)}%</span>
            </div>;
          })}
          {(result.faces ?? []).filter(face => face.box?.length === 4 && face.box.every(value => Number.isFinite(value) && value >= 0 && value <= 1)).map((face, index) => {
            const [x, y, width, height] = face.box!;
            const verified = face.state === 'VERIFIED';
            const stableId = face.person_number != null ? ` \u00b7 ID ${face.person_number}` : '';
            const label = verified ? `\u0110\u00e3 x\u00e1c minh: ${face.full_name}${stableId}` :
              face.state === 'PENDING' ? (face.full_name ? `\u0110ang x\u00e1c minh: ${face.full_name}${stableId}` : '\u0110\u00e3 ph\u00e1t hi\u1ec7n khu\u00f4n m\u1eb7t') :
              face.state === 'UNCERTAIN' ? 'Ch\u01b0a x\u00e1c \u0111\u1ecbnh ch\u1eafc ch\u1eafn' : 'Ng\u01b0\u1eddi l\u1ea1';
            return <div key={index} data-testid="face-detection-box"
              className={`face-detection-box ${verified ? 'verified' : face.state === 'UNKNOWN' ? 'unknown' : 'pending'}`}
              style={{left:`${x*100}%`,top:`${y*100}%`,width:`${width*100}%`,height:`${height*100}%`}}>
              <span>{label}{face.student_id ? ` · ${face.student_id}` : ''}</span>
            </div>;
          })}
        </div>
        <figcaption>{compact ? `${result.persons?.length ?? 0} người · ${result.faces?.length ?? 0} khuôn mặt` : <>{result.person_state === 'READY' ? `${result.persons?.length ?? 0} ng\u01b0\u1eddi \u00b7 ` : ''}{`${result.faces?.length ?? 0} khu\u00f4n m\u1eb7t \u00b7 Track ID l\u00e0 m\u00e3 theo d\u00f5i t\u1ea1m th\u1eddi; danh t\u00ednh ch\u1ec9 hi\u1ec7n khi \u0111\u00e3 x\u00e1c minh.`}</>}</figcaption>
      </figure>}
      {!compact && (result.alerts ?? []).length > 0 && <div className="stranger-alert" role="alert">
        Cảnh báo: phát hiện người lạ trong ROI sau {result.unknown_alert_frames ?? 5} frame liên tiếp.
        {!compact && (result.alerts ?? []).map(alert => <div key={alert.id}>
          {alert.evidence_status === 'READY' && alert.evidence_key
            ? <img className="face-event-snapshot" src={`/api/v1/recognition/evidence/${encodeURIComponent(alert.evidence_key)}`} alt="Snapshot cảnh báo người lạ" />
            : <p>{alert.evidence_status === 'PENDING' ? 'Đang lưu snapshot…' : 'Đã ghi cảnh báo, không lưu được snapshot.'}</p>}
        </div>)}
      </div>}
      {(result.faces ?? []).some(face => face.state === 'UNKNOWN') && !(result.alerts ?? []).length &&
        <p role="status">Đang kiểm tra người chưa khớp ảnh đăng ký; chờ đủ {result.unknown_alert_frames ?? 5} frame liên tiếp.</p>}

      {result.person_state === 'NOT_CONFIGURED' && <p className="field-hint">Chưa bật phát hiện người. Tải và áp dụng model YOLOX trong mục Models.</p>}
      {result.state !== 'READY' && <p>{stateLabels[result.state] ?? `Trạng thái model: ${result.state}`}</p>}
      {!compact && <ul>{(result.faces ?? []).map((face, index) => <li key={index}>
        {face.state === 'VERIFIED' ? `Đã xác minh: ${face.full_name} · ${face.student_id ?? ''}` :
          face.state === 'PENDING' && face.full_name ? `Đang xác minh: ${face.full_name} · ${face.student_id ?? ''}` :
          face.state === 'UNCERTAIN' ? 'Chưa đủ chắc chắn để xác định người.' : 'Chưa khớp ảnh đã đăng ký.'}
        {face.full_name && <span> · Độ tương đồng {face.similarity.toFixed(3)}</span>}
      </li>)}</ul>}
    </>}
  </div>;
}
