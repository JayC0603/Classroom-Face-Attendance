import { useEffect, useState } from "react";
import { getMyProfile, listMyAttendance, listMyEvents, listMyFaceImages, loadPrivateImage, myEventEvidenceUrl, myFaceImageUrl, uploadMyFaceImages, type StudentAttendance, type StudentEvent, type StudentProfile } from "../../api/student";
import type { FaceImagePublic, UploadResult } from "../../api/registry";
import type { WorkspacePage } from "../../app/Sidebar";

export function StudentWorkspace({page}:{page:WorkspacePage}) {
  const [profile,setProfile]=useState<StudentProfile|null>(null); const [images,setImages]=useState<FaceImagePublic[]>([]);
  const [attendance,setAttendance]=useState<StudentAttendance[]>([]); const [events,setEvents]=useState<StudentEvent[]>([]);
  const [error,setError]=useState(""); const [busy,setBusy]=useState(false); const [result,setResult]=useState<UploadResult[]|null>(null);
  useEffect(()=>{let alive=true; setError("");
    const load=page==="me/profile"?getMyProfile().then(v=>{if(alive)setProfile(v);}):page==="me/enrollment"?listMyFaceImages().then(v=>{if(alive)setImages(v);}):page==="me/attendance"?listMyAttendance().then(v=>{if(alive)setAttendance(v);}):listMyEvents().then(v=>{if(alive)setEvents(v);});
    load.catch(e=>{if(alive)setError(e instanceof Error?e.message:"Không tải được dữ liệu");}); return ()=>{alive=false;};
  },[page]);
  async function upload(files:FileList|null){if(!files?.length)return;setBusy(true);setError("");try{setResult(await uploadMyFaceImages(Array.from(files)));setImages(await listMyFaceImages());}catch(e){setError(e instanceof Error?e.message:"Tải ảnh thất bại");}finally{setBusy(false);}}
  return <section className="role-workspace">
    {error&&<p role="alert" className="form-error">{error}</p>}
    {page==="me/profile"&&<article className="workspace-card"><h2>Hồ sơ của tôi</h2>{profile?<><p className="student-name">{profile.full_name}</p><p>Mã sinh viên: {profile.student_id??"Chưa liên kết"}</p><p>Trạng thái đối chiếu: <strong>{profile.recognition_ready?"Đã sẵn sàng":"Chưa có ảnh được duyệt để đối chiếu"}</strong></p></>:<p role="status">Đang tải hồ sơ…</p>}</article>}
    {page==="me/enrollment"&&<><article className="workspace-card"><h2>Đăng ký khuôn mặt</h2><p>Ảnh mới sẽ chờ duyệt. Chỉ ảnh được duyệt mới có thể dùng để đối chiếu.</p><label className="primary-action">{busy?"Đang tải ảnh…":"Chọn ảnh để đăng ký"}<input aria-label="Chọn ảnh khuôn mặt" type="file" accept="image/jpeg,image/png" multiple disabled={busy} onChange={e=>{void upload(e.currentTarget.files);e.currentTarget.value="";}} hidden /></label>{result&&<div role="status" className="upload-results">{result.map((item,index)=><p key={`${item.filename}-${index}`}>{item.filename}: {item.status==="STORED_PENDING"?"Đang chờ duyệt":item.error_category??"Không tải được"}</p>)}</div>}</article><div className="student-image-grid">{images.map(image=><article className="workspace-card" key={image.id}><h3>{image.original_filename}</h3><p className="status-chip">{reviewLabel(image.review_state)}</p><img src={myFaceImageUrl(image.id)} alt={`Ảnh đăng ký ${image.original_filename}`} loading="lazy" /></article>)}{images.length===0&&<p>Chưa có ảnh đăng ký.</p>}</div></>}
    {page==="me/attendance"&&<article className="workspace-card"><h2>Điểm danh của tôi</h2>{attendance.length===0?<p>Chưa có lượt điểm danh.</p>:<div className="table-wrap"><table><thead><tr><th>Ngày</th><th>Camera</th><th>Lần đầu</th><th>Lần cuối</th><th>Trạng thái</th></tr></thead><tbody>{attendance.map(row=><tr key={row.id}><td>{row.attendance_date}</td><td>{row.camera_name??"—"}</td><td>{row.first_seen_at??"—"}</td><td>{row.last_seen_at??"—"}</td><td>Đã ghi nhận</td></tr>)}</tbody></table></div>}</article>}
    {page==="me/history"&&<article className="workspace-card"><h2>Lịch sử nhận diện</h2>{events.length===0?<p>Chưa có lịch sử nhận diện.</p>:<div className="table-wrap"><table><thead><tr><th>Thời điểm</th><th>Camera</th><th>Minh chứng</th></tr></thead><tbody>{events.map(event=><tr key={event.id}><td>{event.recognized_at}</td><td>{event.camera_name??"—"}</td><td><EvidenceLink url={myEventEvidenceUrl(event.id)}/></td></tr>)}</tbody></table></div>}</article>}
  </section>;
}

function reviewLabel(state: string | undefined) { return state === "APPROVED" ? "Đã duyệt" : state === "REJECTED" ? "Từ chối" : "Chờ duyệt"; }
function EvidenceLink({url}:{url:string}){const [src,setSrc]=useState("");const [error,setError]=useState("");const [busy,setBusy]=useState(false);useEffect(()=>()=>{if(src)URL.revokeObjectURL(src);},[src]);async function load(){setBusy(true);setError("");try{setSrc(await loadPrivateImage(url));}catch{setError("Không tải được ảnh minh chứng");}finally{setBusy(false);}}return src?<a href={src} target="_blank" rel="noreferrer">Mở ảnh minh chứng</a>:<><button type="button" disabled={busy} onClick={()=>void load()}>{busy?"Đang tải…":"Xem minh chứng"}</button>{error&&<span role="alert">{error}</span>}</>;}
