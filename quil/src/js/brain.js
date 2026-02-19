/* js/brain.js — Renders all 7 memory type panels */

const SENTIMENT_COLORS = {
  positive: '#3D9970', excited: '#B7E5CD',
  neutral: '#A89880', stressed: '#B85A48', negative: '#B85A48',
};

async function renderBrain(memType) {
  const body = document.getElementById('brainBody');
  body.innerHTML = '<div style="color:var(--ink-3);font-size:.8rem;padding:8px">Loading…</div>';

  switch (memType) {
    case 'associative': renderAssociative(body); break;
    case 'episodic': renderEpisodic(body); break;
    case 'semantic': renderSemantic(body); break;
    case 'procedural': renderProcedural(body); break;
    case 'prospective': renderProspective(body); break;
    case 'emotional': renderEmotional(body); break;
    case 'contextual': renderContextual(body); break;
    case 'dreams': renderDreams(body); break;
  }
}

// ── DREAMS (FIX 4) ────────────────────────────────────────────────────────

async function renderDreams(body) {
  body.innerHTML = `
    <div class="section-note">What Quil thought about while you were away.
    Background consolidation runs every hour — pruning, pattern-finding, generating insights.</div>
    <div style="display:flex;gap:8px;margin-bottom:16px">
      <button onclick="triggerDream()" style="padding:7px 14px;background:var(--ink);color:var(--cream);
        border:none;border-radius:8px;font-family:var(--font-sans);font-size:.79rem;cursor:pointer;">
        ▶ Run dream cycle now
      </button>
    </div>
    <div id="dream-list"></div>`;

  const list = document.getElementById('dream-list');
  let entries = [];
  try { entries = await api.getDreamLog(30); } catch { }

  if (!entries.length) {
    list.innerHTML = `<div class="mem-card" style="color:var(--ink-3);font-size:.84rem">
      No dream cycles yet. Quil dreams every hour while the app is running.
      Click "Run dream cycle now" to trigger one immediately.</div>`;
    return;
  }

  const actionLabels = {
    graph_pruned: { icon: '🌿', label: 'Graph pruned' },
    prospective_overdue: { icon: '⏰', label: 'Overdue reminder' },
    pattern_promoted: { icon: '⬆️', label: 'Pattern learned' },
    insight_generated: { icon: '✦', label: 'New insight' },
    prompt_rejected: { icon: '⚠️', label: 'Prompt update rejected' },
    prompt_evolved: { icon: '🧠', label: 'Prompt evolved' },
  };

  list.innerHTML = `<div class="timeline">${entries.map((e, i) => {
    const meta = actionLabels[e.action] || { icon: '💭', label: e.action };
    return `
        <div class="tl-item" style="animation-delay:${i * 0.03}s">
          <div class="tl-dot" style="background:var(--amber)"></div>
          <div class="tl-date">${formatDate(e.timestamp)}</div>
          <div class="tl-card">
            <div style="display:flex;align-items:center;gap:7px;margin-bottom:4px">
              <span style="font-size:.95rem">${meta.icon}</span>
              <strong style="font-size:.8rem;font-weight:500;color:var(--ink-2)">${meta.label}</strong>
            </div>
            <p>${e.detail}</p>
          </div>
        </div>`;
  }).join('')
    }</div>`;
}

async function triggerDream() {
  try {
    await api.triggerDream();
    setTimeout(() => renderBrain('dreams'), 3000);
  } catch {
    alert('Could not trigger dream cycle — is the backend running?');
  }
}

// ── ASSOCIATIVE (graph) ───────────────────────────────────────────────────

function renderAssociative(body) {
  body.innerHTML = `
    <div class="section-note">This is how Quil's memories connect. Drag nodes, scroll to zoom, hover to explore. The graph grows as you talk.</div>
    <div class="graph-legend" id="graph-legend"></div>
    <div id="graph-wrap"></div>`;
  renderGraph();
}

// ── EPISODIC ──────────────────────────────────────────────────────────────

