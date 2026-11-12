"use client";
import { useCallback, useEffect, useSyncExternalStore } from "react";

/** Khoá lưu lựa chọn nền — dùng chung cho trang chat và văn phòng 3D. */
export const THEME_KEY = "vnlaw-theme";
const CHANGE_EVENT = "vnlaw-theme-change";
const MEDIA = "(prefers-color-scheme: dark)";

/** Lựa chọn đã lưu → nếu chưa có thì theo hệ điều hành. */
function readDark(): boolean {
  try {
    const saved = localStorage.getItem(THEME_KEY);
    if (saved) return saved === "dark";
  } catch {}
  return window.matchMedia?.(MEDIA).matches ?? false;
}

function subscribe(onChange: () => void) {
  const media = window.matchMedia?.(MEDIA);
  window.addEventListener(CHANGE_EVENT, onChange);
  window.addEventListener("storage", onChange); // đổi ở tab khác → tab này đổi theo
  media?.addEventListener("change", onChange);
  return () => {
    window.removeEventListener(CHANGE_EVENT, onChange);
    window.removeEventListener("storage", onChange);
    media?.removeEventListener("change", onChange);
  };
}

/**
 * Chế độ nền sáng/tối, đồng bộ giữa các trang và các tab.
 * Bật/tắt class "dark" trên <html> để các token trong theme.css đổi theo.
 */
export function useThemeMode(): [boolean, () => void] {
  const dark = useSyncExternalStore(subscribe, readDark, () => false);

  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark);
  }, [dark]);

  const toggle = useCallback(() => {
    try { localStorage.setItem(THEME_KEY, readDark() ? "light" : "dark"); } catch {}
    window.dispatchEvent(new Event(CHANGE_EVENT));
  }, []);

  return [dark, toggle];
}
