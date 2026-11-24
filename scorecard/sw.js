// Offline support for brooksholt.com/scorecard.
// Bump VERSION whenever index.html changes so phones pick up the new copy.
var VERSION = 'scorecard-v5';
var CORE = ['./', './index.html', './manifest.webmanifest', './icon-192.png', './icon-512.png', './apple-touch-icon.png', '../logo.png', '../data/courses.json'];

self.addEventListener('install', function (e) {
  e.waitUntil(caches.open(VERSION).then(function (c) { return c.addAll(CORE); }).then(function () { return self.skipWaiting(); }));
});
self.addEventListener('activate', function (e) {
  e.waitUntil(caches.keys().then(function (keys) {
    return Promise.all(keys.filter(function (k) { return k.indexOf('scorecard-') === 0 && k !== VERSION; }).map(function (k) { return caches.delete(k); }));
  }).then(function () { return self.clients.claim(); }));
});

function fromNetwork(req, ms) {
  return new Promise(function (resolve, reject) {
    var t = setTimeout(reject, ms);
    fetch(req).then(function (r) {
      clearTimeout(t);
      if (r && r.ok) { var copy = r.clone(); caches.open(VERSION).then(function (c) { c.put(req, copy); }); }
      resolve(r);
    }, function (err) { clearTimeout(t); reject(err); });
  });
}

self.addEventListener('fetch', function (e) {
  var req = e.request;
  if (req.method !== 'GET') return;
  var url = new URL(req.url);
  var same = url.origin === self.location.origin;
  var font = /fonts\.(googleapis|gstatic)\.com$/.test(url.hostname);
  if (!same && !font) return; // stats, OpenStreetMap, YouTube: straight to the network

  // The page and the course list: try the network briefly (fresh data), fall back to the saved copy.
  if (req.mode === 'navigate' || url.pathname.endsWith('/data/courses.json')) {
    e.respondWith(fromNetwork(req, 3500).catch(function () {
      return caches.match(req, { ignoreSearch: true }).then(function (m) { return m || caches.match('./index.html'); });
    }));
    return;
  }
  // Everything else (icons, logo, fonts): saved copy first, refresh in the background.
  e.respondWith(caches.match(req).then(function (m) {
    var net = fetch(req).then(function (r) {
      if (r && (r.ok || r.type === 'opaque')) { var copy = r.clone(); caches.open(VERSION).then(function (c) { c.put(req, copy); }); }
      return r;
    }).catch(function () { return m; });
    return m || net;
  }));
});
