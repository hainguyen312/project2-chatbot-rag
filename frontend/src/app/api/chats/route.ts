import { NextRequest, NextResponse } from "next/server";
import { createChat, listChats } from "@/lib/chats";

function requireUserId(req: NextRequest): string | NextResponse {
  const uid = req.headers.get("x-user-id")?.trim();
  if (!uid) {
    return NextResponse.json(
      { error: "Thiếu user_id (x-user-id header)" },
      { status: 400 }
    );
  }
  return uid;
}

export async function GET(req: NextRequest) {
  const uid = requireUserId(req);
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
  const uid = requireUserId(req);
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
