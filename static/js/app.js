/* Schul-Cloud Dashboard – Frontend (ohne Framework, nur Fetch + DOM).
   Auf Handybedienung ausgelegt: grosse Tippflaechen, Rueckgaengig nach dem
   Abhaken, Aktualisierung beim Zurueckwechseln zur App. */
'use strict';

const state = {
  active: [],
  archive: [],
  stats: {},
  filter: 'all',
  search: '',
  course: '',
  lastSync: null,
};

const el = (id) => document.getElementById(id);

/* Registriert einen Handler nur, wenn es das Element gibt. Fehlt eines (etwa
   weil der Browser eine aeltere Fassung der Seite zwischengespeichert hat),
   darf das nicht den ganzen Start abbrechen. */
function on(id, event, handler) {
  const node = el(id);
  if (node) node.addEventListener(event, handler);
  else console.warn('Element fehlt:', id);
}

/* Sichtbare Fehlermeldung - besser als eine leere Seite. */
function showFatal(message) {
  const box = el('fatal');
  if (!box) return;
  el('fatal-text').textContent = message;
  box.classList.remove('hidden');
}

window.addEventListener('error', (e) => showFatal(e.message || 'Unbekannter Fehler'));
window.addEventListener('unhandledrejection', (e) =>
  showFatal((e.reason && e.reason.message) || 'Unbekannter Fehler'));

const api = async (path, options = {}) => {
  // Zeitlimit, damit eine haengende Anfrage nicht als "es passiert nichts"
  // endet. Der Server darf beim Aufwachen langsam sein, aber nicht endlos.
  const { timeoutMs = 120000, ...rest } = options;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  let res;
  try {
    res = await fetch(path, {
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      signal: controller.signal,
      ...rest,
    });
  } catch (err) {
    clearTimeout(timer);
    if (err.name === 'AbortError') {
      throw new Error('Der Server hat zu lange nicht geantwortet. Bitte noch einmal versuchen.');
    }
    throw new Error('Keine Verbindung zum Server.');
  }
  clearTimeout(timer);
  let data = {};
  try { data = await res.json(); } catch (_) { /* leere Antwort */ }
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
};

/* ---------------------------------------------------------------- Darstellung */

const LEVELS = {
  overdue:  { bar: 'bg-red-600',    chip: 'bg-red-100 text-red-800' },
  critical: { bar: 'bg-red-500',    chip: 'bg-red-100 text-red-700' },
  warning:  { bar: 'bg-amber-500',  chip: 'bg-amber-100 text-amber-800' },
  soon:     { bar: 'bg-yellow-400', chip: 'bg-yellow-100 text-yellow-800' },
  later:    { bar: 'bg-slate-300',  chip: 'bg-slate-100 text-slate-600' },
  none:     { bar: 'bg-slate-200',  chip: 'bg-slate-100 text-slate-500' },
};

const STATUS = {
  open:      { text: 'Offen',       cls: 'bg-slate-100 text-slate-700' },
  submitted: { text: 'Eingereicht', cls: 'bg-blue-100 text-blue-700' },
  graded:    { text: 'Bewertet',    cls: 'bg-emerald-100 text-emerald-700' },
};

