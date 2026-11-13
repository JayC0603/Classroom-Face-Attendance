import {act, cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {afterEach, expect, it, vi} from 'vitest';
import {App} from './App';

const authMocks=vi.hoisted(()=>({getCurrentSession:vi.fn(),signIn:vi.fn(),signOut:vi.fn()}));
const roleApiMocks=vi.hoisted(()=>({listTeacherClasses:vi.fn(async()=>[]),listTeacherCameras:vi.fn(async()=>[]),listPendingFaceReviews:vi.fn(async()=>[]),getMyProfile:vi.fn(async()=>({id:'person-1',full_name:'Student One',student_id:'S1',recognition_ready:true})),listMyFaceImages:vi.fn(async()=>[]),listMyAttendance:vi.fn(async()=>[])}));
vi.mock('../api/auth',()=>authMocks);
vi.mock('../api/classes',async()=>({...await vi.importActual('../api/classes'),...roleApiMocks}));
vi.mock('../api/student',()=>roleApiMocks);
const disconnect=vi.fn();
vi.mock('../api/client',()=>({getHealth:vi.fn(async()=>({status:'ok',service:'API'})),connectRuntimeStatus:vi.fn(()=>disconnect),setCsrfToken:vi.fn(),setUnauthorizedHandler:vi.fn()}));
vi.mock('../features/monitoring/MonitoringPage',()=>({MonitoringPage:()=> <div>Monitoring workspace</div>}));
vi.mock('../features/cameras/CameraPanel',()=>({CameraPanel:()=> <div>Camera workspace</div>}));
vi.mock('../features/registry/FaceRegistryPanel',()=>({FaceRegistryPanel:()=> <div>Registry workspace</div>}));
vi.mock('../features/models/ModelsPanel',()=>({ModelsPanel:()=> <div>Model workspace</div>}));
const admin={user:{user_id:'admin-1',email:'admin@example.test',role:'ADMIN',person_id:null},csrf_token:'csrf'};
const teacher={user:{user_id:'teacher-1',email:'teacher@example.test',role:'TEACHER',person_id:null},csrf_token:'csrf'};
const student={user:{user_id:'student-1',email:'student@example.test',role:'STUDENT',person_id:'person-1'},csrf_token:'csrf'};

it('collapses desktop navigation while keeping destinations accessible', async () => {
 authMocks.getCurrentSession.mockResolvedValue(admin);
 const user = userEvent.setup();
 render(<App/>);
 const toggle = await screen.findByRole('button', {name:'Thu gọn điều hướng'});
 await user.click(toggle);
 expect(screen.getByRole('button', {name:'Mở rộng điều hướng'}).getAttribute('aria-expanded')).toBe('false');
 expect(document.querySelector('.app-shell')?.classList.contains('nav-compact')).toBe(true);
 await user.click(screen.getByRole('link', {name:'Camera'}));
 expect(await screen.findByText('Camera workspace')).toBeTruthy();
 await user.click(screen.getByRole('button', {name:'Mở rộng điều hướng'}));
 expect(document.querySelector('.app-shell')?.classList.contains('nav-compact')).toBe(false);
});

it('opens the mobile drawer, traps focus and closes with Escape or navigation', async () => {
 const previousWidth = window.innerWidth;
 Object.defineProperty(window, 'innerWidth', {configurable:true, value:390});
 authMocks.getCurrentSession.mockResolvedValue(admin);
 const user = userEvent.setup();
 try {
  render(<App/>);
  const toggle = await screen.findByRole('button', {name:'Mở điều hướng'});
  expect(document.getElementById('workspace-sidebar')?.hasAttribute('inert')).toBe(true);
  await user.click(toggle);
  const close = screen.getAllByRole('button', {name:'Đóng điều hướng'}).find(button=>button.classList.contains('sidebar-close'))!;
  expect(document.activeElement).toBe(close);
  expect(document.querySelector('.workspace-shell')?.hasAttribute('inert')).toBe(true);
  expect(document.body.style.overflow).toBe('hidden');
  await user.tab({shift:true});
  expect(document.activeElement).toBe(screen.getByRole('link', {name:'Trạng thái hệ thống'}));
  fireEvent.keyDown(window, {key:'Escape'});
  expect(document.querySelector('.workspace-sidebar')?.classList.contains('is-open')).toBe(false);
  expect(document.activeElement).toBe(toggle);
  await user.click(toggle);
  await user.click(screen.getByRole('link', {name:'Camera'}));
  expect(await screen.findByText('Camera workspace')).toBeTruthy();
  expect(document.querySelector('.workspace-sidebar')?.classList.contains('is-open')).toBe(false);
  expect(document.body.style.overflow).toBe('');
 } finally {
  Object.defineProperty(window, 'innerWidth', {configurable:true, value:previousWidth});
 }
});
afterEach(()=>{cleanup();window.history.replaceState(null,'','/');localStorage.clear();document.documentElement.removeAttribute('data-theme');vi.clearAllMocks();authMocks.getCurrentSession.mockResolvedValue(admin);});

it('shows sign in only when anonymous and opens no admin data stream',async()=>{authMocks.getCurrentSession.mockRejectedValue(new Error('AUTH_REQUIRED'));render(<App/>);expect(await screen.findByRole('heading',{name:'\u0110\u0103ng nh\u1eadp'})).toBeTruthy();expect(screen.queryByRole('navigation')).toBeNull();expect(disconnect).not.toHaveBeenCalled();});
it('shows the official university mark and a theme control on sign-in',async()=>{authMocks.getCurrentSession.mockRejectedValue(new Error('AUTH_REQUIRED'));render(<App/>);expect(await screen.findByRole('img',{name:'Tr\u01b0\u1eddng \u0110\u1ea1i h\u1ecdc FPT'})).toBeTruthy();expect(screen.getByRole('button',{name:'B\u1eadt giao di\u1ec7n t\u1ed1i'})).toBeTruthy();});
it('signs in and out through the server session contract',async()=>{const user=userEvent.setup();authMocks.getCurrentSession.mockRejectedValue(new Error('AUTH_REQUIRED'));authMocks.signIn.mockResolvedValue(admin);authMocks.signOut.mockResolvedValue(undefined);render(<App/>);await screen.findByRole('heading',{name:'\u0110\u0103ng nh\u1eadp'});await user.type(screen.getByLabelText('Email'),'admin@example.test');await user.type(screen.getByLabelText('M\u1eadt kh\u1ea9u'),'secure-password');await user.click(screen.getByRole('button',{name:'\u0110\u0103ng nh\u1eadp'}));expect(await screen.findByText('admin@example.test')).toBeTruthy();expect(authMocks.signIn).toHaveBeenCalledWith('admin@example.test','secure-password');await user.click(screen.getByRole('button',{name:'\u0110\u0103ng xu\u1ea5t'}));expect(await screen.findByRole('heading',{name:'\u0110\u0103ng nh\u1eadp'})).toBeTruthy();expect(authMocks.signOut).toHaveBeenCalledTimes(1);});
it('clears the role workspace when the server session expires',async()=>{vi.useFakeTimers();authMocks.getCurrentSession.mockResolvedValue({...student,expires_at:new Date(Date.now()+1000).toISOString()});render(<App/>);await act(async()=>{await Promise.resolve();await Promise.resolve();});expect(screen.getByText('student@example.test')).toBeTruthy();await act(async()=>{await vi.advanceTimersByTimeAsync(1001);});expect(screen.getByRole('heading',{name:'\u0110\u0103ng nh\u1eadp'})).toBeTruthy();vi.useRealTimers();});
it('denies another role deep links without starting the admin status stream',async()=>{authMocks.getCurrentSession.mockResolvedValue(teacher);window.history.replaceState(null,'','/#cameras');render(<App/>);expect(await screen.findByRole('heading',{name:'Kh\u00f4ng c\u00f3 quy\u1ec1n truy c\u1eadp'})).toBeTruthy();expect(screen.getByRole('navigation',{name:'\u0110i\u1ec1u h\u01b0\u1edbng ch\u00ednh'})).toBeTruthy();expect(screen.getByRole('link',{name:'L\u1edbp c\u1ee7a t\u00f4i'})).toBeTruthy();expect(screen.queryByRole('link',{name:'Camera tr\u1ef1c ti\u1ebfp'})).toBeNull();expect(disconnect).not.toHaveBeenCalled();});
it('shows student self-service navigation and loads only the student profile',async()=>{authMocks.getCurrentSession.mockResolvedValue(student);window.history.replaceState(null,'','/#me/profile');render(<App/>);expect(await screen.findByRole('heading',{level:1,name:'H\u1ed3 s\u01a1 c\u1ee7a t\u00f4i'})).toBeTruthy();expect(screen.getByRole('link',{name:'\u0110\u0103ng k\u00fd khu\u00f4n m\u1eb7t'})).toBeTruthy();expect(screen.getByRole('link',{name:'\u0110i\u1ec3m danh c\u1ee7a t\u00f4i'})).toBeTruthy();expect(screen.queryByRole('link',{name:'Camera tr\u1ef1c ti\u1ebfp'})).toBeNull();expect(await screen.findByText('Student One')).toBeTruthy();expect(roleApiMocks.getMyProfile).toHaveBeenCalledTimes(1);expect(roleApiMocks.listTeacherClasses).not.toHaveBeenCalled();});
it('keeps the Admin pages navigable and connects runtime status',async()=>{const user=userEvent.setup();render(<App/>);expect(await screen.findByRole('navigation',{name:'\u0110i\u1ec1u h\u01b0\u1edbng ch\u00ednh'})).toBeTruthy();expect(screen.getByText('Monitoring workspace')).toBeTruthy();await user.click(screen.getByRole('link',{name:'H\u1ed3 s\u01a1 khu\u00f4n m\u1eb7t'}));await screen.findByText('Registry workspace');expect(screen.queryByText('Camera workspace')).toBeNull();await user.click(screen.getByRole('link',{name:'M\u00f4 h\u00ecnh nh\u1eadn di\u1ec7n'}));await screen.findByText('Model workspace');expect(disconnect).not.toHaveBeenCalled();});
it('supports admin deep links and releases runtime status on unmount',async()=>{window.history.replaceState(null,'','/#system');const {unmount}=render(<App/>);expect(await screen.findByRole('heading',{level:1,name:'Tr\u1ea1ng th\u00e1i h\u1ec7 th\u1ed1ng'})).toBeTruthy();window.history.replaceState(null,'','/#cameras');window.dispatchEvent(new HashChangeEvent('hashchange'));await waitFor(()=>expect(screen.getByText('Camera workspace')).toBeTruthy());expect(disconnect).not.toHaveBeenCalled();unmount();expect(disconnect).toHaveBeenCalledTimes(1);});
