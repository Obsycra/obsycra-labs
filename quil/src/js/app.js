/* js/app.js */

// ── LOCK SCREEN ─────────────────────────────────────────────────────────────
// First run:  no hash in localStorage → "Set your passphrase" mode.
//             The typed passphrase is hashed with SHA-256 and stored locally.
// Subsequent: hash the input and compare to stored hash. Wrong → error state.
//
// Security note: SHA-256 via Web Crypto API. The hash never leaves the device.
// This is local-only protection (not server-side auth). It protects against
// casual access — not forensic disk analysis. Encrypted storage is on roadmap.

const _PASS_KEY = 'quil_pass_hash';

async function _sha256hex(str) {
  const buf = await crypto.subtle.digest(
    'SHA-256',
    new TextEncoder().encode(str)
  );
  return Array.from(new Uint8Array(buf))
    .map(b => b.toString(16).padStart(2, '0'))
    .join('');
}

function _setLockHint(msg, isError = false) {
  const el = document.getElementById('lockHint');
  if (!el) return;
  el.textContent = msg;
  el.className = isError ? 'lock-hint lock-error' : 'lock-hint';
}

async function unlock() {
  const input = document.getElementById('passInput');
  const val = input ? input.value : '';
  if (!val.length) {
    _setLockHint('Enter a passphrase to continue.', true);
    return;
  }

  const stored = localStorage.getItem(_PASS_KEY);

  if (!stored) {
    // First run — set mode: hash and store the passphrase, then unlock.
    if (val.length < 4) {
      _setLockHint('Passphrase must be at least 4 characters.', true);
      return;
    }
    const hash = await _sha256hex(val);
    localStorage.setItem(_PASS_KEY, hash);
    _setLockHint('');
    _doUnlock();
  } else {
    // Returning user — verify mode.
    const hash = await _sha256hex(val);
    if (hash === stored) {
      _setLockHint('');
      _doUnlock();
    } else {
      _setLockHint('Incorrect passphrase. Try again.', true);
      if (input) { input.value = ''; input.focus(); }
    }
  }
}

function _doUnlock() {
  document.getElementById('lock-screen').classList.remove('active');
  document.getElementById('app').classList.add('active');
  initApp();
}

// Update the hint text based on whether a passphrase has been set.
function _initLockScreen() {
  const stored = localStorage.getItem(_PASS_KEY);
  const btn = document.getElementById('unlockBtn');
  if (!stored) {
    _setLockHint('First time? Type any passphrase to set it.');
    if (btn) btn.querySelector('span').textContent = 'Set passphrase';
  } else {
    _setLockHint('');
    if (btn) btn.querySelector('span').textContent = 'Unlock';
  }
}

document.addEventListener('DOMContentLoaded', _initLockScreen);

document.getElementById('passInput')?.addEventListener('keydown', e => {
  if (e.key === 'Enter') unlock();
});

// ── APP INIT ───────────────────────────────────────────────────────────────

function initApp() {
  initChat();
  initNav();
  renderBrain('associative');
  setTimeout(renderPrompt, 400);
  loadWorkshopJobs();
}

// ── NAVIGATION ─────────────────────────────────────────────────────────────

function initNav() {
  document.querySelectorAll('.nav-item').forEach(btn => {
    btn.addEventListener('click', () => {
      const panel = btn.dataset.panel;
      switchPanel(panel, btn);
    });
  });

  document.querySelectorAll('.btab').forEach(tab => {
    tab.addEventListener('click', () => {
      document.querySelectorAll('.btab').forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      renderBrain(tab.dataset.mem);
    });
  });
}

function switchPanel(name, triggerBtn) {
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  const target = document.getElementById(`panel-${name}`);
  if (target) target.classList.add('active');

  document.querySelectorAll('.nav-item').forEach(b => b.classList.remove('active'));
  if (triggerBtn) {
    triggerBtn.classList.add('active');
  } else {
    const btn = document.querySelector(`.nav-item[data-panel="${name}"]`);
    if (btn) btn.classList.add('active');
  }

  if (name === 'brain') {
    const activeTab = document.querySelector('.btab.active');
    renderBrain(activeTab ? activeTab.dataset.mem : 'associative');
  }
  if (name === 'prompt') {
    renderPrompt();
  }
  if (name === 'workshop') {
    loadWorkshopJobs();
  }
}

