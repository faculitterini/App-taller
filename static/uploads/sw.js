self.addEventListener('install', event => {
  console.log('Service worker instalado');
});

self.addEventListener('fetch', event => {
  // por ahora no cacheamos nada, solo dejamos pasar
});