async function renderEpisodic(body) {
  body.innerHTML = '<div class="section-note">A journal of your time with Quil — moments, decisions, and thoughts worth remembering.</div><div id="ep-list"></div>';
  const list = document.getElementById('ep-list');

  let memories = [];
  let isReal = false;
  try {
    memories = await api.getEpisodic();
    if (memories && memories.length) isReal = true;
  } catch { }

  if (!memories || !memories.length) {
    memories = getDemoEpisodic();
  }

  if (!isReal) {
    list.innerHTML += `<div style="font-size:.76rem;color:var(--ink-3);padding:0 0 12px;font-style:italic">Showing example entries — your real memories will appear here after chatting.</div>`;
  }

  list.innerHTML += `<div class="timeline">${memories.map((m, i) => {
    // Use user_message as preview if summary looks generic
    const summaryText = m.summary || '';
    const isGeneric = !summaryText || summaryText.length < 12 || summaryText.toLowerCase().startsWith('conversation turn');
    const displayText = isGeneric && m.user_message
      ? `"${m.user_message.slice(0, 100)}${m.user_message.length > 100 ? '…' : ''}"`
      : summaryText;
    return `
      <div class="tl-item" style="animation-delay:${i * 0.04}s">
        <div class="tl-dot" style="background:${SENTIMENT_COLORS[m.sentiment] || '#3D9970'}"></div>
        <div class="tl-date">${formatDate(m.timestamp)}</div>
        <div class="tl-card">
          <p>${displayText}</p>
          <div class="tl-tags">${(m.tags || []).map(t => `<span class="chip chip-amber">${t}</span>`).join('')}</div>
        </div>
      </div>`;
  }).join('')}</div>`;
}

// ── SEMANTIC ──────────────────────────────────────────────────────────────

async function renderSemantic(body) {
  body.innerHTML = `
    <div class="section-note">Facts Quil has learned about you over time. Edit or remove anything here.</div>
    <div class="fact-grid" id="fact-grid"></div>`;
  const grid = document.getElementById('fact-grid');

  let facts = [];
  try { facts = await api.getSemantic(); } catch { }
  if (!facts.length) facts = getDemoSemantic();

  grid.innerHTML = facts.map((f, i) => `
    <div class="fact-card" style="animation-delay:${i * 0.04}s" id="fc-${f.id}">
      <div class="fact-label">${f.label || f.category}</div>
      <div class="fact-value" id="fv-${f.id}">${f.value}</div>
      <div class="fact-actions">
        <button class="fact-btn" onclick="editFact('${f.id}')" title="Edit">✎</button>
        <button class="fact-btn" onclick="deleteFact('${f.id}')" title="Remove">✕</button>
      </div>
    </div>`).join('');
}

async function deleteFact(id) {
  const card = document.getElementById(`fc-${id}`);
  if (card) { card.style.opacity = '0'; card.style.transform = 'scale(.95)'; }
  try { await api.deleteFact(id); } catch { }
  setTimeout(() => card && card.remove(), 200);
}

async function editFact(id) {
  const valEl = document.getElementById(`fv-${id}`);
  if (!valEl) return;
  const current = valEl.textContent;
  const input = document.createElement('input');
  input.value = current;
  input.style.cssText = 'width:100%;border:none;background:transparent;font:inherit;color:inherit;outline:none;';
  valEl.replaceWith(input);
  input.focus();
  input.select();
  input.addEventListener('blur', async () => {
    const newVal = input.value.trim() || current;
    try { await api.updateFact(id, newVal); } catch { }
    const span = document.createElement('div');
    span.className = 'fact-value'; span.id = `fv-${id}`; span.textContent = newVal;
    input.replaceWith(span);
  });
}

// ── PROCEDURAL ────────────────────────────────────────────────────────────

async function renderProcedural(body) {
  body.innerHTML = `
    <div class="section-note">Behavioral rules Quil has learned from repeated patterns — promoted automatically from the reflection bank after the same lesson appears 3+ times.</div>
    <div class="rule-list" id="rule-list"></div>`;
  const list = document.getElementById('rule-list');

  let rules = [];
  let isReal = false;
  try { rules = await api.getProcedural(); if (rules && rules.length) isReal = true; } catch { }
  if (!rules || !rules.length) {
    list.innerHTML = `<div class="mem-card" style="color:var(--ink-3);font-size:.84rem;line-height:1.6">
      No procedural rules yet.<br>
      <span style="font-size:.78rem">Rules are auto-promoted from the reflection bank when the same lesson appears 3 or more times. Run a dream cycle or keep chatting and Quil will learn your preferences.</span>
    </div>`;
    return;
  }

  const icons = [
    `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M13 10V3L4 14h7v7l9-11h-7z" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
    `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
    `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M4.318 6.318a4.5 4.5 0 000 6.364L12 20.364l7.682-7.682a4.5 4.5 0 00-6.364-6.364L12 7.636l-1.318-1.318a4.5 4.5 0 00-6.364 0z" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
    `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
  ];

  list.innerHTML = rules.map((r, i) => `
    <div class="rule-card" style="animation-delay:${i * 0.04}s">
      <div class="rule-icon">${icons[i % icons.length]}</div>
      <div>
        <p>${r.rule}</p>
        <small>${r.reasoning}</small>
      </div>
    </div>`).join('');
}

