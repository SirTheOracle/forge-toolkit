import "@testing-library/jest-dom/vitest";
import { afterEach, vi } from "vitest";
import { cleanup } from "@testing-library/react";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

// Node 25 exposes an incomplete ambient storage object; use browser-shaped isolated stores.
for (const name of ["localStorage", "sessionStorage"] as const) {
  if (typeof window[name]?.clear !== "function") {
    const values = new Map<string, string>();
    Object.defineProperty(window, name, { configurable: true, value: {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => { values.set(key, value); },
      removeItem: (key: string) => { values.delete(key); },
      clear: () => values.clear(), key: (index: number) => [...values.keys()][index] ?? null,
      get length() { return values.size; },
    } });
  }
}