// Installable app + offline shell. API answers, uploads and shares are never cached.
// Works at the site root (local app) and under a path (the GitHub Pages demo).
const CACHE = "truefalse-shell-v4";
const BASE = new URL(self.registration.scope).pathname; // "/" or "/truefalse/"
const PAGES = ["", "history", "result/saved"]; // "" (home) must cache; the others may not exist
const DOODLES = [
  "scout-happy", "scout-think", "scout-sad", "scout-sleep", "mochi-happy", "pudding-think",
  "pudding-sleep", "berry-wow", "luna-sleep", "camera", "photo", "mic", "magnifier", "book",
  "moon", "sun", "bin", "check", "cross", "wavy", "question",
].map((name) => `doodles/${name}.svg`);
const FILES = ["offline.html", "manifest.webmanifest", "icon-192.png", ...DOODLES].map((f) => BASE + f);

self.addEventListener("install", (event) => {
  event.waitUntil(precache().then(() => self.skipWaiting()));
});

async function precache() {
  const cache = await caches.open(CACHE);
  await cache.addAll(FILES);
  const assets = new Set();
  for (const page of PAGES) {
    const res = await fetch(BASE + page, { cache: "reload" }).catch(() => null);
    if (!res?.ok) {
      if (page === "") throw new Error("can't cache the home page");
      continue;
    }
    // Saved under the final URL (after any trailing-slash redirect) and as a fresh response:
    // browsers refuse to show a cached redirected response for a navigation.
    const html = await res.text();
    await cache.put(new URL(res.url).pathname, new Response(html, { headers: res.headers }));
    // The scripts, styles and fonts those pages need, so they also open offline.
    for (const [url] of html.matchAll(/\/[\w/-]*_next\/static\/[^"'\\\s)]+/g)) assets.add(url);
  }
  await Promise.all([...assets].map((url) => cache.add(url).catch(() => undefined)));
}

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k.startsWith("truefalse-") && k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  const url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== self.location.origin || !url.pathname.startsWith(BASE)) return;
  const path = url.pathname.slice(BASE.length);
  if (path.startsWith("backend") || path.startsWith("share")) return;

  if (req.mode === "navigate") {
    event.respondWith(page(req, url.pathname));
  } else if (/^(_next\/static|doodles)\//.test(path) || FILES.includes(url.pathname)) {
    event.respondWith(asset(req, path.startsWith("_next/static/")));
  }
});

const isShell = (path) => PAGES.some((p) => [BASE + p, `${BASE + p}/`].includes(path));

async function page(req, path) {
  const cache = await caches.open(CACHE);
  try {
    const res = await fetch(req);
    if (res.ok && isShell(path)) await cache.put(path, res.clone());
    return res;
  } catch {
    return (await cache.match(path)) ?? (await cache.match(`${path}/`)) ?? (await cache.match(BASE + "offline.html"));
  }
}

async function asset(req, immutable) {
  const cache = await caches.open(CACHE);
  const hit = await cache.match(req);
  if (hit && immutable) return hit;
  try {
    const res = await fetch(req);
    if (res.ok) await cache.put(req, res.clone());
    return res;
  } catch {
    return hit ?? Response.error();
  }
}
