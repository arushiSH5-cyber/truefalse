import type { Metadata, Viewport } from "next";
import "./globals.css";
import ServiceWorker from "@/components/ServiceWorker";

const BASE = process.env.NEXT_PUBLIC_BASE_PATH ?? "";

export const metadata: Metadata = {
  title: "TrueFalse",
  description: "Check forwarded messages, photos, voice notes, videos and links, in any language.",
  manifest: `${BASE}/manifest.webmanifest`,
  icons: { icon: `${BASE}/icon-192.png`, apple: `${BASE}/icon-192.png` },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: "#0f172a",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen antialiased">
        <main className="mx-auto max-w-xl px-4 py-6">{children}</main>
        <ServiceWorker />
      </body>
    </html>
  );
}
