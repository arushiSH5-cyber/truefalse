const demo = process.env.NEXT_PUBLIC_DEMO === "1";
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
        async headers() {
          return [{ source: "/sw.js", headers: [{ key: "Cache-Control", value: "no-cache" }] }];
        },
      }),
};
export default nextConfig;
