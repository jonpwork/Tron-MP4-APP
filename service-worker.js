// Tron Conversor — Service Worker v1.0
const CACHE = "tron-v1";

// Arquivos que ficam em cache para abertura rápida
const SHELL = [
  "/",
  "/login",
  "/manifest.json",
  "/static/icon-192.png",
  "/static/icon-512.png"
];

// ── INSTALL: pré-cacheia o shell ──────────────────
self.addEventListener("install", e => {
  e.waitUntil(
    caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

// ── ACTIVATE: limpa caches antigos ───────────────
self.addEventListener("activate", e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

// ── FETCH: network-first para rotas do app ───────
// Cache-first apenas para assets estáticos
self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);

  // Nunca intercepta uploads/conversões (POST)
  if (e.request.method !== "GET") return;

  // Assets estáticos → cache-first
  if (url.pathname.startsWith("/static/")) {
    e.respondWith(
      caches.match(e.request).then(cached => cached || fetch(e.request))
    );
    return;
  }

  // Tudo mais → network-first, fallback para cache
  e.respondWith(
    fetch(e.request)
      .then(res => {
        // Atualiza cache com resposta fresca
        const clone = res.clone();
        caches.open(CACHE).then(c => c.put(e.request, clone));
        return res;
      })
      .catch(() => caches.match(e.request))
  );
});