// ── WORKSHOP ────────────────────────────────────────────────────────────────

async function startWorkshop() {
  const input = document.getElementById('workshopInput');
  const topic = input ? input.value.trim() : '';
  if (!topic) return;

  const btn = document.querySelector('.workshop-run-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Queuing…'; }

  try {
    const res = await api.startWorkshop(topic);
    input.value = '';
    appendWorkshopJob({
      job_id: res.job_id, topic, status: 'queued',
      steps: [], current_step: null, result: null, minimized: false,
    });
    updateWorkshopStatus();
  } catch (e) {
    alert('Could not start workshop task. Is the backend running?');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Run in background ▶'; }
  }
}

async function cancelWorkshopJob(jobId) {
  try {
    await fetch(`${API}/workshop/cancel/${jobId}`, { method: 'POST' });
  } catch { }
}

function toggleMinimize(jobId) {
  const body = document.getElementById(`wjob-body-${jobId}`);
  const btn = document.getElementById(`wjob-min-${jobId}`);
  if (!body) return;
  const isHidden = body.style.display === 'none';
  body.style.display = isHidden ? '' : 'none';
  if (btn) {
    const path = btn.querySelector('path');
    if (path) path.setAttribute('d', isHidden ? 'M19 9l-7 7-7-7' : 'M5 15l7-7 7 7');
  }
}

function updateWorkshopStatus() {
  const statusEl = document.getElementById('workshopStatus');
  if (!statusEl) return;
  const hasRunning = Object.values(_workshopJobsLocal || {}).some(j => j.status === 'running');
  const hasQueued = Object.values(_workshopJobsLocal || {}).some(j => j.status === 'queued');
  if (hasRunning) {
    statusEl.innerHTML = '<div class="live-dot"></div> Working…';
  } else if (hasQueued) {
    statusEl.innerHTML = '<div class="live-dot" style="background:var(--gold)"></div> Queued';
  } else {
    statusEl.innerHTML = '<div class="live-dot"></div> Idle';
  }
}

// Local cache of jobs for the UI
const _workshopJobsLocal = {};

async function loadWorkshopJobs() {
  try {
    const jobs = await api.getWorkshopJobs();
    const container = document.getElementById('workshopJobs');
    if (!container) return;
    container.innerHTML = '';
    jobs.forEach(j => {
      _workshopJobsLocal[j.job_id] = j;
      appendWorkshopJob(j, false);
    });
    updateWorkshopStatus();
  } catch { }
}

function appendWorkshopJob(job, prepend = true) {
  _workshopJobsLocal[job.job_id] = job;
  const container = document.getElementById('workshopJobs');
  if (!container) return;

  const existing = document.getElementById(`wjob-${job.job_id}`);
  if (existing) {
    existing.replaceWith(buildJobCard(job));
    return;
  }

  const card = buildJobCard(job);
  if (prepend) container.prepend(card);
  else container.appendChild(card);
}

