// A static build: the demo (saved answers) or the real app calling a hosted checker
// (NEXT_PUBLIC_STATIC=1 with NEXT_PUBLIC_API_URL). Either way there is no Next.js server.
const demo = process.env.NEXT_PUBLIC_DEMO === "1" || process.env.NEXT_PUBLIC_STATIC === "1";
const basePath = process.env.NEXT_PUBLIC_BASE_PATH || undefined;

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  // Demo build: a static site with saved example answers (GitHub Pages), no backend.
  ...(demo ? { output: "export", basePath, trailingSlash: true, images: { unoptimized: true } } : {}),
  ...(demo
    ? {}
    : {
        async rewrites() {
          const api = process.env.API_URL ?? "http://127.0.0.1:8000";
          return [
            { source: "/backend/api/:path*", destination: `${api}/api/:path*` },
            // WhatsApp, Instagram and Messenger call these through the public link.
            { source: "/webhooks/:path*", destination: `${api}/webhooks/:path*` },
          ];
        },
        async headers() {
          return [{ source: "/sw.js", headers: [{ key: "Cache-Control", value: "no-cache" }] }];
        },
      }),
};
export default nextConfig;
