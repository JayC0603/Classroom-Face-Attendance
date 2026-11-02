import {useEffect, useState} from 'react';
import {getSimpleRecognitionStatus, type SimpleRecognitionStatus} from '../../api/simpleRecognition';
import {AdvancedModelsPanel} from './AdvancedModelsPanel';
import {PersonModelsPanel} from './PersonModelsPanel';

export function ModelsPanel() {
  const [status, setStatus] = useState<SimpleRecognitionStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [advanced, setAdvanced] = useState(false);

  useEffect(() => {
    let disposed = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const controller = new AbortController();
    async function poll() {
      try {
        const next = await getSimpleRecognitionStatus(controller.signal);
        if (!disposed) {setStatus(next); setError(null);}
      } catch {
        if (!disposed) setError('Không kết nối được dịch vụ so sánh khuôn mặt.');
      } finally {
        if (!disposed) timer = setTimeout(() => void poll(), 5000);
      }
    }
    void poll();
    return () => {disposed = true; controller.abort(); clearTimeout(timer);};
  }, []);

  async function refresh() {
    setBusy(true); setError(null);
    try {setStatus(await getSimpleRecognitionStatus(undefined, true));}
    catch {setError('Không nạp lại được ảnh đăng ký.');}
    finally {setBusy(false);}
  }

  const rejected = status?.images?.filter(image => image.state !== 'READY') ?? [];
  return <section className="recognition-models" aria-labelledby="simple-models-heading">
    <p className="eyebrow">SO SÁNH KHUÔN MẶT</p>
    <h2 id="simple-models-heading">Mô hình nhận diện</h2>
    {error && <p role="alert">{error}</p>}
    <article>
      <h3>YuNet + SFace</h3>
      <p>YuNet tìm khuôn mặt; SFace so sánh với ảnh đã đăng ký. Model và ảnh mẫu được xử lý tự động.</p>
      <a className="button-link" href="#people">{'M\u1edf h\u1ed3 s\u01a1 khu\u00f4n m\u1eb7t \u0111\u1ec3 t\u1ea3i \u1ea3nh'}</a>
      <strong>{status?.state === 'READY' ? 'Sẵn sàng so sánh' :
        status?.state === 'NO_REFERENCE_IMAGES' ? 'Cần ảnh đăng ký có một khuôn mặt rõ' :
        status?.state === 'PROCESSING' || !status ? 'Đang chuẩn bị model và ảnh mẫu…' : `Lỗi: ${status.state}`}</strong>
      {status?.reference_people !== undefined && status.reference_images !== undefined && <p>{status.reference_people} hồ sơ · {status.reference_images} ảnh mẫu dùng được</p>}
      {rejected.length > 0 && <p role="status">{rejected.length} ảnh chưa dùng được: cần một khuôn mặt rõ, không bị cắt hoặc ảnh nhóm.</p>}
      <p>Bật camera để so sánh. Cần ít nhất 3 frame khớp liên tiếp để xác minh; độ tương đồng không phải phần trăm xác suất.</p>
      <button type="button" disabled={busy} onClick={() => void refresh()}>{busy ? 'Đang nạp ảnh…' : 'Nạp lại ảnh đăng ký'}</button>
    </article>
    <PersonModelsPanel />
    <details onToggle={event => setAdvanced(event.currentTarget.open)}>
      <summary>Cấu hình pipeline nâng cao</summary>
      <p>YOLO/SCRFD/AdaFace là pipeline tùy chỉnh riêng. Luồng so sánh YuNet + SFace phía trên không yêu cầu cấu hình này.</p>
      {advanced && <AdvancedModelsPanel />}
    </details>
  </section>;
}
