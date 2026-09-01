const fields = ['dashboardUrl', 'ingestToken', 'schulcloudUrl'];
const status = document.getElementById('status');

chrome.storage.local.get(fields).then((cfg) => {
  fields.forEach((f) => { if (cfg[f]) document.getElementById(f).value = cfg[f]; });
});

document.getElementById('save').addEventListener('click', async () => {
  const values = Object.fromEntries(fields.map((f) => [f, document.getElementById(f).value.trim()]));
  await chrome.storage.local.set(values);
  status.textContent = 'Gespeichert.';
});

document.getElementById('sync').addEventListener('click', () => {
  status.textContent = 'Übertrage …';
  chrome.runtime.sendMessage({ type: 'sync' }, (res) => {
    status.textContent = res?.ok ? `Übertragen (${res.data.count ?? 0} Einträge).` : `Fehler: ${res?.error}`;
  });
});

document.getElementById('scrape').addEventListener('click', async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  chrome.tabs.sendMessage(tab.id, { type: 'scrape' }, (res) => {
    const tasks = res?.tasks || [];
    if (!tasks.length) { status.textContent = 'Keine Aufgaben auf dieser Seite gefunden.'; return; }
    chrome.runtime.sendMessage({ type: 'items', items: tasks }, (out) => {
      status.textContent = out?.ok ? `${tasks.length} Aufgaben übertragen.` : `Fehler: ${out?.error}`;
    });
  });
});
