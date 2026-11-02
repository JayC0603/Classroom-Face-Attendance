import { useEffect, useState } from "react";
import { connectRuntimeStatus, getHealth, type HealthStatus } from "../api/client";
import type { RuntimeStatus } from "../api/contracts";
import { AdminManagement } from "../features/admin/AdminManagement";
import { LoginPage } from "../features/auth/LoginPage";
import { SessionProvider, useSession } from "../features/auth/SessionProvider";
import { CameraPanel } from "../features/cameras/CameraPanel";
import {LivewallPage} from "../features/monitoring/LivewallPage";
import { MonitoringPage } from "../features/monitoring/MonitoringPage";
import { AlertsPage } from "../features/alerts/AlertsPage";
import { RecognitionHistoryPanel } from "../features/history/RecognitionHistoryPanel";
import { ModelsPanel } from "../features/models/ModelsPanel";
import { FaceRegistryPanel } from "../features/registry/FaceRegistryPanel";
import { StudentWorkspace } from "../features/student/StudentWorkspace";
import { TeacherWorkspace } from "../features/teacher/TeacherWorkspace";
import { ThemeToggle } from "../ui/ThemeToggle";
import { ThemeProvider } from "../ui/ThemeProvider";
import { defaultPage, pagesForRole, Sidebar, workspacePages, type WorkspacePage } from "./Sidebar";

function AppContent() {
 const session=useSession();
 if(session.status==="loading")return <main className="session-loading" role="status">Đang kiểm tra phiên đăng nhập…</main>;
 if(session.status==="anonymous"||session.status==="error")return <LoginPage onSignIn={session.signIn} error={session.status==="error"?session.error:null} onRetry={session.retry}/>;
 if(!session.user)return <LoginPage onSignIn={session.signIn} error={null} onRetry={session.retry}/>;
 return <Workspace key={session.user.user_id} user={session.user} onSignOut={session.signOut}/>;
}

