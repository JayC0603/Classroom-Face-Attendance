import { useEffect, useRef, useState } from "react";

import {
  createPerson, listFaceImages, listPersons, uploadFaceImages,
  type FaceImagePublic, type ImageSource, type PersonPage, type PersonPublic, type UploadResult
} from "../../api/registry";
import { ImageUpload } from "./ImageUpload";
import { AdminFaceReviewQueue } from "./AdminFaceReviewQueue";

export function FaceRegistryPanel() {
  const [page, setPage] = useState<PersonPage | null>(null);
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);
  const [revision, setRevision] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<PersonPublic | null>(null);
  const [fullName, setFullName] = useState("");
  const [studentId, setStudentId] = useState("");
  const [busy, setBusy] = useState(false);
  const [images, setImages] = useState<FaceImagePublic[]>([]);
  const [galleryLoading, setGalleryLoading] = useState(false);
  const [galleryError, setGalleryError] = useState<string | null>(null);
  const [resetKey, setResetKey] = useState(0);
  const form = useRef<HTMLFormElement>(null);
  const submitting = useRef(false);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError(null);
    const timer = setTimeout(() => {
      listPersons(search, offset, controller.signal).then(value => {
        if (!controller.signal.aborted) setPage(value);
      }).catch(reason => {
        if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Không tải được hồ sơ");
      }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    }, 200);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [search, offset, revision]);

  useEffect(() => {
    setImages([]); setGalleryError(null);
    if (!selected) { setGalleryLoading(false); return; }
    const controller = new AbortController();
    setGalleryLoading(true);
    listFaceImages(selected.id, controller.signal).then(value => {
      if (!controller.signal.aborted) setImages(value);
    }).catch(reason => {
      if (!controller.signal.aborted) setGalleryError(reason instanceof Error ? reason.message : "Không tải được thư viện ảnh");
    }).finally(() => { if (!controller.signal.aborted) setGalleryLoading(false); });
    return () => controller.abort();
  }, [selected?.id, revision]);

  function select(person: PersonPublic | null) {
    setSelected(person); setFullName(""); setStudentId(""); setResetKey(value => value + 1);
  }

  async function save(files: File[], source: ImageSource): Promise<UploadResult[]> {
    if (submitting.current) throw new Error("Yêu cầu đang được xử lý");
    if (!selected && !form.current?.reportValidity()) throw new Error("Vui lòng nhập họ tên và mã sinh viên");
    submitting.current = true; setBusy(true);
    try {
      let person = selected;
      if (!person) {
        person = await createPerson({ full_name: fullName.trim(), student_id: studentId.trim() });
        setSelected(person); setFullName(""); setStudentId("");
      }
      const results = files.length ? await uploadFaceImages(person.id, files, source) : [];
      return results;
    } finally {
      submitting.current = false; setBusy(false); setRevision(value => value + 1);
    }
  }

  return (
    <section aria-labelledby="face-registry-heading" className="face-registry">
      <p className="eyebrow">HỒ SƠ ĐĂNG KÝ</p>
      <h2 id="face-registry-heading">Đăng ký khuôn mặt</h2>
      <p className="registry-notice">{'\u1ea2nh t\u1ea3i l\u00ean s\u1ebd ch\u1edd duy\u1ec7t. \u1ea2nh \u0111\u01b0\u1ee3c duy\u1ec7t v\u00e0 c\u00f3 \u0111\u00fang m\u1ed9t khu\u00f4n m\u1eb7t r\u00f5 s\u1ebd t\u1ef1 \u0111\u01b0\u1ee3c d\u00f9ng \u0111\u1ec3 so s\u00e1nh.'}</p>
      {page?.persistence === "in_memory" && <p role="status" className="registry-notice">
        Đang dùng bộ nhớ tạm. Hồ sơ sẽ mất khi khởi động lại máy chủ.
      </p>}
      <AdminFaceReviewQueue refreshKey={revision} />
      <div className="registry-layout">
        <article>
          <h3>Danh sách hồ sơ</h3>
          <label htmlFor="registry-search">Tìm theo tên hoặc mã sinh viên</label>
          <input id="registry-search" type="search" value={search} maxLength={200}
            onChange={event => { setSearch(event.target.value); setOffset(0); }} />
          <button type="button" disabled={busy} onClick={() => select(null)}>Tạo hồ sơ mới</button>
          {loading && <p role="status">Đang tải hồ sơ…</p>}
          {error && <p role="alert" className="registry-error">{error}</p>}
          {error && <button onClick={() => setRevision(value => value + 1)}>Thử lại</button>}
          {!loading && !error && page?.items.length === 0 && <p>Chưa có hồ sơ phù hợp.</p>}
          {!loading && !error && <ul className="person-list">
            {page?.items.map(person => <li key={person.id}>
              <button type="button" disabled={busy} aria-pressed={selected?.id === person.id}
                onClick={() => select(person)}>
                <strong>{person.full_name}</strong>
                <span>ID {person.person_number} · {person.student_id ?? "Chưa có mã sinh viên"} · {person.image_count} ảnh</span>
                {person.status === "DISABLED" && <span>Đã vô hiệu hóa</span>}
              </button>
            </li>)}
          </ul>}
          <div className="registry-pagination">
            <button disabled={loading || offset === 0} onClick={() => setOffset(value => Math.max(0, value - 20))}>Trước</button>
            <span>{page?.total ?? 0} hồ sơ</span>
            <button disabled={loading || offset + 20 >= (page?.total ?? 0)} onClick={() => setOffset(value => value + 20)}>Sau</button>
          </div>
        </article>
        <article>
          <h3>{selected ? selected.full_name : "Hồ sơ mới"}</h3>
          {selected && <p>ID {selected.person_number} · {selected.student_id} · Ảnh mẫu được kiểm tra tự động khi so sánh.</p>}
          <form ref={form} onSubmit={event => event.preventDefault()}>
            {!selected && <div className="registry-fields">
              <label htmlFor="registry-name">Họ và tên</label>
              <input id="registry-name" required maxLength={200} value={fullName} disabled={busy}
                onChange={event => setFullName(event.target.value)} />
              <label htmlFor="registry-student-id">Mã sinh viên</label>
              <input id="registry-student-id" required maxLength={100} value={studentId} disabled={busy}
                onChange={event => setStudentId(event.target.value)} />
            </div>}
            <ImageUpload busy={busy} resetKey={resetKey} isExisting={!!selected} onUpload={save} />
          </form>
          {selected && <div aria-label="Thư viện ảnh">
            <h4>Thư viện ảnh</h4>
            {galleryLoading && <p role="status">Đang tải ảnh…</p>}
            {galleryError && <p role="alert" className="registry-error">{galleryError}</p>}
            {galleryError && <button onClick={() => setRevision(value => value + 1)}>Tải lại ảnh</button>}
            {!galleryLoading && !galleryError && images.length === 0 && <p>Chưa có ảnh.</p>}
            <div className="face-gallery">
              {images.filter(image => image.person_id === selected.id).map(image => <figure key={image.id}>
                <img src={image.content_url} alt={`Ảnh của ${selected.full_name}: ${image.original_filename}`} loading="lazy" />
                <figcaption>{image.original_filename}<br />
                  {image.source === "CAMERA_CAPTURE" ? "Chụp từ webcam" : "Tải từ máy"}
                  <br /><span>{image.review_state === "APPROVED" ? "\u0110\u00e3 duy\u1ec7t" : image.review_state === "REJECTED" ? "B\u1ecb t\u1eeb ch\u1ed1i" : "\u0110ang ch\u1edd duy\u1ec7t"}</span></figcaption>
              </figure>)}
            </div>
          </div>}
        </article>
      </div>
    </section>
  );
}
