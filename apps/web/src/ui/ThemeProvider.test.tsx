import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { ThemeProvider, useTheme } from './ThemeProvider';

function ThemeProbe() {
  const { theme, toggleTheme } = useTheme();
  return <button onClick={toggleTheme}>Đổi giao diện, hiện tại {theme}</button>;
}

afterEach(() => {
  cleanup();
  localStorage.clear();
  document.documentElement.removeAttribute('data-theme');
  vi.restoreAllMocks();
});

it('uses the operating-system preference until the user chooses a theme', () => {
  vi.stubGlobal('matchMedia', vi.fn().mockReturnValue({ matches: true }));
  render(<ThemeProvider><ThemeProbe /></ThemeProvider>);
  expect(screen.getByRole('button').textContent).toContain('dark');
  expect(document.documentElement.dataset.theme).toBe('dark');
  expect(localStorage.getItem('face-attendance-theme')).toBeNull();
});

it('restores the saved choice and persists a keyboard-operable toggle', () => {
  localStorage.setItem('face-attendance-theme', 'dark');
  render(<ThemeProvider><ThemeProbe /></ThemeProvider>);
  const button = screen.getByRole('button');
  expect(button.textContent).toContain('dark');
  fireEvent.click(button);
  expect(button.textContent).toContain('light');
  expect(localStorage.getItem('face-attendance-theme')).toBe('light');
  expect(document.documentElement.dataset.theme).toBe('light');
});
