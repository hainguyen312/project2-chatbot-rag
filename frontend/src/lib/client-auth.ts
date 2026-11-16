"use client";

import { getFirebaseAuth, isFirebaseConfigured } from "@/lib/firebase";

/**
 * Header Authorization với Firebase ID token của người đang đăng nhập (trình duyệt).
 * Server verify token và lấy uid từ đó — không gửi uid riêng. Chưa đăng nhập → {}.
 */
export async function authHeader(): Promise<Record<string, string>> {
  if (!isFirebaseConfigured()) return {};
  const user = getFirebaseAuth().currentUser;
  if (!user) return {};
  try {
    return { Authorization: `Bearer ${await user.getIdToken()}` };
  } catch {
    return {};
  }
}
