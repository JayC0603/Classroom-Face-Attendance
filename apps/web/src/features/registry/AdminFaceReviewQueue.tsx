import {useEffect, useState} from 'react';
import {listAdminPendingFaceReviews, reviewAdminFaceImage, type FaceReviewImage} from '../../api/classes';

export function AdminFaceReviewQueue({refreshKey = 0}: {refreshKey?: number}) {
  const [images, setImages] = useState<FaceReviewImage[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    listAdminPendingFaceReviews(controller.signal).then(value => {
      if (!controller.signal.aborted) setImages(value);
    }).catch(reason => {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : 'Không tải được ảnh chờ duyệt.');
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [revision, refreshKey]);

  async function review(image: FaceReviewImage, decision: 'APPROVE' | 'REJECT') {
    if (decision === 'REJECT' && !window.confirm(`Từ chối ảnh ${image.original_filename}?`)) return;
    setBusyId(image.id);
    setError(null);
    setMessage(null);
    try {
      await reviewAdminFaceImage(image.id, decision);
      setImages(current => current.filter(item => item.id !== image.id));
      setMessage(decision === 'APPROVE'
        ? 'Đã duyệt ảnh. Ảnh đủ chất lượng sẽ được nạp vào so sánh tự động.'
        : 'Đã từ chối ảnh đăng ký.');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Không lưu được quyết định duyệt ảnh.');
    } finally {
      setBusyId(null);
    }
  }

  return <section className="admin-face-review" aria-labelledby="admin-face-review-heading">
    <div className="section-heading">
      <div><p className="eyebrow">KIỂM SOÁT CHẤT LƯỢNG</p><h3 id="admin-face-review-heading">Ảnh đăng ký chờ duyệt</h3></div>
      <button type="button" disabled={loading} onClick={() => setRevision(value => value + 1)}>Tải lại danh sách</button>
    </div>
    <p>Chỉ ảnh được duyệt và có đúng một khuôn mặt rõ mới được đưa vào so sánh.</p>
    {error && <p role="alert" className="form-error">{error}</p>}
    {message && <p role="status">{message}</p>}
    {loading ? <p role="status">Đang tải ảnh chờ duyệt…</p> : images.length === 0
      ? <p>Không còn ảnh nào chờ duyệt.</p>
      : <div className="admin-face-review-grid">{images.map(image => <article key={image.id} className="workspace-card review-card">
        <img src={image.content_url} alt={`Ảnh chờ duyệt: ${image.original_filename}`} />
        <div><h4>{image.original_filename}</h4><p>{image.person_name ?? 'Hồ sơ'} · ID {image.person_number ?? image.person_id}{image.student_id ? ` · ${image.student_id}` : ''}</p><p>{image.width} × {image.height} · {image.source === 'CAMERA_CAPTURE' ? 'Chụp webcam' : 'Tải từ máy'}</p>
          <div className="review-actions">
            <button type="button" disabled={busyId !== null} onClick={() => void review(image, 'APPROVE')}>Duyệt ảnh</button>
            <button type="button" disabled={busyId !== null} onClick={() => void review(image, 'REJECT')}>Từ chối ảnh</button>
          </div>
        </div>
      </article>)}</div>}
  </section>;
}
