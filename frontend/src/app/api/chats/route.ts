import { NextRequest, NextResponse } from "next/server";
import { createChat, listChats } from "@/lib/chats";
import { requireUser } from "@/lib/server-auth";

/**
 * uid lấy từ Firebase ID token đã verify ở server (Authorization: Bearer …).
 * Không tin header x-user-id / body do client gửi — trước đây đọc/xoá được chat người khác.
 */
async function requireUserId(req: NextRequest): Promise<string | NextResponse> {
  const auth = await requireUser(req);
  return auth instanceof NextResponse ? auth : auth.uid;
}

export async function GET(req: NextRequest) {
  const uid = await requireUserId(req);
  if (uid instanceof NextResponse) return uid;
  try {
    const chats = await listChats(uid);
    return NextResponse.json({ chats });
  } catch (error) {
    return NextResponse.json(
      { error: "Không thể tải danh sách hội thoại", detail: String(error) },
      { status: 500 }
    );
  }
}

export async function POST(req: NextRequest) {
  const uid = await requireUserId(req);
  if (uid instanceof NextResponse) return uid;
  try {
    const body = (await req.json().catch(() => ({}))) as { title?: string };
    const chat = await createChat(uid, body.title || "Cuộc trò chuyện mới");
    return NextResponse.json({ chat }, { status: 201 });
  } catch (error) {
    return NextResponse.json(
      { error: "Không thể tạo hội thoại", detail: String(error) },
      { status: 500 }
    );
  }
}
