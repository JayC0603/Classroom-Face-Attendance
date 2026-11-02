import {useEffect, useState} from 'react';
import {FaceEventsPanel} from './FaceEventsPanel';
import {listCameras} from '../../api/client';
import {apiFetch} from '../../api/client';
import type {CameraPublic} from '../../api/contracts';
import {PersonPicker} from '../../ui/PersonPicker';

type EventRow = {id:string;camera_id:string;camera_name:string;track_id:number|null;person_id:string;full_name:string;student_id:string|null;recognized_at:string;attendance_date:string;evidence_key:string|null};
type AttendanceRow = {id:string;attendance_date:string;person_id:string;full_name:string;student_id:string|null;first_seen_at:string;last_seen_at:string;exited_at:string|null;camera_id:string;camera_name:string};

export function RecognitionHistoryPanel() {
  const [tab,setTab]=useState<'events'|'attendance'|'faces'>('events');
  const [events,setEvents]=useState<EventRow[]>([]); const [attendance,setAttendance]=useState<AttendanceRow[]>([]);
  const [cameras,setCameras]=useState<CameraPublic[]>([]); const [camera,setCamera]=useState('');
  const [start,setStart]=useState(''); const [end,setEnd]=useState(''); const [person,setPerson]=useState('');
  const [error,setError]=useState('');
  const query=new URLSearchParams(); if(start)query.set('start',start); if(end)query.set('end',end); if(camera)query.set('camera_id',camera); if(person)query.set('person_id',person);
  const params=query.toString();
  useEffect(()=>{void listCameras().then(setCameras).catch(()=>undefined);},[]);
  useEffect(() => {
    if (tab === 'faces') return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    setEvents([]); setAttendance([]); setError('');
    async function load() {
      try {
        const endpoint = tab === 'events' ? '/api/v1/recognition/events' : '/api/v1/attendance';
        const response = await apiFetch(`${endpoint}?${params}`, {cache: 'no-store', signal: controller.signal});
        if (!response.ok) throw new Error('Không tải được lịch sử.');
        const rows = await response.json();
        if (!controller.signal.aborted) {if (tab === 'events') setEvents(rows); else setAttendance(rows); setError('');}
      } catch (reason) {if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : 'Không tải được lịch sử.');}
      finally {if (!controller.signal.aborted) timer = setTimeout(() => void load(), 5000);}
    }
    void load();
    return () => {controller.abort(); clearTimeout(timer);};
  }, [params, tab]);
  return <section className="history-panel" aria-label="Lịch sử nhận diện và điểm danh">
    <div className="history-toolbar">
      <button type="button" aria-pressed={tab==='faces'} onClick={()=>setTab('faces')}>Ảnh vào &amp; cảnh báo</button>
      <button type="button" aria-pressed={tab==='events'} onClick={()=>setTab('events')}>Lịch sử xác minh</button>
      <button type="button" aria-pressed={tab==='attendance'} onClick={()=>setTab('attendance')}>Điểm danh</button>
      {tab==='attendance'&&<a className="button-link" href={`/api/v1/attendance/export.xlsx${params?`?${params}`:''}`}>Xuất Excel</a>}
    </div>
    <div className="history-filters">
      {tab !== 'faces' && <label>Từ ngày<input type="date" value={start} onChange={e=>setStart(e.target.value)}/></label>}
      {tab !== 'faces' && <label>Đến ngày<input type="date" value={end} onChange={e=>setEnd(e.target.value)}/></label>}
      <label>Camera<select value={camera} onChange={e=>setCamera(e.target.value)}><option value="">Tất cả</option>{cameras.map(item=><option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
      {tab !== 'faces' && <PersonPicker value={person} onChange={setPerson} label="Người cần tra cứu"/>}
    </div>
    {error&&<p role="alert">{error}</p>}
    {tab==='faces'?<FaceEventsPanel cameraId={camera||undefined}/>:tab==='events'?<div className="history-list">{events.length===0?<p className="empty-state">Chưa có sự kiện xác minh phù hợp.</p>:events.map(row=><article key={row.id} className="history-event">
      {row.evidence_key?<img src={`/api/v1/recognition/evidence/${encodeURIComponent(row.evidence_key)}`} alt={`Ảnh minh chứng ${row.full_name}`} loading="lazy"/>:<div className="evidence-expired">Ảnh minh chứng đã hết hạn</div>}
      <div><h3>{row.full_name}</h3><p>{row.student_id||row.person_id}</p><p>{new Date(row.recognized_at).toLocaleString('vi-VN')} · {row.camera_name}</p><p>{row.track_id==null?'Không có Track ID':`Track #${row.track_id}`}</p></div>
    </article>)}</div>:<div className="table-scroll"><table><thead><tr><th>Ngày</th><th>Mã</th><th>Họ tên</th><th>Vào</th><th>Ra</th><th>Camera</th></tr></thead><tbody>{attendance.map(row=><tr key={row.id}><td>{row.attendance_date}</td><td>{row.student_id||row.person_id}</td><td>{row.full_name}</td><td>{new Date(row.first_seen_at).toLocaleTimeString('vi-VN')}</td><td>{row.exited_at?new Date(row.exited_at).toLocaleTimeString('vi-VN'):'Đang có mặt'}</td><td>{row.camera_name}</td></tr>)}</tbody></table>{attendance.length===0&&<p>Chưa có dữ liệu điểm danh trong bộ lọc.</p>}</div>}
    {tab !== 'faces' && <p className="field-hint">Chỉ danh tính đã xác minh mới được ghi điểm danh. Điểm danh tự khử trùng theo người và ngày tại múi giờ Việt Nam; ảnh minh chứng tự xóa theo thời hạn camera.</p>}
  </section>;
}