function Workspace({user,onSignOut}:{user:NonNullable<ReturnType<typeof useSession>["user"]>;onSignOut:()=>Promise<void>}) {
 const rolePages=pagesForRole(user.role);
 const initial=()=>{const hash=window.location.hash.slice(1);return hash&&Object.hasOwn(workspacePages,hash)?hash as WorkspacePage:defaultPage(user.role);};
 const [page,setPage]=useState<WorkspacePage>(initial);
 const [health,setHealth]=useState<HealthStatus|null>(null);const [error,setError]=useState<string|null>(null);const [runtimeStatuses,setRuntimeStatuses]=useState<RuntimeStatus[]>([]);const [menuOpen,setMenuOpen]=useState(false);const [signingOut,setSigningOut]=useState(false);
 const allowed=rolePages.includes(page);const info=workspacePages[page];
 useEffect(()=>{const navigate=()=>{const hash=window.location.hash.slice(1);if(Object.hasOwn(workspacePages,hash))setPage(hash as WorkspacePage);setMenuOpen(false);};window.addEventListener("hashchange",navigate);return()=>window.removeEventListener("hashchange",navigate);},[]);
 useEffect(()=>{if(!menuOpen)return;const previous=document.activeElement as HTMLElement|null;const elements=Array.from(document.querySelectorAll<HTMLElement>("#workspace-sidebar a, #workspace-sidebar button"));elements[0]?.focus();const keydown=(event:KeyboardEvent)=>{if(event.key==="Escape"){setMenuOpen(false);return;}if(event.key!=="Tab")return;const first=elements[0],last=elements[elements.length-1];if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus();}};const resize=()=>{if(window.innerWidth>900)setMenuOpen(false);};window.addEventListener("keydown",keydown);window.addEventListener("resize",resize);return()=>{window.removeEventListener("keydown",keydown);window.removeEventListener("resize",resize);previous?.focus();};},[menuOpen]);
 useEffect(()=>{
  if(user.role!=="ADMIN")return;
  let disposed=false; let timer: ReturnType<typeof setTimeout>;
  async function refreshHealth(){try{const value=await getHealth();if(!disposed){setHealth(value);setError(null);}}catch{if(!disposed){setHealth(null);setError("API kh\u00f4ng ph\u1ea3n h\u1ed3i");}}finally{if(!disposed)timer=setTimeout(()=>void refreshHealth(),15000);}}
  void refreshHealth();
  const disconnect=connectRuntimeStatus(next=>setRuntimeStatuses(current=>[...current.filter(item=>item.camera_id!==next.camera_id).slice(-255),next]));
  return()=>{disposed=true;clearTimeout(timer);disconnect();};
 },[user.role]);
 const apiOnline=health?.status==="ok";
 return <div className="app-shell"><a className="skip-link" href="#workspace-content" onClick={event=>{event.preventDefault();document.getElementById("workspace-content")?.focus();}}>Đi đến nội dung</a>
  {menuOpen&&<button className="sidebar-backdrop" aria-label="Đóng điều hướng" onClick={()=>setMenuOpen(false)}/>}
  <Sidebar page={page} open={menuOpen} onNavigate={()=>setMenuOpen(false)} role={user.role}/>
  <div className="workspace-shell" inert={menuOpen}><header className="workspace-topbar"><button className="menu-toggle" aria-label="Mở điều hướng" aria-expanded={menuOpen} aria-controls="workspace-sidebar" onClick={()=>setMenuOpen(!menuOpen)}>☰</button><span className="workspace-breadcrumb">Không gian làm việc <span>/</span> <strong>{info.label}</strong></span>{user.role==="ADMIN"&&<span className={`api-status ${apiOnline?'is-online':''}`} role="status"><i aria-hidden="true"/>{apiOnline?"API đã kết nối":error??"Đang kiểm tra API…"}</span>}<span className="session-user">{user.email}</span><ThemeToggle/><button className="secondary-button" disabled={signingOut} onClick={()=>{setSigningOut(true);void onSignOut().finally(()=>setSigningOut(false));}}>{signingOut?"Đăng xuất…":"Đăng xuất"}</button></header>
   <main className="workspace-content" id="workspace-content" tabIndex={-1}><header className="page-heading" key={page}><p className="eyebrow">{info.group}</p><h1>{info.label}</h1><p>{info.description}</p></header>
    {!allowed?<section className="workspace-card access-denied" role="alert"><h2>Không có quyền truy cập</h2><p>Tài khoản của bạn không có quyền mở mục này.</p><a className="primary-action" href={`#${defaultPage(user.role)}`}>Về không gian của tôi</a></section>:
      page==="overview"||page==="monitoring"?<MonitoringPage statuses={runtimeStatuses} overview={page==="overview"}/>:page==="livewall"?<LivewallPage statuses={runtimeStatuses}/>:page==="alerts"?<AlertsPage/>:page==="cameras"?<CameraPanel runtimeStatuses={runtimeStatuses}/>:page==="people"?<FaceRegistryPanel/>:page==="history"?<RecognitionHistoryPanel/>:page==="models"?<ModelsPanel/>:page==="system"?<SystemStatus statuses={runtimeStatuses}/>:page==="accounts"||page==="classes"?<AdminManagement page={page}/>:page.startsWith("teacher/")?<TeacherWorkspace page={page}/>:<StudentWorkspace page={page}/>}</main>
  </div>
 </div>;
}

function SystemStatus({statuses}:{statuses:RuntimeStatus[]}){return <section aria-labelledby="runtime-heading"><h2 id="runtime-heading">Hoạt động của worker</h2>{statuses.length===0?<article className="empty-state"><h3>Chưa nhận được trạng thái</h3><p>Trạng thái worker sẽ xuất hiện khi có dữ liệu gửi về. Bạn có thể kiểm tra camera tại mục Giám sát.</p><a href="#cameras">Đến camera trực tiếp →</a></article>:<div className="grid">{statuses.map(runtime=><article key={`${runtime.worker_id}:${runtime.camera_id}`}><p className="eyebrow">{runtime.camera_state}</p><h3>Camera đang báo cáo</h3><p>{runtime.error_category==="PERSON_DETECTOR_NOT_CONFIGURED"?"Đang cấp hình cho bộ so sánh":runtime.worker_health}</p><dl><dt>FPS nguồn hình</dt><dd>{runtime.input_fps.toFixed(1)}</dd><dt>Frame bị bỏ</dt><dd>{runtime.dropped_frames}</dd><dt>Track đang theo dõi</dt><dd>{runtime.active_tracks}</dd><dt>Bộ đệm</dt><dd>{runtime.ring_buffer_frames} / {runtime.ring_buffer_capacity}</dd></dl><details className="runtime-details"><summary>Thông tin kỹ thuật</summary><p>Camera: {runtime.camera_id}</p><p>Worker: {runtime.worker_id}</p><p>Phiên bản cấu hình: {runtime.active_config_revision??"Chưa có"}</p></details></article>)}</div>}</section>;}

export function App(){return <ThemeProvider><SessionProvider><AppContent/></SessionProvider></ThemeProvider>;}
