/**
 * Firebase client SDK initialization.
 *
 * SETUP:
 * 1. Mở Firebase Console → Project "chatbot-d960f" → Project Settings → General.
 * 2. Trong section "Your apps" → Web app, copy Firebase config snippet.
 * 3. Tạo file frontend/.env.local với các biến sau (lấy giá trị từ console):
 *
 *    NEXT_PUBLIC_FIREBASE_API_KEY=...
 *    NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN=chatbot-d960f.firebaseapp.com
 *    NEXT_PUBLIC_FIREBASE_PROJECT_ID=chatbot-d960f
 *    NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET=chatbot-d960f.firebasestorage.app
 *    NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID=...
 *    NEXT_PUBLIC_FIREBASE_APP_ID=...
 *
 * 4. Trong Firebase Console → Authentication → Sign-in method, enable:
 *    - Email/Password
 *    - Google (cần điền support email)
 *
 * 5. Authorized domains đảm bảo có `localhost` (mặc định đã có khi tạo project).
 */

import { initializeApp, getApps, getApp, FirebaseApp } from "firebase/app";
import { getAuth, Auth } from "firebase/auth";

const config = {
  apiKey: process.env.NEXT_PUBLIC_FIREBASE_API_KEY,
  authDomain: process.env.NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN,
  projectId: process.env.NEXT_PUBLIC_FIREBASE_PROJECT_ID,
  storageBucket: process.env.NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET,
  messagingSenderId: process.env.NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID,
  appId: process.env.NEXT_PUBLIC_FIREBASE_APP_ID,
};

let _app: FirebaseApp | null = null;
let _auth: Auth | null = null;

export function getFirebaseApp(): FirebaseApp {
  if (_app) return _app;
  if (!config.apiKey) {
    throw new Error(
      "Firebase chưa được cấu hình. Tạo file frontend/.env.local với các biến NEXT_PUBLIC_FIREBASE_* — xem comment trong src/lib/firebase.ts."
    );
  }
  _app = getApps().length ? getApp() : initializeApp(config);
  return _app;
}

export function getFirebaseAuth(): Auth {
  if (_auth) return _auth;
  _auth = getAuth(getFirebaseApp());
  return _auth;
}

export function isFirebaseConfigured(): boolean {
  return !!config.apiKey;
}
