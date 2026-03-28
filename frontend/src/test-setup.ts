/**
 * Global test setup for Vitest + React Testing Library.
 * Loaded before every test file via vite.config.ts → test.setupFiles.
 */
import '@testing-library/jest-dom';
import { vi } from 'vitest';

// ── Browser APIs not available in jsdom ────────────────────────────────────

// URL.createObjectURL / revokeObjectURL are needed by downloadFile()
Object.defineProperty(globalThis, 'URL', {
  writable: true,
  value: class extends URL {
    static createObjectURL = vi.fn(() => 'blob:mock-url-0000');
    static revokeObjectURL = vi.fn();
  },
});

// matchMedia (used by some third-party UI libs)
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })),
});

// ResizeObserver (used by @react-three/fiber canvas measurement)
globalThis.ResizeObserver = class ResizeObserver {
  observe = vi.fn();
  unobserve = vi.fn();
  disconnect = vi.fn();
};

// Silence noisy console.log/warn in tests (errors still surface)
vi.spyOn(console, 'log').mockReturnValue(undefined);
vi.spyOn(console, 'warn').mockReturnValue(undefined);

// Suppress jsdom "Not implemented: navigation" unhandled errors (from anchor clicks on blob URLs).
const _origConsoleError = console.error.bind(console);
vi.spyOn(console, 'error').mockImplementation((...args: unknown[]) => {
  const msg = String(args[0] ?? '');
  if (msg.includes('Not implemented') || msg.includes('navigation')) return;
  _origConsoleError(...args);
});

// jsdom fires an `error` window event for "Not implemented: navigation" when
// an anchor with a blob: href is clicked.  Suppress it so vitest exits 0.
window.addEventListener('error', (e) => {
  if (e.message?.includes('Not implemented') || e.error?.message?.includes('Not implemented')) {
    e.preventDefault();
    e.stopPropagation();
  }
});

// jsdom VirtualConsole writes "Not implemented: navigation" directly to process.stderr
// (asynchronously, after anchor.click()). Intercept stderr to prevent vitest exit code 1.
const _origStderrWrite = process.stderr.write.bind(process.stderr);
(process.stderr as any).write = function (chunk: any, ...args: any[]) {
  const msg = String(chunk);
  if (msg.includes('Not implemented') || msg.includes('navigation')) return true;
  return _origStderrWrite(chunk, ...args);
};
