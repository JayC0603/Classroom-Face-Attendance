import { useEffect, useRef, useState } from "react";

import { registryError, type ImageSource, type UploadResult } from "../../api/registry";
import { WebcamCapture } from "./WebcamCapture";

interface Props {
  busy: boolean;
  resetKey: number;
  isExisting: boolean;
  onUpload(files: File[], source: ImageSource): Promise<UploadResult[]>;
}

export function ImageUpload({ busy, resetKey, isExisting, onUpload }: Props) {
  const [files, setFiles] = useState<File[]>([]);
  const [previews, setPreviews] = useState<Array<{ url: string; filename: string }>>([]);
  const [results, setResults] = useState<UploadResult[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [source, setSource] = useState<ImageSource>("MANUAL_UPLOAD");
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const next = files.map(file => ({ url: URL.createObjectURL(file), filename: file.name }));
    setPreviews(next);
    return () => next.forEach(item => URL.revokeObjectURL(item.url));
  }, [files]);

  useEffect(() => {
    setFiles([]); setResults([]); setError(null);
    if (input.current) input.current.value = "";
  }, [resetKey]);

  async function submit() {
    setError(null); setResults([]);
    try {
      const next = await onUpload(files, source);
      setResults(next);
      setFiles(current => current.filter((_, index) => next[index]?.status !== "STORED_PENDING"));
      if (input.current) input.current.value = "";
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Không tải được ảnh");
    }
  }

  return (
    <div className="registry-upload">
      <fieldset className="registry-source" disabled={busy}>
        <legend>Nguồn ảnh</legend>
        {(["MANUAL_UPLOAD", "CAMERA_CAPTURE"] as const).map(value => (
          <label key={value}>
            <input type="radio" name="registry-source" value={value} checked={source === value}
              onChange={() => {
                setSource(value); setFiles([]); setResults([]); setError(null);
                if (input.current) input.current.value = "";
              }} />
            {value === "MANUAL_UPLOAD" ? "Tải ảnh từ máy" : "Dùng webcam"}
          </label>
        ))}
      </fieldset>
      <p className="registry-help">Chuyển nguồn sẽ xóa các ảnh đang chọn chưa lưu.</p>
      {source === "MANUAL_UPLOAD" ? <>
      <label htmlFor="registry-files">Chọn ảnh khuôn mặt</label>
      <input ref={input} id="registry-files" type="file" multiple accept="image/jpeg,image/png"
        disabled={busy} aria-describedby="registry-file-help" onChange={event => {
          const next = Array.from(event.target.files ?? []);
          setError(null); setResults([]); setFiles([]);
          if (next.length > 10) { setError("Mỗi lần chỉ tải tối đa 10 ảnh"); event.target.value = ""; return; }
          if (next.some(file => file.size > 10 * 1024 * 1024)) {
            setError("Mỗi ảnh phải nhỏ hơn hoặc bằng 10 MB"); event.target.value = ""; return;
          }
          setFiles(next);
        }} />
      <p id="registry-file-help" className="registry-help">JPG/PNG · Tối đa 10 ảnh/lần · 10 MB/ảnh.</p>
      </> : <WebcamCapture key={resetKey} disabled={busy} remaining={10 - files.length}
        onCapture={file => {
          setFiles(current => current.length < 10 ? [...current, file] : current);
          setResults([]); setError(null);
        }} />}
      <div className="face-gallery" aria-label="Ảnh đã chọn">
        {previews.map((preview, index) => (
          <figure key={`${preview.url}-${index}`}>
            <img src={preview.url} alt={`Xem trước ${preview.filename}`} />
            <figcaption>{preview.filename}</figcaption>
          </figure>
        ))}
      </div>
      <button type="button" disabled={busy || (isExisting && files.length === 0)} onClick={submit}>
        {busy ? "Đang lưu…" : isExisting ? "Tải ảnh lên" : "Lưu hồ sơ và tải ảnh"}
      </button>
      {error && <p role="alert" className="registry-error">{error}</p>}
      <ul className="upload-results" aria-live="polite">
        {results.map((result, index) => <li key={index}>
          {result.filename}: {result.status === "STORED_PENDING"
            ? "Đã tải lên — Chờ admin duyệt" : registryError(result.error_category)}
        </li>)}
      </ul>
    </div>
  );
}
