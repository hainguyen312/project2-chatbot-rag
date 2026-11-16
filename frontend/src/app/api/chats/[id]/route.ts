import { NextRequest, NextResponse } from "next/server";
import { deleteChatById, getChatById, updateChat } from "@/lib/chats";
import { requireUser } from "@/lib/server-auth";

type RouteContext = {
  params: Promise<{ id: string }>;
};

/**
 * uid lấy từ Firebase ID token đã verify ở server (Authorization: Bearer …).
 * Không tin header x-user-id / body do client gửi — trước đây đọc/xoá được chat người khác.
 */
async function requireUserId(req: NextRequest): Promise<string | NextResponse> {
  const auth = await requireUser(req);
  return auth instanceof NextResponse ? auth : auth.uid;
}

export async function GET(req: NextRequest, context: RouteContext) {
  const uid = await requireUserId(req);
  if (uid instanceof NextResponse) return uid;
  const { id } = await context.params;
  const chat = await getChatById(id, uid);
  if (!chat) {
    return NextResponse.json({ error: "Không tìm thấy hội thoại" }, { status: 404 });
  }
  return NextResponse.json({ chat });
}

export async function PATCH(req: NextRequest, context: RouteContext) {
  const uid = await requireUserId(req);
  if (uid instanceof NextResponse) return uid;
  const { id } = await context.params;
  const body = (await req.json().catch(() => ({}))) as {
    title?: string;
    title_source?: string;
    pinned?: boolean;
    messages?: { role: "user" | "assistant"; content: string }[];
  };

  const patch: {
    title?: string;
    title_source?: "auto" | "llm" | "user";
    pinned?: boolean;
    messages?: { role: "user" | "assistant"; content: string }[];
  } = {};

  if ("title" in body) patch.title = body.title;
  if (body.title_source === "auto" || body.title_source === "llm" || body.title_source === "user") {
    patch.title_source = body.title_source;
  }
  if ("pinned" in body) patch.pinned = body.pinned;
  if ("messages" in body) patch.messages = body.messages;

  const chat = await updateChat(id, uid, patch);

  if (!chat) {
    return NextResponse.json({ error: "Không tìm thấy hội thoại" }, { status: 404 });
  }
  return NextResponse.json({ chat });
}

export async function DELETE(req: NextRequest, context: RouteContext) {
  const uid = await requireUserId(req);
  if (uid instanceof NextResponse) return uid;
  const { id } = await context.params;
  const ok = await deleteChatById(id, uid);
  if (!ok) {
    return NextResponse.json({ error: "Không tìm thấy hội thoại" }, { status: 404 });
  }
  return NextResponse.json({ ok: true });
}