// ── PROSPECTIVE ───────────────────────────────────────────────────────────

async function renderProspective(body) {
  body.innerHTML = `
    <div class="section-note">Things you mentioned wanting to do or follow up on. Quil will surface these at the right moment. You can also add your own tasks.</div>
    <div class="prospect-add-wrap" style="margin-bottom:14px;display:flex;gap:8px">
      <input type="text" id="prospect-add-input" placeholder="Add a task or reminder…" style="flex:1;padding:9px 14px;border:1.5px solid var(--cream-3);border-radius:10px;background:var(--warm-white);font-family:var(--font-sans);font-size:.84rem;color:var(--ink);outline:none;" />
      <select id="prospect-add-urgency" style="padding:9px 10px;border:1.5px solid var(--cream-3);border-radius:10px;background:var(--warm-white);font-family:var(--font-sans);font-size:.8rem;color:var(--ink-2);outline:none;">
        <option value="soon">Soon</option>
        <option value="urgent">Urgent</option>
        <option value="later">Later</option>
      </select>
      <button onclick="addProspectiveItem()" style="padding:9px 16px;background:var(--ink);color:var(--cream);border:none;border-radius:10px;font-family:var(--font-sans);font-size:.82rem;cursor:pointer;">Add</button>
    </div>
    <div class="prospect-list" id="prospect-list"></div>`;
  const list = document.getElementById('prospect-list');

  let items = [];
  let isReal = false;
  try { items = await api.getProspective(); if (items && items.length) isReal = true; } catch { }
  if (!items.length) items = getDemoProspective();

  if (!isReal) {
    list.insertAdjacentHTML('beforebegin', `<div style="font-size:.76rem;color:var(--ink-3);padding:0 0 12px;font-style:italic">Showing examples — your real tasks will appear here.</div>`);
  }

  // Separate active and done items
  const active = items.filter(i => !i.done);
  const done = items.filter(i => i.done);

  let html = active.map((item, i) => `
    <div class="prospect-card" style="animation-delay:${i * 0.04}s" id="pr-${item.id}">
      <div class="urgency-bar urgency-${item.urgency}"></div>
      <p>${item.content}</p>
      <span class="prospect-date">${item.due_hint || formatDate(item.created_at, true)}</span>
      <div class="done-circle" onclick="markDone('${item.id}', this)" title="Mark done"></div>
    </div>`).join('');

  if (done.length) {
    html += `<div style="font-size:.72rem;color:var(--ink-3);margin-top:16px;margin-bottom:6px;font-weight:500">COMPLETED</div>`;
    html += done.slice(0, 5).map((item, i) => `
      <div class="prospect-card prospect-done" style="animation-delay:${(active.length + i) * 0.04}s" id="pr-${item.id}">
        <div class="urgency-bar urgency-${item.urgency}"></div>
        <p>${item.content}</p>
        <span class="prospect-date">${item.due_hint || formatDate(item.created_at, true)}</span>
        <div class="done-circle done"></div>
      </div>`).join('');
  }

  list.innerHTML = html;
}

async function addProspectiveItem() {
  const input = document.getElementById('prospect-add-input');
  const urgency = document.getElementById('prospect-add-urgency');
  if (!input || !input.value.trim()) return;
  try {
    await fetch(`${API}/memory/prospective`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content: input.value.trim(), urgency: urgency.value }),
    });
    input.value = '';
    renderBrain('prospective');
  } catch { alert('Could not add task'); }
}

async function markDone(id, el) {
  el.classList.toggle('done');
  try { await api.markDone(id); } catch { }
  const card = document.getElementById(`pr-${id}`);
  if (card) {
    setTimeout(() => { card.style.opacity = '0.4'; }, 200);
  }
}

// ── EMOTIONAL ─────────────────────────────────────────────────────────────

