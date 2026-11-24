import type { Metadata, Viewport } from "next";
import "./globals.css";
import { Tabs, TopBar } from "@/components/Chrome";
import ServiceWorker from "@/components/ServiceWorker";
import Sky from "@/components/Sky";
import { body, display } from "./fonts";

const BASE = process.env.NEXT_PUBLIC_BASE_PATH ?? "";

export const metadata: Metadata = {
  title: "TrueFalse",
  applicationName: "TrueFalse",
  description: "Check forwarded messages, photos, voice notes, videos and links, in any language.",
  manifest: `${BASE}/manifest.webmanifest`,
  icons: {
    icon: [
      { url: `${BASE}/icon.svg`, type: "image/svg+xml" },
      { url: `${BASE}/favicon.png`, type: "image/png", sizes: "64x64" },
    ],
    apple: `${BASE}/apple-touch-icon.png`,
  },
  appleWebApp: { capable: true, title: "TrueFalse", statusBarStyle: "default" },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  themeColor: "#fffaf4",
};

// Cream by default; night only if this person picked it. Runs before first paint, so no flash.
const THEME = `(function(){var t;try{t=localStorage.getItem("truefalse.theme")}catch(e){}
document.documentElement.dataset.theme=t==="night"?t:"day"})()`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${display.variable} ${body.variable}`} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME }} />
      </head>
      <body className="antialiased">
        <Sky />
        <div className="shell">
          <TopBar />
          <main>{children}</main>
        </div>
        <Tabs />
        <ServiceWorker />
      </body>
    </html>
  );
}
