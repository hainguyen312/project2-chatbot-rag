import { MongoClient } from "mongodb";

export type ChatMessage = {
  role: "user" | "assistant";
  content: string;
};

export type ChatDoc = {
  _id: string;
  user_id: string;
  title: string;
  pinned: boolean;
  messages: ChatMessage[];
  createdAt?: string;
  updatedAt?: string;
  created_at?: string;
  updated_at?: string;
};

const MONGODB_URI = process.env.MONGODB_URI ?? "";
const MONGODB_DB = process.env.MONGODB_DB ?? "chatbot_rag";
const MONGODB_COLLECTION = process.env.MONGODB_COLLECTION ?? "conversations";

let mongoClient: MongoClient | null = null;

const memoryStore = new Map<string, ChatDoc>();

function nowIso() {
  return new Date().toISOString();
}

function hasMongo() {
  return Boolean(MONGODB_URI);
}

async function getCollection() {
  if (!hasMongo()) return null;
  if (!mongoClient) {
    mongoClient = new MongoClient(MONGODB_URI, {
      serverSelectionTimeoutMS: 8000,
    });
    await mongoClient.connect();
  }
  return mongoClient.db(MONGODB_DB).collection<ChatDoc>(MONGODB_COLLECTION);
}

function sortChats(chats: ChatDoc[]) {
  const getUpdatedValue = (chat: ChatDoc) =>
    chat.updated_at ?? chat.updatedAt ?? chat.created_at ?? chat.createdAt ?? "";

  return chats.sort((a, b) => {
    const aPinned = Boolean(a?.pinned);
    const bPinned = Boolean(b?.pinned);
    if (aPinned !== bPinned) return aPinned ? -1 : 1;

    return getUpdatedValue(b).localeCompare(getUpdatedValue(a));
  });
}

export async function listChats(userId: string): Promise<ChatDoc[]> {
  const col = await getCollection();
  if (!col) {
    return sortChats(
      Array.from(memoryStore.values()).filter((c) => c.user_id === userId)
    );
  }
  const docs = await col.find({ user_id: userId }).toArray();
  return sortChats(docs);
}

export async function createChat(
  userId: string,
  title = "Cuộc trò chuyện mới"
): Promise<ChatDoc> {
  const doc: ChatDoc = {
    _id: crypto.randomUUID(),
    user_id: userId,
    title,
    pinned: false,
    messages: [],
    createdAt: nowIso(),
    updatedAt: nowIso(),
  };
  const col = await getCollection();
  if (!col) {
    memoryStore.set(doc._id, doc);
    return doc;
  }
  await col.insertOne(doc);
  return doc;
}

export async function getChatById(
  id: string,
  userId: string
): Promise<ChatDoc | null> {
  const col = await getCollection();
  if (!col) {
    const doc = memoryStore.get(id);
    return doc && doc.user_id === userId ? doc : null;
  }
  return col.findOne({ _id: id, user_id: userId });
}

export async function updateChat(
  id: string,
  userId: string,
  patch: Partial<Pick<ChatDoc, "title" | "pinned" | "messages">>
): Promise<ChatDoc | null> {
  const col = await getCollection();
  const updatedAt = nowIso();
  const safePatch = Object.fromEntries(
    Object.entries(patch).filter(([, value]) => value !== undefined)
  ) as Partial<Pick<ChatDoc, "title" | "pinned" | "messages">>;

  if (!col) {
    const current = memoryStore.get(id);
    if (!current || current.user_id !== userId) return null;
    const next: ChatDoc = { ...current, ...safePatch, updatedAt };
    memoryStore.set(id, next);
    return next;
  }

  const result = await col.updateOne(
    { _id: id, user_id: userId },
    { $set: { ...safePatch, updatedAt } }
  );
  if (result.matchedCount === 0) return null;
  return col.findOne({ _id: id, user_id: userId });
}

export async function deleteChatById(
  id: string,
  userId: string
): Promise<boolean> {
  const col = await getCollection();
  if (!col) {
    const doc = memoryStore.get(id);
    if (!doc || doc.user_id !== userId) return false;
    return memoryStore.delete(id);
  }
  const result = await col.deleteOne({ _id: id, user_id: userId });
  return result.deletedCount > 0;
}
