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

const api = async (path, options = {}) => {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    credentials: 'same-origin',
    ...options,
  });
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
    statTile('Überfällig', s.overdue ?? 0, s.overdue ? 'text-red-600' : ''),
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
  const restore = item.status === 'graded' && !item.done
    ? '<span class="shrink-0 rounded-full bg-emerald-100 px-2 py-1 text-[11px] text-emerald-700">bewertet</span>'
    : '<button class="undo-btn shrink-0 rounded-lg border border-slate-300 px-3 py-2 text-xs font-medium active:bg-slate-100">Zurückholen</button>';

  return `
  <li class="flex items-center gap-3 rounded-xl border border-slate-200 bg-white px-3 py-2.5" data-id="${escapeHtml(item.id)}">
    <svg viewBox="0 0 20 20" class="h-4 w-4 shrink-0 text-emerald-600" fill="currentColor">
      <path d="M7.6 13.2 4.4 10l-1.1 1.1 4.3 4.3 9-9-1.1-1.1z"/>
    </svg>
    <div class="min-w-0 flex-1">
      <p class="truncate text-sm font-medium text-slate-600 line-through">${escapeHtml(item.title)}</p>
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
        && !['overdue', 'critical', 'warning'].includes(item.urgency?.level)) return false;
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

/* Fehlt dem Server ein Haken, den dieses Geraet kennt, wird er nachgereicht. */
async function syncDoneState(data) {
  const known = localDone();
  if (!known.size) return data;

  const serverDone = new Set((data.archive || []).filter((i) => i.done).map((i) => i.id));
  const missing = [...known].filter((id) => !serverDone.has(id));
  if (!missing.length) return data;

  try {
    return await api('/api/items/state/bulk', {
      method: 'POST',
      body: JSON.stringify({ done: missing }),
    });
  } catch (_) {
    return data;
  }
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
  el('pin-view').classList.add('hidden');
  el('login-view').classList.add('hidden');
  el('dash-view').classList.remove('hidden');
  el('btn-refresh').classList.replace('hidden', 'grid');
  el('btn-logout').classList.replace('hidden', 'grid');
  el('header-sub').textContent = (status.user || '') + (status.mode === 'demo' ? ' · Demo' : '');
}

function showPin() {
  el('dash-view').classList.add('hidden');
  el('login-view').classList.add('hidden');
  el('pin-view').classList.remove('hidden');
  el('btn-refresh').classList.add('hidden');
  el('btn-logout').classList.add('hidden');
  el('pin').focus();
}

function showLogin() {
  el('pin-view').classList.add('hidden');
  el('dash-view').classList.add('hidden');
  el('login-view').classList.remove('hidden');
  el('btn-refresh').classList.add('hidden');
  el('btn-logout').classList.add('hidden');
  el('header-sub').textContent = window.APP_CONFIG.baseUrl;
}

async function loadItems() {
  const data = await api('/api/items');
  applyPayload(await syncDoneState(data));
}

let refreshing = false;
async function refresh(silent = false) {
  if (refreshing) return;
  refreshing = true;
  el('refresh-icon').classList.add('animate-spin');
  try {
    applyPayload(await api('/api/refresh', { method: 'POST' }));
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
    applyPayload(await api(`/api/items/${encodeURIComponent(itemId)}/done`, {
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
  btn.disabled = true;
  error.classList.add('hidden');
  try {
    const res = await api('/api/login', { method: 'POST', body: JSON.stringify(payload) });
    showDashboard(res);
    await loadItems();
  } catch (err) {
    error.textContent = err.message;
    error.classList.remove('hidden');
  } finally {
    btn.disabled = false;
  }
}

/* --------------------------------------------------------------- Event-Bindung */

document.addEventListener('DOMContentLoaded', async () => {
  el('login-form').addEventListener('submit', (event) => {
    event.preventDefault();
    login({
      username: el('username').value,
      password: el('password').value,
      jwt: el('jwt').value,
    });
  });

  el('pin-form').addEventListener('submit', async (event) => {
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

  el('btn-demo').addEventListener('click', () => login({ demo: true }));
  el('btn-refresh').addEventListener('click', () => refresh());

  el('btn-logout').addEventListener('click', async () => {
    await api('/api/logout', { method: 'POST' }).catch(() => {});
    showLogin();
  });

  el('search').addEventListener('input', (e) => { state.search = e.target.value; render(); });
  el('course-filter').addEventListener('change', (e) => { state.course = e.target.value; render(); });

  document.querySelectorAll('.filter-btn').forEach((btn) => {
    btn.addEventListener('click', () => { state.filter = btn.dataset.filter; render(); });
  });

  el('toggle-archive').addEventListener('click', () => {
    el('archive-list').classList.toggle('hidden');
  });

  el('active-list').addEventListener('click', (event) => {
    const btn = event.target.closest('.check-btn');
    if (!btn) return;
    event.preventDefault();
    setDone(btn.closest('li').dataset.id, true);
  });

  el('archive-list').addEventListener('click', (event) => {
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
  } catch (_) {
    // Offline: letzte bekannte Liste zeigen, statt eine leere Seite
    if (loadOffline()) {
      showDashboard({ user: 'Offline – letzter Stand' });
      toast('Keine Verbindung – letzter gespeicherter Stand');
    } else {
      showLogin();
    }
  }

  // Beim Zurueckwechseln zur App aktualisieren (typisch auf dem Handy)
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && !el('dash-view').classList.contains('hidden')) {
      refresh(true);
    }
  });

  const minutes = window.APP_CONFIG.refreshMinutes;
  if (minutes > 0) {
    setInterval(() => {
      if (!el('dash-view').classList.contains('hidden') && document.visibilityState === 'visible') {
        refresh(true);
      }
    }, minutes * 60000);
  }

  // Service Worker nur in sicheren Kontexten (HTTPS oder localhost)
  if ('serviceWorker' in navigator && window.isSecureContext) {
    navigator.serviceWorker.register('/sw.js').catch(() => {});
  }
});