async function renderEmotional(body) {
  body.innerHTML = `
    <div class="section-note">Quil notices your emotional tone over time — not to judge, just to show up better for you. Score ranges from negative (-1) to positive (+1).</div>
    <div class="mood-chart">
      <h4>Your mood this week</h4>
      <div class="mood-bars" id="mood-bars"></div>
    </div>
    <div class="mood-insights" id="mood-insights"></div>`;

  let data = [];
  try { data = await api.getEmotional(); } catch { }
  if (!data.length) data = getDemoEmotional();

  const bars = document.getElementById('mood-bars');
  const week = data.slice(-7);

  // Color bars by emotion/score
  const emotionColors = {
    positive: '#3D9970', excited: '#6A9470', curious: '#4A85A0',
    neutral: '#A89880', stressed: '#C09040', anxious: '#C09040',
    negative: '#B85A48', sad: '#8878C0',
  };

  bars.innerHTML = week.map((d, i) => {
    const color = emotionColors[d.dominant_emotion] || '#A89880';
    return `
      <div class="mbar-wrap">
        <div class="mbar" id="mbar-${i}" style="height:3px;background:${color};opacity:.85"></div>
        <div class="mbar-lbl">${d.day_label}</div>
      </div>`;
  }).join('');

  // score is -1..1 — map to 5%..95% bar height so negatives still show
  week.forEach((d, i) => {
    setTimeout(() => {
      const el = document.getElementById(`mbar-${i}`);
      if (el) el.style.height = Math.round(((d.score + 1) / 2) * 90 + 5) + '%';
    }, 80 + i * 60);
  });

  // Build real insights from data
  const insightEl = document.getElementById('mood-insights');
  if (week.length) {
    const avg = week.reduce((s, d) => s + d.score, 0) / week.length;
    const best = week.reduce((a, b) => a.score > b.score ? a : b);
    const worst = week.reduce((a, b) => a.score < b.score ? a : b);
    const avgLabel = avg > 0.3 ? 'positive' : avg > 0 ? 'slightly positive' : avg > -0.2 ? 'steady' : 'low';
    const emotions = week.map(d => d.dominant_emotion).filter(Boolean);
    const mostCommon = emotions.sort((a, b) => emotions.filter(v => v === a).length - emotions.filter(v => v === b).length).pop() || 'neutral';
    insightEl.innerHTML = `
      <div class="mood-insight">✦ Overall mood this period: <strong>${avgLabel}</strong> (avg score ${avg.toFixed(2)})</div>
      <div class="mood-insight">📊 Most frequent emotion: <strong>${mostCommon}</strong></div>
      <div class="mood-insight">🌱 Best day: <strong>${best.day_label}</strong> — ${best.dominant_emotion} (${best.score > 0 ? '+' : ''}${best.score.toFixed(2)})</div>
      ${week.length > 1 ? `<div class="mood-insight">☁️ Hardest day: <strong>${worst.day_label}</strong> — ${worst.dominant_emotion} (${worst.score > 0 ? '+' : ''}${worst.score.toFixed(2)})</div>` : ''}`;
  } else {
    insightEl.innerHTML = `<div class="mood-insight" style="color:var(--ink-3)">No emotional data yet — insights appear after your first conversations.</div>`;
  }
}

// ── CONTEXTUAL ────────────────────────────────────────────────────────────

async function renderContextual(body) {
  body.innerHTML = `
    <div class="section-note">Patterns Quil has noticed about when and how you use it.</div>
    <div class="context-grid" id="ctx-grid"></div>`;

  let patterns = [];
  let isRealCtx = false;
  try { patterns = await api.getContextual(); if (patterns && patterns.length) isRealCtx = true; } catch { }
  if (!patterns || !patterns.length) patterns = getDemoContextual();

  const grid = document.getElementById('ctx-grid');
  if (!isRealCtx && patterns.length) {
    grid.insertAdjacentHTML('beforebegin', `<div style="font-size:.76rem;color:var(--ink-3);padding:0 0 12px;font-style:italic">Showing examples — real patterns detected automatically as you chat.</div>`);
  }

  grid.innerHTML = patterns.map((p, i) => `
    <div class="ctx-card" style="animation-delay:${i * 0.04}s">
      <div class="ctx-icon">${p.icon}</div>
      <h4>${p.trigger}</h4>
      <p>${p.pattern}</p>
    </div>`).join('');
}

// ── DEMO DATA ─────────────────────────────────────────────────────────────

