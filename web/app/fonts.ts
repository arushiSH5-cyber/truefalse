import localFont from "next/font/local";

// Self-hosted (SIL OFL, licenses in ./fonts). Other scripts fall back to the phone's own fonts.
export const display = localFont({
  src: [
    { path: "./fonts/fredoka-latin-500-normal.woff2", weight: "500" },
    { path: "./fonts/fredoka-latin-700-normal.woff2", weight: "700" },
  ],
  variable: "--font-display",
  display: "swap",
});

export const body = localFont({
  src: [
    { path: "./fonts/nunito-latin-500-normal.woff2", weight: "500" },
    { path: "./fonts/nunito-latin-700-normal.woff2", weight: "700" },
    { path: "./fonts/nunito-latin-800-normal.woff2", weight: "800" },
  ],
  variable: "--font-body",
  display: "swap",
});
