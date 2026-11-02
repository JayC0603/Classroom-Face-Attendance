import { useTheme } from './ThemeProvider';

export function ThemeToggle() {
  const { theme, toggleTheme } = useTheme();
  const next = theme === 'light' ? 'dark' : 'light';
  return <button className="theme-toggle" type="button" onClick={toggleTheme} aria-label={`Bật giao diện ${next === 'dark' ? 'tối' : 'sáng'}`} title={`Giao diện ${next === 'dark' ? 'tối' : 'sáng'}`}>
    <span aria-hidden="true">{theme === 'light' ? '☾' : '☀'}</span><span className="theme-toggle-label">{next === 'dark' ? 'Giao diện tối' : 'Giao diện sáng'}</span>
  </button>;
}