function formatDue(iso, short = false) {
  if (!iso) return 'Kein Abgabetermin';
  const d = new Date(iso);
  return d.toLocaleString('de-DE', short
    ? { weekday: 'short', day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }
    : { weekday: 'short', day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

function statTile(label, value, tone) {
  return `<div class="min-w-[7rem] shrink-0 rounded-xl border border-slate-200 bg-white px-3 py-2 sm:min-w-0">
            <p class="text-[11px] font-medium text-slate-500">${label}</p>
            <p class="text-xl font-semibold ${tone || ''}">${value}</p>
          </div>`;
}

function renderStats() {
  const s = state.stats || {};
  el('stats').innerHTML = [
    statTile('Offen', s.open ?? 0),
    statTile('Abgelaufen', s.overdue ?? 0, s.overdue ? 'text-slate-500' : ''),
    statTile('Nächste 24 h', s.next24h ?? 0, s.next24h ? 'text-red-600' : ''),
    statTile('Nächste 48 h', s.next48h ?? 0, s.next48h ? 'text-amber-600' : ''),
    statTile('Tests', s.exams ?? 0, 'text-indigo-600'),
    statTile('Erledigt', s.done ?? 0, 'text-emerald-600'),
  ].join('');
}

function itemCard(item) {
  const level = LEVELS[item.urgency?.level] || LEVELS.none;
  const status = STATUS[item.status] || STATUS.open;
  const kindBadge = item.kind === 'exam'
    ? '<span class="rounded-full bg-indigo-100 px-2 py-0.5 text-[11px] font-medium text-indigo-700">Test</span>'
    : '';
  // Aus einem Kursthema gelesen - keine offizielle Aufgabe der Schul-Cloud.
  const originBadge = item.origin === 'topic'
    ? '<span class="rounded-full bg-violet-100 px-2 py-0.5 text-[11px] font-medium text-violet-700">Kursthema</span>'
    : '';
  const dot = item.color
    ? `<span class="inline-block h-2 w-2 shrink-0 rounded-full" style="background:${escapeHtml(item.color)}"></span>`
    : '';

  return `
  <li class="card-enter overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm"
      data-id="${escapeHtml(item.id)}">
    <div class="flex">
      <span class="w-1.5 shrink-0 ${level.bar}"></span>

      <!-- Abhaken: grosse Tippflaeche am linken Rand -->
      <button class="check-btn no-select grid w-14 shrink-0 place-items-center active:bg-emerald-50"
              aria-label="Als erledigt markieren">
        <span class="grid h-7 w-7 place-items-center rounded-lg border-2 border-slate-300 text-transparent">
          <svg viewBox="0 0 20 20" class="h-4 w-4" fill="currentColor">
            <path d="M7.6 13.2 4.4 10l-1.1 1.1 4.3 4.3 9-9-1.1-1.1z"/>
          </svg>
        </span>
      </button>

      <a href="${escapeHtml(item.url)}" target="_blank" rel="noopener"
         class="min-w-0 flex-1 py-3 pr-3 active:bg-slate-50">
        <div class="flex flex-wrap items-center gap-1.5">
          <h3 class="text-[15px] font-semibold leading-snug">${escapeHtml(item.title)}</h3>
          ${kindBadge}
          ${originBadge}
          <span class="rounded-full px-2 py-0.5 text-[11px] font-medium ${status.cls}">${status.text}</span>
        </div>
        <p class="mt-1 flex items-center gap-1.5 text-xs text-slate-500">
          ${dot}<span class="truncate font-medium">${escapeHtml(item.course)}</span>
        </p>
        <div class="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
          <span class="rounded-full px-2 py-0.5 font-medium ${level.chip}">${escapeHtml(item.urgency?.label || '')}</span>
          <span class="text-slate-500">${formatDue(item.due, true)}</span>
        </div>
      </a>
    </div>
  </li>`;
}

function archiveCard(item) {
  // Was die Schul-Cloud selbst als abgegeben/bewertet meldet, laesst sich hier
  // nicht zurueckholen - beim naechsten Laden waere es ohnehin wieder erledigt.
  const fromSchulCloud = !item.done && ['submitted', 'graded'].includes(item.status);
  const badge = item.status === 'graded' ? 'bewertet' : 'abgegeben';
  const restore = fromSchulCloud
    ? `<span class="shrink-0 rounded-full bg-emerald-100 px-2 py-1 text-[11px] text-emerald-700">${badge}</span>`
    : item.expired && !item.done
      ? '<span class="shrink-0 rounded-full bg-slate-200 px-2 py-1 text-[11px] text-slate-600">abgelaufen</span>'
      : '<button class="undo-btn shrink-0 rounded-lg border border-slate-300 px-3 py-2 text-xs font-medium active:bg-slate-100">Zurückholen</button>';

  return `
  <li class="flex items-center gap-3 rounded-xl border border-slate-200 bg-white px-3 py-2.5" data-id="${escapeHtml(item.id)}">
    ${item.expired && !item.done
      ? '<span class="h-4 w-4 shrink-0 text-center text-xs text-slate-400">⌛</span>'
      : `<svg viewBox="0 0 20 20" class="h-4 w-4 shrink-0 text-emerald-600" fill="currentColor">
           <path d="M7.6 13.2 4.4 10l-1.1 1.1 4.3 4.3 9-9-1.1-1.1z"/>
         </svg>`}
    <div class="min-w-0 flex-1">
      <p class="truncate text-sm font-medium text-slate-600 ${item.expired && !item.done ? '' : 'line-through'}">${escapeHtml(item.title)}</p>
      <p class="truncate text-xs text-slate-400">${escapeHtml(item.course)} · ${formatDue(item.due, true)}</p>
    </div>
    ${restore}
  </li>`;
}

function visibleItems() {
  const q = state.search.trim().toLowerCase();
  return state.active.filter((item) => {
    if (state.filter === 'homework' && item.kind !== 'homework') return false;
    if (state.filter === 'exam' && item.kind !== 'exam') return false;
    if (state.filter === 'urgent'
        && !['critical', 'warning'].includes(item.urgency?.level)) return false;
    if (state.course && item.course !== state.course) return false;
    if (q) {
      const haystack = `${item.title} ${item.course} ${item.description}`.toLowerCase();
      if (!haystack.includes(q)) return false;
    }
    return true;
  });
}

function renderCourseFilter() {
  const select = el('course-filter');
  const courses = [...new Set(state.active.map((i) => i.course))].sort((a, b) => a.localeCompare(b, 'de'));
  const current = state.course;
  select.innerHTML = '<option value="">Alle Kurse</option>'
    + courses.map((c) => `<option value="${escapeHtml(c)}">${escapeHtml(c)}</option>`).join('');
  select.value = courses.includes(current) ? current : '';
  state.course = select.value;
}

function render() {
  renderStats();
  renderCourseFilter();

  const items = visibleItems();
  el('active-list').innerHTML = items.map(itemCard).join('');
  el('empty-active').classList.toggle('hidden', items.length > 0);

  el('archive-list').innerHTML = state.archive.map(archiveCard).join('');
  el('archive-count').textContent = state.archive.length;

  document.querySelectorAll('.filter-btn').forEach((btn) => {
    const on = btn.dataset.filter === state.filter;
    btn.className = 'filter-btn shrink-0 rounded-full px-4 py-2 text-sm font-medium '
      + (on ? 'bg-ink text-white' : 'border border-slate-300 bg-white text-slate-600');
  });
}

/* Einziger Weg, wie Server-Daten in die Anzeige gelangen. */
function applyServerData(data) {
  applyPayload(mergeLocalState(data));
  syncDoneState(data);   // repariert den Server im Hintergrund
}

function applyPayload(data) {
  state.active = data.active || [];
  state.archive = data.archive || [];
  state.stats = data.stats || {};
  state.lastSync = data.last_sync || state.lastSync;

  const box = el('warnings');
  const warnings = [...(data.warnings || [])];
  if (data.mode === 'demo') warnings.push('Demo-Modus: Die angezeigten Daten sind Beispieldaten.');
  box.classList.toggle('hidden', warnings.length === 0);
  box.innerHTML = warnings.map((w) => `<p>${escapeHtml(w)}</p>`).join('');

  render();
  cacheOffline(data);
}

/* Eigene Kopie der Haken: Beim Hosting auf Gratis-Angeboten ist der Speicher
   des Servers fluechtig - so ueberleben die Haken einen Neustart. */
function localDone() {
  try {
    return new Set(JSON.parse(localStorage.getItem('sc-done') || '[]'));
  } catch (_) {
    return new Set();
  }
}

function rememberDone(itemId, done) {
  try {
    const set = localDone();
    if (done) set.add(itemId); else set.delete(itemId);
    localStorage.setItem('sc-done', JSON.stringify([...set]));
  } catch (_) { /* privater Modus o.ae. */ }
}

/* Die Haken dieses Geraets gelten immer - auch wenn der Server sie vergessen
   hat (beim Gratis-Hosting wird sein Speicher regelmaessig geleert). */
function mergeLocalState(data) {
  const known = localDone();
  if (!known.size) return data;

  const active = [];
  const archive = [...(data.archive || [])];
  for (const item of data.active || []) {
    if (known.has(item.id)) archive.unshift({ ...item, done: true });
    else if (item.urgency?.level === 'overdue') archive.push({ ...item, expired: true });
    else active.push(item);
  }
  return { ...data, active, archive, stats: computeStats(active, archive) };
}

function computeStats(active, archive) {
  const levels = active.map((i) => i.urgency?.level);
  const count = (level) => levels.filter((l) => l === level).length;
  return {
    open: active.length,
    overdue: archive.filter((i) => i.expired && !i.done).length,
    next24h: count('critical'),
    next48h: count('critical') + count('warning'),
    exams: active.filter((i) => i.kind === 'exam').length,
    done: archive.filter((i) => !i.expired || i.done).length,
  };
}

/* Fehlt dem Server ein Haken, den dieses Geraet kennt, wird er nachgereicht.
   Laeuft nebenher; die Anzeige stimmt durch mergeLocalState ohnehin schon. */
async function syncDoneState(data) {
  const known = localDone();
  if (!known.size) return;

  const serverDone = new Set((data.archive || []).filter((i) => i.done).map((i) => i.id));
  const missing = [...known].filter((id) => !serverDone.has(id));
  if (!missing.length) return;

  try {
    await api('/api/items/state/bulk', { method: 'POST', body: JSON.stringify({ done: missing }) });
  } catch (_) { /* beim naechsten Laden erneut versucht */ }
}

/* Letzte Antwort lokal sichern, damit die Liste auch ohne Verbindung erscheint. */
function cacheOffline(data) {
  try {
    localStorage.setItem('sc-items', JSON.stringify({ ...data, cached_at: Date.now() }));
  } catch (_) { /* privater Modus o.ae. */ }
}

function loadOffline() {
  try {
    const raw = localStorage.getItem('sc-items');
    if (!raw) return false;
    const data = JSON.parse(raw);
    state.active = data.active || [];
    state.archive = data.archive || [];
    state.stats = data.stats || {};
    state.lastSync = data.last_sync;
    render();
    return true;
  } catch (_) {
    return false;
  }
}

let toastTimer;
function toast(message, undoAction) {
  el('toast-text').textContent = message;
  const action = el('toast-action');
  action.classList.toggle('hidden', !undoAction);
  action.onclick = undoAction ? () => { action.classList.add('hidden'); undoAction(); } : null;

  el('toast').classList.remove('hidden');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el('toast').classList.add('hidden'), undoAction ? 5000 : 2500);
}

/* ------------------------------------------------------------------- Aktionen */

function showDashboard(status) {
  setBooting(false);
  el('pin-view').classList.add('hidden');
  el('login-view').classList.add('hidden');
  el('dash-view').classList.remove('hidden');
  el('btn-refresh').classList.replace('hidden', 'grid');
  el('btn-logout').classList.replace('hidden', 'grid');
  el('header-sub').textContent = (status.user || '') + (status.mode === 'demo' ? ' · Demo' : '');
}

function showPin() {
  setBooting(false);
  el('dash-view').classList.add('hidden');
  el('login-view').classList.add('hidden');
  el('pin-view').classList.remove('hidden');
  el('btn-refresh').classList.add('hidden');
  el('btn-logout').classList.add('hidden');
  el('pin').focus();
}

function showLogin() {
  setBooting(false);
  el('pin-view').classList.add('hidden');
  el('dash-view').classList.add('hidden');
  el('login-view').classList.remove('hidden');
  el('btn-refresh').classList.add('hidden');
  el('btn-logout').classList.add('hidden');
  el('header-sub').textContent = window.APP_CONFIG.baseUrl;
}

async function loadItems() {
  applyServerData(await api('/api/items'));
}

let refreshing = false;
async function refresh(silent = false) {
  if (refreshing) return;
  refreshing = true;
  el('refresh-icon').classList.add('animate-spin');
  try {
    applyServerData(await api('/api/refresh', { method: 'POST' }));
    if (!silent) toast('Daten aktualisiert');
  } catch (err) {
    const message = String(err.message);
    if (message.includes('angemeldet') || message.includes('abgelaufen')) showLogin();
    if (!silent) toast('Fehler: ' + message);
  } finally {
    refreshing = false;
    el('refresh-icon').classList.remove('animate-spin');
  }
}

async function setDone(itemId, done, quiet = false) {
  rememberDone(itemId, done);
  try {
    applyServerData(await api(`/api/items/${encodeURIComponent(itemId)}/done`, {
      method: 'POST',
      body: JSON.stringify({ done }),
    }));
    if (quiet) return;
    if (done) toast('Abgehakt', () => setDone(itemId, false, true));
    else toast('Zurück in der To-do-Liste');
  } catch (err) {
    toast('Fehler: ' + err.message);
  }
}

async function login(payload) {
  const btn = el('btn-login');
  const error = el('login-error');
  const label = btn.textContent;

  btn.disabled = true;
  btn.textContent = 'Anmeldung läuft …';
  error.classList.add('hidden');

  // Nach 8 Sekunden ohne Antwort erklaeren, warum es dauert.
  const hint = setTimeout(() => {
    error.textContent = 'Der Server wacht gerade auf – das dauert bis zu einer Minute.';
    error.className = 'rounded-xl bg-slate-100 px-3 py-2 text-sm text-slate-600';
    error.classList.remove('hidden');
  }, 8000);

  try {
    const res = await api('/api/login', { method: 'POST', body: JSON.stringify(payload) });
    showDashboard(res);
    await loadItems();
    // Kursthemen kosten viele Abrufe und laufen deshalb erst jetzt, im
    // Hintergrund - die Liste steht dadurch sofort.
    refresh(true).catch(() => {});
  } catch (err) {
    error.textContent = err.message;
    error.className = 'rounded-xl bg-red-50 px-3 py-2 text-sm text-red-700';
    error.classList.remove('hidden');

    // Hat die Schul-Cloud abgelehnt, ist das Session-Token der naechste Weg -
    // also aufklappen statt nur davon zu schreiben.
    if (/abgelehnt|Session-Token|nicht möglich/i.test(err.message)) {
      const alternative = document.querySelector('#login-form details');
      if (alternative) {
        alternative.open = true;
        alternative.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }
    }
  } finally {
    clearTimeout(hint);
    btn.disabled = false;
    btn.textContent = label;
  }
}

/* --------------------------------------------------------------- Event-Bindung */

document.addEventListener('DOMContentLoaded', () => {
  start().catch((err) => {
    showLogin();                       // irgendetwas ist immer sichtbar
    showFatal('Start fehlgeschlagen: ' + (err.message || err));
  });
});

function setBooting(active) {
  const boot = el('booting');
  const login = el('login-view');
  if (boot) boot.classList.toggle('hidden', !active);
  // Waehrend der Pruefung nicht die Anmeldung zeigen - sie koennte unnoetig sein.
  if (login && active) login.classList.add('hidden');
}

async function start() {
  setBooting(true);
  on('login-form', 'submit', (event) => {
    event.preventDefault();
    const password = el('password').value;

    // Beim Einfuegen auf dem Handy rutscht leicht ein Leerzeichen mit hinein.
    // Das sieht man nicht und die Schul-Cloud lehnt dann ab.
    if (password && password !== password.trim()) {
      const error = el('login-error');
      error.textContent = 'Im Passwort steht ein Leerzeichen am Anfang oder Ende. '
        + 'Bitte prüfen – es wird mitgesendet.';
      error.className = 'rounded-xl bg-amber-50 px-3 py-2 text-sm text-amber-800';
      error.classList.remove('hidden');
    }

    login({
      username: el('username').value.trim(),
      password,
      jwt: el('jwt').value,
    });
  });

  on('pin-form', 'submit', async (event) => {
    event.preventDefault();
    const error = el('pin-error');
    error.classList.add('hidden');
    try {
      await api('/api/pin', { method: 'POST', body: JSON.stringify({ pin: el('pin').value }) });
      el('pin').value = '';
      const status = await api('/api/status');
      if (status.logged_in) {
        showDashboard(status);
        await loadItems();
      } else {
        showLogin();
      }
    } catch (err) {
      error.textContent = err.message;
      error.classList.remove('hidden');
    }
  });

  on('btn-demo', 'click', () => login({ demo: true }));
  on('btn-refresh', 'click', () => refresh());

  on('btn-logout', 'click', async () => {
    await api('/api/logout', { method: 'POST' }).catch(() => {});
    showLogin();
  });

  on('search', 'input', (e) => { state.search = e.target.value; render(); });
  on('course-filter', 'change', (e) => { state.course = e.target.value; render(); });

  document.querySelectorAll('.filter-btn').forEach((btn) => {
    btn.addEventListener('click', () => { state.filter = btn.dataset.filter; render(); });
  });

  on('toggle-archive', 'click', () => {
    const list = el('archive-list');
    if (list) list.classList.toggle('hidden');
  });

  on('active-list', 'click', (event) => {
    const btn = event.target.closest('.check-btn');
    if (!btn) return;
    event.preventDefault();
    setDone(btn.closest('li').dataset.id, true);
  });

  on('archive-list', 'click', (event) => {
    const btn = event.target.closest('.undo-btn');
    if (btn) setDone(btn.closest('li').dataset.id, false);
  });

  // Startzustand
  try {
    const status = await api('/api/status');
    if (status.pin_required) {
      showPin();
    } else if (status.logged_in) {
      showDashboard(status);
      await loadItems();
    } else {
      showLogin();
    }
  } catch (err) {
    // Offline: letzte bekannte Liste zeigen, statt eine leere Seite
    if (loadOffline()) {
      showDashboard({ user: 'Offline – letzter Stand' });
      toast('Keine Verbindung – letzter gespeicherter Stand');
    } else {
      showLogin();
      showFatal('Keine Verbindung zum Server: ' + err.message);
    }
  }

  // Beim Zurueckwechseln zur App aktualisieren (typisch auf dem Handy)
  document.addEventListener('visibilitychange', () => {
    const dash = el('dash-view');
    if (document.visibilityState === 'visible' && dash && !dash.classList.contains('hidden')) {
      refresh(true);
    }
  });

  const minutes = window.APP_CONFIG.refreshMinutes;
  if (minutes > 0) {
    setInterval(() => {
      const view = el('dash-view');
      if (view && !view.classList.contains('hidden') && document.visibilityState === 'visible') {
        refresh(true);
      }
    }, minutes * 60000);
  }

  // Service Worker nur in sicheren Kontexten (HTTPS oder localhost)
  if ('serviceWorker' in navigator && window.isSecureContext) {
    navigator.serviceWorker.register('/sw.js').catch(() => {});
  }
}
