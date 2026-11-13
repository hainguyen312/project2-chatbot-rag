/** Id ẩn danh demo_… lưu trong localStorage — cùng khoá với trang chat để dữ liệu gắn đúng người. */
export const ANON_ID_KEY = "rag_user_id";

export function getOrCreateAnonId(): string {
  if (typeof window === "undefined") return "";
  try {
    let id = window.localStorage.getItem(ANON_ID_KEY);
    if (!id) {
      id = `demo_${crypto.randomUUID().slice(0, 8)}`;
      window.localStorage.setItem(ANON_ID_KEY, id);
    }
    return id;
  } catch {
    return "";
  }
}
