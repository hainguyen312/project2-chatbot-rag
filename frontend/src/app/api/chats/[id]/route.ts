import { NextRequest, NextResponse } from "next/server";
import { deleteChatById, getChatById, updateChat } from "@/lib/chats";

type RouteContext = {
  params: Promise<{ id: string }>;
};

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

export async function GET(req: NextRequest, context: RouteContext) {
  const uid = requireUserId(req);
  if (uid instanceof NextResponse) return uid;
  const { id } = await context.params;
  const chat = await getChatById(id, uid);
  if (!chat) {
    return NextResponse.json({ error: "Không tìm thấy hội thoại" }, { status: 404 });
  }
  return NextResponse.json({ chat });
}

export async function PATCH(req: NextRequest, context: RouteContext) {
  const uid = requireUserId(req);
  if (uid instanceof NextResponse) return uid;
  const { id } = await context.params;
  const body = (await req.json().catch(() => ({}))) as {
    title?: string;
    pinned?: boolean;
    messages?: { role: "user" | "assistant"; content: string }[];
  };

  const patch: {
    title?: string;
    pinned?: boolean;
    messages?: { role: "user" | "assistant"; content: string }[];
  } = {};

  if ("title" in body) patch.title = body.title;
  if ("pinned" in body) patch.pinned = body.pinned;
  if ("messages" in body) patch.messages = body.messages;

  const chat = await updateChat(id, uid, patch);

  if (!chat) {
    return NextResponse.json({ error: "Không tìm thấy hội thoại" }, { status: 404 });
  }
  return NextResponse.json({ chat });
}

export async function DELETE(req: NextRequest, context: RouteContext) {
  const uid = requireUserId(req);
  if (uid instanceof NextResponse) return uid;
  const { id } = await context.params;
  const ok = await deleteChatById(id, uid);
  if (!ok) {
    return NextResponse.json({ error: "Không tìm thấy hội thoại" }, { status: 404 });
  }
  return NextResponse.json({ ok: true });
}