function getDemoEpisodic() {
  return [
    { id: '1', timestamp: new Date().toISOString(), summary: 'Designing the full architecture for Quil — memory layers, Tauri stack, and D3 force graph for the brain view.', tags: ['Quil', 'architecture'], sentiment: 'excited' },
    { id: '2', timestamp: new Date(Date.now() - 86400000).toISOString(), summary: 'Decided Quil should feel warm and calm like Headspace. Chose vanilla JS over React for the frontend.', tags: ['design', 'tech stack'], sentiment: 'positive' },
    { id: '3', timestamp: new Date(Date.now() - 172800000).toISOString(), summary: 'Started exploring the AI agent space. Drawn to the memory problem — why agents reset every session.', tags: ['exploration', 'agent memory'], sentiment: 'positive' },
  ];
}

function getDemoSemantic() {
  return [
    { id: 's1', category: 'project', label: 'Current project', value: 'Building Quil — local, private AI companion with persistent memory' },
    { id: 's2', category: 'preference', label: 'Communication style', value: 'Direct and concise — skip the preamble' },
    { id: 's3', category: 'preference', label: 'Design philosophy', value: 'Warm, calm, accessible to everyday people' },
    { id: 's4', category: 'preference', label: 'Tech preference', value: 'Lightweight over complex, vanilla over frameworks' },
    { id: 's5', category: 'value', label: 'Core value', value: 'Privacy — everything local, zero cloud' },
    { id: 's6', category: 'habit', label: 'Learning style', value: 'Learns by building and diving in' },
  ];
}

function getDemoRules() {
  return [
    { id: 'r1', rule: 'Skip preamble — lead with the useful thing.', reasoning: 'Learned from 12 interactions where direct responses got more engagement', evidence_count: 12 },
    { id: 'r2', rule: 'Give one clear recommendation, not a list of options.', reasoning: 'You rephrased "give me options" to "just pick one" in 4 conversations', evidence_count: 4 },
    { id: 'r3', rule: 'Celebrate things built, briefly and genuinely.', reasoning: 'Inferred from emotional patterns around sharing completed work', evidence_count: 3 },
    { id: 'r4', rule: 'After 10pm, be softer and shorter.', reasoning: 'Engagement drops with long responses in late-night sessions', evidence_count: 8 },
  ];
}

function getDemoProspective() {
  return [
    { id: 'p1', content: 'Finish the Quil prototype and push to GitHub', urgency: 'urgent', due_hint: 'This week', done: false, created_at: new Date().toISOString() },
    { id: 'p2', content: 'Set up Tauri project and connect to Ollama', urgency: 'soon', due_hint: 'Soon', done: false, created_at: new Date().toISOString() },
    { id: 'p3', content: 'Explore LanceDB as the embedded vector store', urgency: 'soon', due_hint: 'Mentioned today', done: false, created_at: new Date().toISOString() },
    { id: 'p4', content: 'Figure out the self-improving prompt scoring logic', urgency: 'later', due_hint: 'When ready', done: false, created_at: new Date().toISOString() },
  ];
}

function getDemoEmotional() {
  const days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  const scores = [0.3, 0.5, 0.6, 0.7, 0.85, 0.6, 0.9];
  return days.map((d, i) => ({ day_label: d, score: scores[i], dominant_emotion: 'positive' }));
}

function getDemoContextual() {
  return [
    { id: 'c1', icon: '🌙', trigger: 'Late night', pattern: 'After 10pm you want to think out loud, not get things done. Quil becomes reflective.' },
    { id: 'c2', icon: '⌨️', trigger: 'When pasting code', pattern: 'Almost always want debugging help, not an explanation of what the code does.' },
    { id: 'c3', icon: '☀️', trigger: 'Morning sessions', pattern: 'Task-oriented — planning, quick questions. Quil stays concise and practical.' },
    { id: 'c4', icon: '💭', trigger: 'Long messages', pattern: 'When you write a lot, you\'re processing. Quil listens more, reflects back.' },
    { id: 'c5', icon: '🔁', trigger: 'Rephrasing signals', pattern: 'When you rephrase twice, the response missed the point — a cue to recalibrate.' },
    { id: 'c6', icon: '🚀', trigger: 'Build sessions', pattern: 'In flow? Fast, confident answers — no caveats, no "it depends."' },
  ];
}

function formatDate(iso, short = false) {
  if (!iso) return '';
  const d = new Date(iso);
  if (short) return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }) +
    ' · ' + d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
}