function buildJobCard(job) {
  const card = document.createElement('div');
  card.className = 'workshop-card';
  card.id = `wjob-${job.job_id}`;

  const statusMap = {
    'queued': { cls: 'ws-queued', dot: '◌', label: 'Queued' },
    'running': { cls: 'ws-running', dot: '◉', label: 'Working' },
    'done': { cls: 'ws-done', dot: '●', label: 'Done' },
    'error': { cls: 'ws-error', dot: '✕', label: 'Error' },
    'cancelled': { cls: 'ws-cancelled', dot: '◌', label: 'Cancelled' },
  };
  const s = statusMap[job.status] || { cls: '', dot: '○', label: job.status };

  const cancelBtn = (job.status === 'running' || job.status === 'queued')
    ? `<button class="ws-cancel-btn" onclick="cancelWorkshopJob('${job.job_id}')">Cancel</button>`
    : '';

  const toggleBtn = `<button class="ws-min-btn" id="wjob-min-${job.job_id}" onclick="toggleMinimize('${job.job_id}')" title="Toggle">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" width="10" height="10"><path d="M19 9l-7 7-7-7" stroke-linecap="round" stroke-linejoin="round"/></svg>
  </button>`;

  // Steps
  let stepsHtml = '';
  if (job.steps && job.steps.length) {
    stepsHtml = `<div class="ws-steps">${job.steps.map((s, i) => {
      const isDone = s.status === 'done';
      const isRunning = s.status === 'running';
      const cls = isDone ? 'ws-step-done' : isRunning ? 'ws-step-active' : 'ws-step-pending';
      const icon = isDone
        ? `<svg class="ws-step-check" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M5 13l4 4L19 7" stroke-linecap="round" stroke-linejoin="round"/></svg>`
        : isRunning
          ? `<span class="ws-step-spinner"></span>`
          : `<span class="ws-step-num">${i + 1}</span>`;
      return `<div class="ws-step ${cls}" id="wstep-${job.job_id}-${i}">
        <div class="ws-step-icon-wrap">${icon}</div>
        <div class="ws-step-content">
          <span class="ws-step-label">${s.label}</span>
          ${s.output ? `<div class="ws-step-output">${s.output.slice(0, 160)}${s.output.length > 160 ? '…' : ''}</div>` : ''}
        </div>
      </div>`;
    }).join('')}</div>`;
  }

  const resultHtml = job.result
    ? `<div class="ws-result">${typeof renderMarkdown === 'function' ? renderMarkdown(job.result) : job.result.replace(/\n/g, '<br>')}</div>`
    : job.status === 'running'
      ? `<div class="ws-waiting"><span class="ws-waiting-dot"></span>Quil is working…</div>`
      : job.status === 'queued'
        ? `<div class="ws-waiting"><span class="ws-waiting-dot"></span>Waiting in queue…</div>`
        : '';

  card.innerHTML = `
    <div class="ws-header">
      <div class="ws-header-left">
        <span class="ws-status-dot ${s.cls}-dot"></span>
        <strong class="ws-topic">${job.topic || 'Task'}</strong>
      </div>
      <div class="ws-header-right">
        <span class="ws-badge ${s.cls}">${s.label}</span>
        ${cancelBtn}
        ${toggleBtn}
      </div>
    </div>
    <div id="wjob-body-${job.job_id}" class="ws-body">
      ${stepsHtml}
      ${resultHtml}
    </div>`;
  return card;
}

/**
 * Surgically update a single step row — avoids full card rebuilds on every
 * workshop_step_start / workshop_step_done WebSocket event.
 */
function _updateStepStatus(jobId, stepIndex, status, output) {
  const stepEl = document.getElementById(`wstep-${jobId}-${stepIndex}`);
  if (!stepEl) {
    // Step row doesn't exist yet — fall back to a full card rebuild.
    const job = _workshopJobsLocal[jobId];
    if (job) appendWorkshopJob(job);
    return;
  }

  const cls = status === 'done' ? 'ws-step-done' : status === 'running' ? 'ws-step-active' : 'ws-step-pending';
  stepEl.className = `ws-step ${cls}`;

  // Update the icon inside .ws-step-icon-wrap (SVG checkmark, spinner, or number).
  const iconWrap = stepEl.querySelector('.ws-step-icon-wrap');
  if (iconWrap) {
    if (status === 'done') {
      iconWrap.innerHTML = `<svg class="ws-step-check" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M5 13l4 4L19 7" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
    } else if (status === 'running') {
      iconWrap.innerHTML = `<span class="ws-step-spinner"></span>`;
    }
  }

  if (output) {
    let outEl = stepEl.querySelector('.ws-step-output');
    if (!outEl) {
      outEl = document.createElement('div');
      outEl.className = 'ws-step-output';
      const contentEl = stepEl.querySelector('.ws-step-content');
      if (contentEl) contentEl.appendChild(outEl);
      else stepEl.appendChild(outEl);
    }
    const truncated = output.slice(0, 200) + (output.length > 200 ? '…' : '');
    outEl.textContent = truncated;
  }
}

