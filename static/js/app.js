/* Schul-Cloud Dashboard - Frontend-Logik (ohne Framework, nur Fetch + DOM). */
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
  overdue:  { border: 'border-l-red-600',    chip: 'bg-red-100 text-red-800',       label: 'Überfällig' },
  critical: { border: 'border-l-red-500',    chip: 'bg-red-100 text-red-700',       label: 'In 24 Stunden' },
  warning:  { border: 'border-l-amber-500',  chip: 'bg-amber-100 text-amber-800',   label: 'In 48 Stunden' },
  soon:     { border: 'border-l-yellow-400', chip: 'bg-yellow-100 text-yellow-800', label: 'Diese Woche' },
  later:    { border: 'border-l-slate-300',  chip: 'bg-slate-100 text-slate-600',   label: 'Später' },
  none:     { border: 'border-l-slate-200',  chip: 'bg-slate-100 text-slate-500',   label: 'Ohne Termin' },
};

const STATUS = {
  open:      { text: 'Offen',      cls: 'bg-slate-100 text-slate-700' },
  submitted: { text: 'Eingereicht', cls: 'bg-blue-100 text-blue-700' },
  graded:    { text: 'Bewertet',    cls: 'bg-emerald-100 text-emerald-700' },
};

function formatDue(iso) {
  if (!iso) return 'Kein Abgabetermin';
  const d = new Date(iso);
  return d.toLocaleString('de-DE', {
    weekday: 'short', day: '2-digit', month: '2-digit', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  }) + ' Uhr';
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

function statTile(label, value, tone) {
  return `<div class="rounded-xl border border-slate-200 bg-white p-3">
            <p class="text-xs font-medium text-slate-500">${label}</p>
            <p class="mt-1 text-2xl font-semibold ${tone || ''}">${value}</p>
          </div>`;
}

function renderStats() {
  const s = state.stats || {};
  el('stats').innerHTML = [
    statTile('Offen', s.open ?? 0),
    statTile('Überfällig', s.overdue ?? 0, (s.overdue ? 'text-red-600' : '')),
    statTile('Nächste 24 h', s.next24h ?? 0, (s.next24h ? 'text-red-600' : '')),
    statTile('Nächste 48 h', s.next48h ?? 0, (s.next48h ? 'text-amber-600' : '')),
    statTile('Tests / Arbeiten', s.exams ?? 0, 'text-indigo-600'),
    statTile('Erledigt', s.done ?? 0, 'text-emerald-600'),
  ].join('');
}

function itemCard(item) {
  const level = LEVELS[item.urgency?.level] || LEVELS.none;
  const status = STATUS[item.status] || STATUS.open;
  const kindBadge = item.kind === 'exam'
    ? '<span class="rounded-full bg-indigo-100 px-2 py-0.5 text-xs font-medium text-indigo-700">Test / Arbeit</span>'
    : '';
  const grade = item.grade
    ? `<span class="rounded-full bg-emerald-100 px-2 py-0.5 text-xs font-medium text-emerald-700">Note: ${escapeHtml(item.grade)}</span>`
    : '';
  const dot = item.color
    ? `<span class="inline-block h-2.5 w-2.5 rounded-full" style="background:${escapeHtml(item.color)}"></span>`
    : '';

  return `
  <li class="card-enter rounded-xl border border-slate-200 border-l-4 ${level.border} bg-white p-4 shadow-sm"
      data-id="${escapeHtml(item.id)}">
    <div class="flex items-start gap-3">
      <button class="check-btn mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-md border-2 border-slate-300 text-transparent transition hover:border-emerald-500 hover:text-emerald-600"
              title="Als erledigt markieren" aria-label="Als erledigt markieren">
        <svg viewBox="0 0 20 20" class="h-4 w-4" fill="currentColor">
          <path d="M7.6 13.2 4.4 10l-1.1 1.1 4.3 4.3 9-9-1.1-1.1z"/>
        </svg>
      </button>

      <div class="min-w-0 flex-1">
        <div class="flex flex-wrap items-center gap-2">
          <h3 class="truncate text-sm font-semibold">${escapeHtml(item.title)}</h3>
          ${kindBadge}
          <span class="rounded-full px-2 py-0.5 text-xs font-medium ${status.cls}">${status.text}</span>
          ${grade}
        </div>
        <p class="mt-1 flex items-center gap-1.5 text-xs text-slate-500">
          ${dot}<span class="font-medium">${escapeHtml(item.course)}</span>
          ${item.teacher ? '· ' + escapeHtml(item.teacher) : ''}
        </p>
        ${item.description ? `<p class="mt-2 line-clamp-2 text-sm text-slate-600">${escapeHtml(item.description)}</p>` : ''}
        <div class="mt-2 flex flex-wrap items-center gap-2 text-xs">
          <span class="rounded-full px-2 py-0.5 font-medium ${level.chip}">${escapeHtml(item.urgency?.label || level.label)}</span>
          <span class="text-slate-500">${formatDue(item.due)}</span>
          <a href="${escapeHtml(item.url)}" target="_blank" rel="noopener"
             class="ml-auto font-medium text-slate-600 underline-offset-2 hover:underline">In der Schul-Cloud öffnen →</a>
        </div>
      </div>
    </div>
  </li>`;
}

function archiveCard(item) {
  return `
  <li class="flex items-center gap-3 rounded-lg border border-slate-200 bg-white px-4 py-2.5" data-id="${escapeHtml(item.id)}">
    <svg viewBox="0 0 20 20" class="h-4 w-4 shrink-0 text-emerald-600" fill="currentColor">
      <path d="M7.6 13.2 4.4 10l-1.1 1.1 4.3 4.3 9-9-1.1-1.1z"/>
    </svg>
    <div class="min-w-0 flex-1">
      <p class="truncate text-sm font-medium text-slate-600 line-through">${escapeHtml(item.title)}</p>
      <p class="text-xs text-slate-400">${escapeHtml(item.course)} · ${formatDue(item.due)}</p>
    </div>
    ${item.status === 'graded' && !item.done
      ? '<span class="rounded-full bg-emerald-100 px-2 py-0.5 text-xs text-emerald-700">bewertet</span>'
      : '<button class="undo-btn rounded-lg border border-slate-300 px-2 py-1 text-xs font-medium hover:bg-slate-100">Zurückholen</button>'}
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
    btn.className = `filter-btn rounded-md px-3 py-1 font-medium ${on ? 'bg-ink text-white' : 'text-slate-600 hover:bg-slate-100'}`;
  });

  el('sync-info').textContent = state.lastSync
    ? 'Stand: ' + new Date(state.lastSync).toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' })
    : '';
  el('sync-info').classList.toggle('hidden', !state.lastSync);
}

function applyPayload(data) {
  state.active = data.active || [];
  state.archive = data.archive || [];
  state.stats = data.stats || {};
  state.lastSync = data.last_sync || state.lastSync;

  const box = el('warnings');
  const warnings = data.warnings || [];
  box.classList.toggle('hidden', warnings.length === 0);
  box.innerHTML = warnings.map((w) => `<p>⚠️ ${escapeHtml(w)}</p>`).join('')
    + (data.mode === 'demo' ? '<p>ℹ️ Demo-Modus: Die angezeigten Daten sind Beispieldaten.</p>' : '');
  if (data.mode === 'demo') box.classList.remove('hidden');

  render();
}

function toast(message) {
  const node = el('toast');
  node.textContent = message;
  node.classList.remove('hidden');
  clearTimeout(node._timer);
  node._timer = setTimeout(() => node.classList.add('hidden'), 2500);
}

/* ------------------------------------------------------------------- Aktionen */

function showDashboard(status) {
  el('login-view').classList.add('hidden');
  el('dash-view').classList.remove('hidden');
  el('btn-refresh').classList.remove('hidden');
  el('btn-logout').classList.remove('hidden');
  el('header-sub').textContent = `${status.user || ''} · ${window.APP_CONFIG.baseUrl}`
    + (status.mode === 'demo' ? ' · Demo' : '');
}

function showLogin() {
  el('dash-view').classList.add('hidden');
  el('login-view').classList.remove('hidden');
  el('btn-refresh').classList.add('hidden');
  el('btn-logout').classList.add('hidden');
  el('header-sub').textContent = window.APP_CONFIG.baseUrl;
}

async function loadItems() {
  const data = await api('/api/items');
  applyPayload(data);
}

async function refresh(silent = false) {
  const btn = el('btn-refresh');
  btn.disabled = true;
  btn.textContent = 'Lädt ...';
  try {
    applyPayload(await api('/api/refresh', { method: 'POST' }));
    if (!silent) toast('Daten aktualisiert');
  } catch (err) {
    if (String(err.message).includes('angemeldet') || String(err.message).includes('abgelaufen')) {
      showLogin();
    }
    toast('Fehler: ' + err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Aktualisieren';
  }
}

async function setDone(itemId, done) {
  try {
    applyPayload(await api(`/api/items/${encodeURIComponent(itemId)}/done`, {
      method: 'POST',
      body: JSON.stringify({ done }),
    }));
    toast(done ? 'Als erledigt abgehakt' : 'Zurück in die To-do-Liste');
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
    toast('Angemeldet als ' + res.user);
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
    if (btn) setDone(btn.closest('li').dataset.id, true);
  });

  el('archive-list').addEventListener('click', (event) => {
    const btn = event.target.closest('.undo-btn');
    if (btn) setDone(btn.closest('li').dataset.id, false);
  });

  // Startzustand ermitteln
  try {
    const status = await api('/api/status');
    if (status.logged_in) {
      showDashboard(status);
      await loadItems();
    } else {
      showLogin();
    }
  } catch (_) {
    showLogin();
  }

  // Regelmaessig neu zeichnen (Countdown) und Daten nachladen.
  setInterval(() => { if (state.active.length) loadItems().catch(() => {}); }, 60000);
  const minutes = window.APP_CONFIG.refreshMinutes;
  if (minutes > 0) {
    setInterval(() => {
      if (!el('dash-view').classList.contains('hidden')) refresh(true);
    }, minutes * 60000);
  }
});
