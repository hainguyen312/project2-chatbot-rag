import type { Metadata } from "next";
import { Be_Vietnam_Pro, Geist_Mono, Source_Serif_4 } from "next/font/google";
import "./globals.css";
import "./theme.css";
import { AuthProvider } from "@/hooks/useAuth";

const beVietnamPro = Be_Vietnam_Pro({
  variable: "--font-be-vietnam",
  subsets: ["latin", "vietnamese"],
  weight: ["300", "400", "500", "600", "700"],
});

// Câu trả lời đọc như văn bản luật: serif có đủ dấu tiếng Việt, trục opsz cho cỡ chữ đọc
const sourceSerif = Source_Serif_4({
  variable: "--font-serif",
  subsets: ["latin", "vietnamese"],
  axes: ["opsz"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "VNLaw Chatbot",
  description: "Chatbot hỏi đáp pháp luật Việt Nam",
  icons: "/logo.png",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="vi"
      className={`${beVietnamPro.variable} ${sourceSerif.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col">
        <AuthProvider>{children}</AuthProvider>
      </body>
    </html>
  );
}