function handleWorkshopDone(payload) {
  if (_workshopJobsLocal[payload.job_id]) {
    _workshopJobsLocal[payload.job_id].status = 'done';
    _workshopJobsLocal[payload.job_id].result = payload.result;
  }
  appendWorkshopJob({
    job_id: payload.job_id, topic: payload.topic, status: 'done',
    steps: _workshopJobsLocal[payload.job_id]?.steps || [],
    result: payload.result
  });
  updateWorkshopStatus();
  if (typeof addMsg === 'function') {
    addMsg('quil', `✦ Workshop complete: <strong>${payload.topic}</strong> — switch to Workshop to see the result.`);
  }
}

function handleWorkshopError(payload) {
  if (_workshopJobsLocal[payload.job_id]) {
    _workshopJobsLocal[payload.job_id].status = 'error';
  }
  appendWorkshopJob({
    job_id: payload.job_id, topic: payload.topic || 'Task',
    status: 'error', steps: [], result: payload.error
  });
  updateWorkshopStatus();
}

// ── FILE UPLOAD ─────────────────────────────────────────────────────────────

async function handleFileUpload(input) {
  const file = input.files[0];
  if (!file) return;

  const sizeMB = (file.size / 1024 / 1024).toFixed(1);
  const isLarge = file.size > 100_000; // >100KB → use streaming ingest

  // Show initial bubble
  const bubble = addMsg('quil', `📄 Reading <strong>${file.name}</strong> (${sizeMB} MB)…`);

  const reader = new FileReader();
  reader.onload = async (e) => {
    const text = e.target.result;
    input.value = '';

    if (!isLarge) {
      // Small file — simple ingest
      try {
        const res = await api.ingest(file.name, text);
        bubble.innerHTML = `✅ Learned from <strong>${file.name}</strong> — ${res.chunks_created} memory sections saved. Ask me anything from it.`;
      } catch (err) {
        bubble.innerHTML = `❌ Couldn't process file: ${err.message}`;
      }
      return;
    }

    // Large file — streaming ingest with progress
    bubble.innerHTML = `📄 Processing <strong>${file.name}</strong>… <span id="ingest-pct">0%</span>
      <div style="margin-top:6px;height:3px;background:var(--cream-3);border-radius:2px;overflow:hidden">
        <div id="ingest-bar" style="height:100%;width:0%;background:var(--amber);transition:width .3s;border-radius:2px"></div>
      </div>`;

    try {
      const resp = await api.ingestStream(file.name, text);
      const reader2 = resp.body.getReader();
      const decoder = new TextDecoder();
      let buf = '';
      let chunks = 0;

      while (true) {
        const { done, value } = await reader2.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split('\n');
        buf = lines.pop();
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          try {
            const evt = JSON.parse(line.slice(6));
            if (evt.type === 'progress') {
              const pct = evt.pct + '%';
              const pctEl = document.getElementById('ingest-pct');
              const barEl = document.getElementById('ingest-bar');
              if (pctEl) pctEl.textContent = pct;
              if (barEl) barEl.style.width = pct;
            }
            if (evt.type === 'done') chunks = evt.chunks_created;
          } catch { }
        }
      }

      bubble.innerHTML = `✅ Learned from <strong>${file.name}</strong> — ${chunks} sections indexed. Ask me anything about it.`;
    } catch (err) {
      bubble.innerHTML = `❌ Streaming ingest failed: ${err.message}. Trying standard ingest…`;
      try {
        const res = await api.ingest(file.name, text);
        bubble.innerHTML = `✅ Learned from <strong>${file.name}</strong> — ${res.chunks_created} sections saved.`;
      } catch { }
    }
  };

  reader.readAsText(file);
}