/* Holt das Session-Cookie (jwt) und schickt es an das lokale Dashboard. */

const DEFAULTS = {
  dashboardUrl: 'http://127.0.0.1:5000',
  ingestToken: '',
  schulcloudUrl: 'https://brandenburg.cloud',
};

async function settings() {
  return { ...DEFAULTS, ...(await chrome.storage.local.get(Object.keys(DEFAULTS))) };
}

async function readJwt(schulcloudUrl) {
  const cookie = await chrome.cookies.get({ url: schulcloudUrl, name: 'jwt' });
  return cookie ? cookie.value : null;
}

async function push(payload) {
  const cfg = await settings();
  const res = await fetch(`${cfg.dashboardUrl}/api/ingest`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Ingest-Token': cfg.ingestToken },
    body: JSON.stringify(payload),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

async function syncNow() {
  const cfg = await settings();
  const jwt = await readJwt(cfg.schulcloudUrl);
  if (!jwt) throw new Error('Kein jwt-Cookie gefunden – bitte zuerst in der Schul-Cloud anmelden.');
  return push({ jwt });
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  const handler = message.type === 'sync' ? syncNow() : push({ items: message.items || [] });
  handler.then((data) => sendResponse({ ok: true, data }))
         .catch((err) => sendResponse({ ok: false, error: err.message }));
  return true; // asynchrone Antwort
});
