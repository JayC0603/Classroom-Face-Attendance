import {useEffect, useState, type FormEvent} from 'react';
import {activateModelDeployment, assignModel, createModelDeployment} from '../../api/client';
import {deactivatePersonDeployment, deletePersonModel, getPersonModels, updatePersonModel, uploadPersonModel, type PersonModels} from '../../api/personModels';

export function PersonModelsPanel() {
  const [models, setModels] = useState<PersonModels | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editVersion, setEditVersion] = useState('');
  const [editConfidence, setEditConfidence] = useState(0.3);
  const [search, setSearch] = useState('');
  const visibleModels = (models?.items ?? []).filter(model => `${model.architecture ?? 'YOLOX'} ${model.version}`.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()));
  const selectedModel = models?.items?.find(model => model.deployments?.some(deployment => deployment.id === models.active_deployment_id));

  useEffect(() => {
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    async function poll() {
      try {
        const next = await getPersonModels(controller.signal);
        if (!disposed) {setModels(next); setLoadError(null);}
      } catch {
        if (!disposed) setLoadError('Không tải được trạng thái model người. Đang thử lại…');
      } finally {
        if (!disposed) timer = setTimeout(() => void poll(), 3000);
      }
    }
    void poll();
    return () => {disposed = true; controller.abort(); clearTimeout(timer);};
  }, [refresh]);

  async function action(work: () => Promise<unknown>, success: string) {
    setBusy(true); setError(null); setMessage('');
    try {await work(); setMessage(success); setRefresh(value => value + 1);}
    catch (error) {setError(error instanceof Error ? error.message : 'Thao tác thất bại.');}
    finally {setBusy(false);}
  }

  function upload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const file = data.get('file');
    if (file instanceof File && models && file.size > models.max_upload_bytes) {
      setError('File vượt quá giới hạn tải lên.'); return;
    }
    void action(async () => {await uploadPersonModel(data); form.reset();},
      'Đã kiểm tra và lưu bản nháp. Kích hoạt để worker nạp và kiểm thử model.');
  }

  return <article className="person-models" aria-labelledby="person-model-heading">
    <p className="eyebrow">PHÁT HIỆN NGƯỜI</p>
    <h3 id="person-model-heading">Model phát hiện người</h3>
    <p>Chọn model để phát hiện và theo dõi người trên camera.</p>
    <div className="model-selection-summary"><span className="eyebrow">MODEL ĐƯỢC CHỌN CHO CAMERA</span><strong>{selectedModel ? `${selectedModel.architecture ?? 'YOLOX'} · ${selectedModel.version}` : models ? 'Chưa chọn model' : 'Đang tải cấu hình…'}</strong><p>{selectedModel ? 'Trạng thái chạy thực tế được hiển thị tại màn Giám sát.' : 'Tải model → kiểm tra trên CPU → áp dụng lên camera.'}</p></div>
    <details><summary>Thêm model person · YOLOX</summary>
    <form onSubmit={upload} className="person-model-form">
      <label className="person-file">File model ONNX
        <input name="file" type="file" accept=".onnx" required disabled={busy} />
      </label>
      <label>Phiên bản<input name="version" defaultValue="v1" required maxLength={80} disabled={busy} /></label>
      <label>Kiểu đầu ra
        <select name="decode_mode" defaultValue="auto" disabled={busy}>
          <option value="auto">Tự nhận dạng kiểu export</option>
          <option value="yolox_raw">YOLOX raw · chưa decode</option>
          <option value="yolox_decoded">YOLOX · đã decode tọa độ</option>
          <option value="yolox_xyxy">YOLOX xyxy · score + class ID</option>
        </select>
      </label>
      <label>Class người<input name="person_class_id" type="number" min={0} max={999} step={1} defaultValue={0} required disabled={busy} /></label>
      <label>Ngưỡng phát hiện<input name="confidence" type="number" min={0.01} max={1} step={0.01} defaultValue={0.3} required disabled={busy} /></label>
      <button type="submit" disabled={busy || !models}>{busy ? 'Đang xử lý…' : 'Kiểm tra & tải model'}</button>
    </form>
    <p className="field-hint">Hỗ trợ 608 cao × 1088 rộng, FP32, NCHW. Tự nhận dạng export xyxy + score + class ID từ cấu trúc model. Với export khác, chọn raw hoặc đã decode theo cấu hình export; chưa hỗ trợ đầu ra đã gộp NMS, FP16 hoặc chiều ảnh động.</p>
    {models && <p className="field-hint">Giới hạn file: {Math.floor(models.max_upload_bytes / 1024 / 1024)} MB. Class 0 thường là người; kiểm tra nhãn khi dùng model tùy chỉnh.</p>}
    <p className="field-hint">Model YOLO khác dùng mục cấu hình nâng cao để khai báo đúng đầu vào và kiểu giải mã.</p></details>
    {error && <p role="alert">{error}</p>}
    {loadError && <p role="alert">{loadError}</p>}
    {message && <p role="status">{message}</p>}
    {!!models?.items?.length && <div className="filter-toolbar"><label>Tìm model<input type="search" value={search} onChange={e => setSearch(e.target.value)} placeholder="Tên phiên bản hoặc YOLO26, YOLOX…"/></label><span className="field-hint">{visibleModels.length} phiên bản</span></div>}
    {!!models?.items?.length && !visibleModels.length && <p role="status">Không có model phù hợp. <button onClick={() => setSearch('')}>Xóa tìm kiếm</button></p>}
    {models && !(models.items ?? []).length && <div className="person-model-empty">Chưa có model phát hiện người. Tải file của bạn lên để bắt đầu.</div>}
    {visibleModels.map(model => <section className={`person-model-version ${selectedModel?.id === model.id ? 'is-selected-model' : ''}`} key={model.id} aria-label={`${model.architecture ?? 'YOLOX'} ${model.version}`}>
      <div><strong>{model.architecture ?? 'YOLOX'} · {model.version}</strong><p>{model.input_shape.join(' × ')} · Ngưỡng {model.confidence}</p><details><summary>Thông tin kỹ thuật</summary><p>{model.decode_mode} · Class {model.person_class_id}</p></details></div>
      {editingId === model.id ? <form className="person-model-form" onSubmit={event => {
        event.preventDefault();
        void action(async () => {
          await updatePersonModel(model.id, editVersion.trim(), model.deployments?.length ? undefined : editConfidence);
          setEditingId(null);
        }, 'Đã lưu thông tin model.');
      }}>
        <label>Tên phiên bản<input required maxLength={80} value={editVersion} disabled={busy} onChange={e => setEditVersion(e.target.value)}/></label>
        <label>Ngưỡng phát hiện<input type="number" min={0.01} max={1} step={0.01} required value={editConfidence} disabled={busy || !!model.deployments?.length} onChange={e => setEditConfidence(Number(e.target.value))}/></label>
        {!!model.deployments?.length && <p className="field-hint">Model đã được triển khai. Có thể đổi tên; để thay ngưỡng hoặc file ONNX, tải một phiên bản mới rồi kiểm tra trước khi áp dụng.</p>}
        <button disabled={busy || !editVersion.trim()}>Lưu model</button><button type="button" disabled={busy} onClick={() => setEditingId(null)}>Hủy sửa</button>
      </form> : <div className="camera-actions">
        <button type="button" disabled={busy} onClick={() => {setEditingId(model.id); setEditVersion(model.version); setEditConfidence(model.confidence);}}>Sửa model</button>
        <button className="danger-button" type="button" disabled={busy} onClick={() => {
          if (window.confirm(`Xóa model “${model.architecture ?? 'YOLO'} · ${model.version}” khỏi danh sách? Chỉ xóa được khi model không hoạt động và không được chọn cho camera.`)) {
            void action(() => deletePersonModel(model.id), 'Đã xóa model khỏi danh sách.');
          }
        }}>Xóa model</button>
      </div>}
      <button className="button-primary" type="button" disabled={busy || (model.deployments ?? []).some(d => d.status === 'ACTIVATION_PENDING')} onClick={() => void action(async () => {
        const deployment = await createModelDeployment(model.id, 'ONNX_CPU');
        await activateModelDeployment(deployment.id);
      }, 'Đã yêu cầu kích hoạt. Đang chờ worker nạp model và chạy warmup…')}>Kích hoạt trên CPU</button>
      {!!model.deployments?.length && <details className="deployment-list" open={(model.deployments ?? []).some(d => d.status === 'ACTIVATION_PENDING' || (d.status === 'ACTIVE' && d.id !== models?.active_deployment_id))}><summary>Bản triển khai & thao tác áp dụng ({model.deployments.length})</summary>
      {(model.deployments ?? []).map(deployment => <div className="person-deployment" key={deployment.id}>
        <p>{models?.active_deployment_id === deployment.id ? 'Đã chọn cho camera' :
          deployment.status === 'INACTIVE' ? 'Bản triển khai không hoạt động' :
          deployment.runtime_state === 'ACTIVE' ? 'Worker đã nạp và kiểm thử thành công' :
          deployment.runtime_state === 'ERROR' || deployment.status === 'ERROR' ? 'Không nạp được model' : 'Đang chờ worker nạp model'}
          {deployment.error_category ? ` · ${deployment.error_category}` : ''}</p>
        <button type="button" disabled={busy || deployment.status !== 'ACTIVE' || deployment.runtime_state !== 'ACTIVE' || models?.active_deployment_id === deployment.id}
          onClick={() => void action(() => assignModel('PERSON_DETECTOR', deployment.id),
            'Đã áp dụng model. Mở Camera để xem ô người khi worker nhận cấu hình mới.')}>Áp dụng lên camera</button>
        {models?.active_deployment_id !== deployment.id && ['ACTIVE', 'ACTIVATION_PENDING'].includes(deployment.status) && <button type="button" disabled={busy} onClick={() => {
          if (window.confirm('Ngừng bản triển khai này để có thể xóa model?')) void action(() => deactivatePersonDeployment(deployment.id), 'Đã ngừng bản triển khai.');
        }}>Ngừng bản triển khai</button>}
      </div>)}</details>}
    </section>)}
  </article>;
}
