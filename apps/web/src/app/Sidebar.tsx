import type { UserRole } from "../api/auth";
import fptLogo from "../assets/fpt-university-logo.png";

export const workspacePages = {
 overview:{label:"Tổng quan",group:"VẬN HÀNH",description:"Theo dõi nguồn hình và những sự kiện cần chú ý tại cơ sở.",roles:["ADMIN"]},
 monitoring:{label:"Giám sát trực tiếp",group:"VẬN HÀNH",description:"Quan sát camera và kết quả nhận diện trên cùng một khung hình.",roles:["ADMIN"]},
 livewall:{label:"Livewall",group:"V\u1eacN H\u00c0NH",description:"Quan s\u00e1t nhi\u1ec1u camera c\u00f9ng l\u00fac tr\u00ean m\u1ed9t m\u00e0n h\u00ecnh.",roles:["ADMIN"]},
 alerts:{label:"Cảnh báo",group:"VẬN HÀNH",description:"Kiểm tra người chưa khớp ảnh đăng ký và ảnh minh chứng từ camera.",roles:["ADMIN"]},
 cameras:{label:"Camera",group:"QUẢN LÝ",description:"Thiết lập nguồn hình, vùng giám sát và chính sách nhận diện cho từng camera.",roles:["ADMIN"]},
 models:{label:"Mô hình nhận diện",group:"THIẾT LẬP",description:"Quản lý model và cấu hình nhận diện.",roles:["ADMIN"]},
 people:{label:"Hồ sơ khuôn mặt",group:"QUẢN LÝ",description:"Quản lý hồ sơ và ảnh đăng ký khuôn mặt.",roles:["ADMIN"]},
 history:{label:"Lịch sử & điểm danh",group:"VẬN HÀNH",description:"Xem lịch sử xác minh và ảnh minh chứng.",roles:["ADMIN"]},
 accounts:{label:"Tài khoản",group:"QUẢN LÝ",description:"Tạo và quản lý tài khoản sử dụng hệ thống.",roles:["ADMIN"]},
 classes:{label:"Lớp học",group:"QUẢN LÝ",description:"Tạo lớp, phân công giáo viên và quản lý thành viên.",roles:["ADMIN"]},
 system:{label:"Trạng thái hệ thống",group:"THIẾT LẬP",description:"Theo dõi tình trạng worker và camera.",roles:["ADMIN"]},
 "teacher/classes":{label:"Lớp của tôi",group:"GIẢNG DẠY",description:"Quản lý buổi điểm danh và xem sĩ số lớp được phân công.",roles:["TEACHER"]},
 "teacher/review":{label:"Duyệt khuôn mặt",group:"GIẢNG DẠY",description:"Duyệt ảnh đăng ký của sinh viên thuộc lớp phụ trách.",roles:["TEACHER"]},
 "me/profile":{label:"Hồ sơ của tôi",group:"TÀI KHOẢN",description:"Thông tin hồ sơ sinh viên và trạng thái đăng ký.",roles:["STUDENT"]},
 "me/enrollment":{label:"Đăng ký khuôn mặt",group:"TÀI KHOẢN",description:"Tải ảnh và theo dõi trạng thái duyệt.",roles:["STUDENT"]},
 "me/attendance":{label:"Điểm danh của tôi",group:"HỌC TẬP",description:"Xem các buổi điểm danh đã được ghi nhận.",roles:["STUDENT"]},
 "me/history":{label:"Lịch sử nhận diện",group:"HỌC TẬP",description:"Xem lịch sử nhận diện và ảnh minh chứng của bạn.",roles:["STUDENT"]}
} as const;
export type WorkspacePage=keyof typeof workspacePages;
export function pagesForRole(role:UserRole):WorkspacePage[]{return (Object.keys(workspacePages) as WorkspacePage[]).filter(page=>(workspacePages[page].roles as readonly UserRole[]).includes(role));}
export function defaultPage(role:UserRole):WorkspacePage{return role==="ADMIN"?"overview":role==="TEACHER"?"teacher/classes":"me/profile";}
function NavIcon({page}: {page: WorkspacePage}) {
 const paths: Record<string, string> = {livewall:'M3 3h18v18H3z M3 12h18 M12 3v18', overview:'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z', monitoring:'M3 4h18v13H3z M8 21h8 M12 17v4', cameras:'M3 7h12v12H3z M15 11l6-4v12l-6-4 M6 4h6', alerts:'M12 3L2 21h20L12 3z M12 9v5 M12 17v1', people:'M8 8a4 4 0 1 0 8 0a4 4 0 1 0-8 0 M4 21v-2a8 8 0 0 1 16 0v2', models:'M12 3l9 5-9 5-9-5 9-5z M3 12l9 5 9-5 M3 16l9 5 9-5', system:'M3 12h4l3-8 4 16 3-8h4', classes:'M3 4h7l2 2 2-2h7v16h-7l-2 2-2-2H3z M12 6v16', history:'M4 8a9 9 0 1 1-1 7 M3 3v6h6 M12 7v6l4 2'};
 return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[page] ?? paths.people}/></svg>;
}
export function Sidebar({page,open,mobile,onNavigate,role}:{page:WorkspacePage;open:boolean;mobile:boolean;onNavigate:()=>void;role:UserRole}){
 const pages=pagesForRole(role);const groups=[...new Set(pages.map(key=>workspacePages[key].group))];
 return <aside id="workspace-sidebar" className={`workspace-sidebar ${open?'is-open':''}`} inert={mobile && !open}>
  <button className="sidebar-close" aria-label="Đóng điều hướng" onClick={onNavigate}>×</button>
  <a className="app-brand" href={`#${defaultPage(role)}`} onClick={onNavigate}><span className="brand-logo-frame"><img src={fptLogo} alt="Trường Đại học FPT" /></span><span className="brand-copy"><strong>Điểm danh thông minh</strong><small>{role==="ADMIN"?"Quản trị hệ thống":role==="TEACHER"?"Không gian giảng dạy":"Không gian sinh viên"}</small></span></a>
  <nav aria-label="Điều hướng chính">{groups.map(group=><section className="nav-group" key={group} aria-label={group}><h2>{group}</h2>{pages.filter(key=>workspacePages[key].group===group).map(key=><a key={key} href={`#${key}`} title={workspacePages[key].label} aria-label={workspacePages[key].label} onClick={onNavigate} aria-current={page===key?'page':undefined}><span className="nav-icon"><NavIcon page={key}/></span><span className="nav-label">{workspacePages[key].label}</span><span className="nav-chevron" aria-hidden="true">›</span></a>)}</section>)}</nav>
  <div className="sidebar-note"><strong>{role==="ADMIN"?"Quản trị tập trung":role==="TEACHER"?"Lớp học được phân công":"Thông tin cá nhân"}</strong><p>{role==="ADMIN"?"Quản lý camera, hồ sơ, lớp và tài khoản.":role==="TEACHER"?"Chỉ xem lớp và ảnh thuộc phạm vi phụ trách.":"Chỉ bạn mới xem được hồ sơ và lịch sử của mình."}</p></div>
 </aside>;
}
