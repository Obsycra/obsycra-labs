/* js/chat.js — Chat panel logic */

let sessionId = 'session_' + Date.now();
let isStreaming = false;
let _userInitial = 'Y';

/**
 * Escape HTML special characters in a string before it enters the markdown
 * renderer. This prevents XSS from LLM output containing raw HTML tags.
 * The markdown syntax characters (*, #, `, -, etc.) are not HTML and survive
 * escaping unchanged, so the markdown renderer still works correctly.
 */
function escapeHtml(str) {
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/**
 * Load the user's initial from semantic memory.
 * Falls back to 'Y' if no name fact is found.
 * Called from initChat() after backend is confirmed reachable.
 */
async function _loadUserInitial() {
  try {
    const facts = await api.getSemantic();
    const nameFact = facts.find(f =>
      /^(name|first name|full name)$/i.test((f.label || '').trim())
    );
    if (nameFact && nameFact.value) {
      _userInitial = nameFact.value.trim()[0].toUpperCase();
    }
  } catch { }
}

// RAF token-batching state — batches multiple tokens per animation frame
// to avoid layout thrash, while still streaming visually token-by-token.
let _tokenBuffer = '';
let _tokenBubble = null;
let _rafPending = false;
let _fullResponse = '';  // accumulate full response for markdown rendering

/**
 * Simple Markdown → HTML renderer for chat messages.
 * Handles: **bold**, *italic*, ## headings, - bullet lists, 1. numbered lists,
 * `inline code`, ```code blocks```, and paragraph breaks.
 */
function renderMarkdown(text) {
  if (!text) return '';

  // Escape HTML before any processing to prevent XSS from LLM output.
  // Markdown syntax characters (*, #, `, -, etc.) do not contain HTML
  // special chars, so they survive escaping and still render correctly.
  let html = escapeHtml(text);

  // Code blocks (```...```) — restore after escaping so < > inside code
  // blocks display as literal text (they're already escaped above).
  html = html.replace(/```(\w*)\n?([\s\S]*?)```/g, '<pre class="md-code-block"><code>$2</code></pre>');

  // Inline code (`...`)
  html = html.replace(/`([^`\n]+)`/g, '<code class="md-inline-code">$1</code>');

  // Split into lines for block-level processing
  const lines = html.split('\n');
  const processed = [];
  let inList = false;
  let listType = null; // 'ul' or 'ol'
  let inCodeBlock = false;

  for (let i = 0; i < lines.length; i++) {
    let line = lines[i];

    // Skip if inside a pre block already rendered
    if (line.includes('<pre class="md-code-block">')) { inCodeBlock = true; processed.push(line); continue; }
    if (line.includes('</pre>')) { inCodeBlock = false; processed.push(line); continue; }
    if (inCodeBlock) { processed.push(line); continue; }

    // Headings — mapped to h2/h3/h4 so each level is visually distinct.
    // # → h2 (largest), ## → h3 (medium), ### → h4 (smallest subheading).
    if (line.match(/^###\s+(.+)/)) {
      if (inList) { processed.push(listType === 'ol' ? '</ol>' : '</ul>'); inList = false; }
      line = `<h4 class="md-h3">${line.replace(/^###\s+/, '')}</h4>`;
      processed.push(line); continue;
    }
    if (line.match(/^##\s+(.+)/)) {
      if (inList) { processed.push(listType === 'ol' ? '</ol>' : '</ul>'); inList = false; }
      line = `<h3 class="md-h2">${line.replace(/^##\s+/, '')}</h3>`;
      processed.push(line); continue;
    }
    if (line.match(/^#\s+(.+)/)) {
      if (inList) { processed.push(listType === 'ol' ? '</ol>' : '</ul>'); inList = false; }
      line = `<h2 class="md-h1">${line.replace(/^#\s+/, '')}</h2>`;
      processed.push(line); continue;
    }

    // Unordered list items (- or *)
    if (line.match(/^\s*[-*]\s+(.+)/)) {
      const content = line.replace(/^\s*[-*]\s+/, '');
      if (!inList || listType !== 'ul') {
        if (inList) processed.push(listType === 'ol' ? '</ol>' : '</ul>');
        processed.push('<ul class="md-list">');
        inList = true; listType = 'ul';
      }
      processed.push(`<li>${applyInline(content)}</li>`);
      continue;
    }

    // Ordered list items (1. 2. etc)
    if (line.match(/^\s*\d+\.\s+(.+)/)) {
      const content = line.replace(/^\s*\d+\.\s+/, '');
      if (!inList || listType !== 'ol') {
        if (inList) processed.push(listType === 'ol' ? '</ol>' : '</ul>');
        processed.push('<ol class="md-list">');
        inList = true; listType = 'ol';
      }
      processed.push(`<li>${applyInline(content)}</li>`);
      continue;
    }

    // Close list if we hit a non-list line
    if (inList) {
      processed.push(listType === 'ol' ? '</ol>' : '</ul>');
      inList = false;
    }

    // Empty line = paragraph break
    if (line.trim() === '') {
      processed.push('<div class="md-break"></div>');
      continue;
    }

    // Regular paragraph
    processed.push(`<p class="md-p">${applyInline(line)}</p>`);
  }

  if (inList) processed.push(listType === 'ol' ? '</ol>' : '</ul>');

  return processed.join('\n');
}

function applyInline(text) {
  // Bold (**text**)
  text = text.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  // Italic (*text*)
  text = text.replace(/(?<!\*)\*([^*]+)\*(?!\*)/g, '<em>$1</em>');
  return text;
}

function _flushTokenBuffer() {
  if (_tokenBubble && _tokenBuffer) {
    _fullResponse += _tokenBuffer;
    // Re-render the full response as markdown each frame
    _tokenBubble.innerHTML = renderMarkdown(_fullResponse);
    _tokenBuffer = '';
    scrollBottom();
  }
  _rafPending = false;
}

function _appendToken(bubble, content) {
  _tokenBuffer += content;
  _tokenBubble = bubble;
  if (!_rafPending) {
    _rafPending = true;
    requestAnimationFrame(_flushTokenBuffer);
  }
}

function getTime() {
  return new Date().toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
}

function scrollBottom() {
  const el = document.getElementById('messages');
  el.scrollTop = el.scrollHeight;
}

function addMsg(role, text, memoryHint = null) {
  const el = document.getElementById('messages');
  const div = document.createElement('div');
  div.className = `msg ${role}`;

  const avatar = role === 'quil'
    ? `<div class="msg-av"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4"><path d="M20 4C16 4 8 6 4 20c2-4 6-6 8-6-2 2-2 4-1 6 4-8 8-10 9-16z" stroke-linejoin="round"/></svg></div>`
    : `<div class="msg-av">${_userInitial}</div>`;

  const memTag = memoryHint
    ? `<div class="mem-tag" onclick="switchPanel('brain')">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M12 1v4M12 19v4M4.22 4.22l2.83 2.83M16.95 16.95l2.83 2.83M1 12h4M19 12h4M4.22 19.78l2.83-2.83M16.95 7.05l2.83-2.83"/></svg>
        ${memoryHint}
      </div>` : '';

  div.innerHTML = `
    ${avatar}
    <div class="msg-inner">
      <div class="bubble" id="bubble-${Date.now()}">${text}</div>
      ${memTag}
      <div class="msg-time">${getTime()}</div>
    </div>`;

  el.appendChild(div);
  scrollBottom();
  return div.querySelector('.bubble');
}

function addTyping() {
  const el = document.getElementById('messages');
  const wrap = document.createElement('div');
  wrap.className = 'msg quil';
  wrap.id = 'typing-wrap';
  wrap.innerHTML = `
    <div class="msg-inner">
      <div class="thinking-chip" id="thinking-chip">
        <svg class="thinking-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4">
          <path d="M20 4C16 4 8 6 4 20c2-4 6-6 8-6-2 2-2 4-1 6 4-8 8-10 9-16z" stroke-linejoin="round"/>
        </svg>
        <span class="thinking-chip-label">
          Quil is thinking
          <span class="thinking-dots-inline"><span></span><span></span><span></span></span>
        </span>
        <button class="thinking-close-btn" title="Dismiss" onclick="document.getElementById('typing-wrap')?.remove()">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" width="9" height="9">
            <path d="M18 6L6 18M6 6l12 12" stroke-linecap="round"/>
          </svg>
        </button>
      </div>
    </div>`;
  el.appendChild(wrap);
  scrollBottom();
  return wrap;
}

async function sendMessage() {
  if (isStreaming) return;
  const input = document.getElementById('msgInput');
  const text = input.value.trim();
  if (!text) return;

  input.value = '';
  input.style.height = 'auto';
  isStreaming = true;

  addMsg('user', text);
  const typing = addTyping();

  try {
    const resp = await api.chat(text, sessionId);

    if (!resp.ok) throw new Error('Backend unreachable');

    typing.remove();

    // Create quil bubble for streaming
    const bubble = addMsg('quil', '');
    _tokenBuffer = '';
    _tokenBubble = bubble;
    _rafPending = false;
    _fullResponse = '';

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      const lines = decoder.decode(value).split('\n');
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        try {
          const data = JSON.parse(line.slice(6));
          if (data.type === 'token') {
            _appendToken(bubble, data.content);
          } else if (data.type === 'done') {
            updateMemoryCount();
          } else if (data.type === 'error') {
            bubble.textContent = data.content;
          }
        } catch { }
      }
    }

  } catch (err) {
    typing.remove();
    const isConnErr = err.message.includes('fetch') || err.message.includes('network') || err.name === 'TypeError';
    const msg = isConnErr
      ? '⚠ Cannot reach backend. Is it running?'
      : `⚠ Error: ${err.message}`;
    addMsg('quil', msg);
    console.error('Chat error:', err);
  }

  isStreaming = false;
}

async function updateMemoryCount() {
  try {
    const facts = await api.getSemantic();
    const subtitle = document.getElementById('chatSubtitle');
    if (subtitle) subtitle.textContent = `Quil remembers ${facts.length} thing${facts.length !== 1 ? 's' : ''} about you`;
  } catch { }
}

// ── GOVERNOR WEBSOCKET (Real-time proactive events) ──
let _govWs = null;

function startGovernorSocket() {
  function connect() {
    try {
      _govWs = new WebSocket('ws://127.0.0.1:8765/ws/events');

      _govWs.onmessage = (e) => {
        try {
          const event = JSON.parse(e.data);
          if (event.type === 'alert' && event.payload?.content) {
            addMsg('quil', event.payload.content);
          }
          if (event.type === 'status' && event.payload?.msg) {
            console.log('[Governor]', event.payload.msg);
          }
          // Workshop events
          if (event.type === 'workshop_queued' && event.payload) {
            appendWorkshopJob(event.payload);
            updateWorkshopStatus();
          }
          if (event.type === 'workshop_started' && event.payload) {
            if (_workshopJobsLocal[event.payload.job_id]) {
              _workshopJobsLocal[event.payload.job_id].status = 'running';
            }
            updateWorkshopStatus();
          }
          if (event.type === 'workshop_plan' && event.payload) {
            const j = _workshopJobsLocal[event.payload.job_id];
            if (j) { j.steps = event.payload.steps; }
            appendWorkshopJob(j || event.payload);
          }
          if (event.type === 'workshop_step_start' && event.payload) {
            const j = _workshopJobsLocal[event.payload.job_id];
            if (j && j.steps && j.steps[event.payload.step_index]) {
              j.steps[event.payload.step_index].status = 'running';
              _updateStepStatus(event.payload.job_id, event.payload.step_index, 'running', null);
            }
          }
          if (event.type === 'workshop_step_done' && event.payload) {
            const j = _workshopJobsLocal[event.payload.job_id];
            if (j && j.steps && j.steps[event.payload.step_index]) {
              j.steps[event.payload.step_index].status = 'done';
              j.steps[event.payload.step_index].output = event.payload.output;
              _updateStepStatus(event.payload.job_id, event.payload.step_index, 'done', event.payload.output);
            }
          }
          if (event.type === 'workshop_done' && event.payload) {
            handleWorkshopDone(event.payload);
          }
          if (event.type === 'workshop_error' && event.payload) {
            handleWorkshopError(event.payload);
          }
          if (event.type === 'workshop_cancelled' && event.payload) {
            const j = _workshopJobsLocal[event.payload.job_id];
            if (j) { j.status = 'cancelled'; }
            appendWorkshopJob(j || { job_id: event.payload.job_id, status: 'cancelled', steps: [] });
            updateWorkshopStatus();
          }
        } catch { }
      };

      // Keep-alive ping every 20s
      _govWs.onopen = () => {
        setInterval(() => {
          if (_govWs && _govWs.readyState === WebSocket.OPEN) {
            _govWs.send('ping');
          }
        }, 20000);
      };

      // Reconnect on close
      _govWs.onclose = () => { setTimeout(connect, 5000); };
      _govWs.onerror = () => { _govWs.close(); };
    } catch { }
  }
  connect();
}

// ── AUTONOMY LOOP (Polling fallback for environments without WS) ──
function startAutonomyLoop() {
  setInterval(async () => {
    // Skip poll if WebSocket is connected (Governor handles it)
    if (_govWs && _govWs.readyState === WebSocket.OPEN) return;
    try {
      const data = await api.checkAutonomy();
      if (data.alerts && data.alerts.length) {
        data.alerts.forEach(alert => addMsg('quil', alert.content));
      }
    } catch { }
  }, 30000);
}


function initChat() {
  const input = document.getElementById('msgInput');

  input.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });

  input.addEventListener('input', function () {
    this.style.height = 'auto';
    this.style.height = Math.min(this.scrollHeight, 110) + 'px';
    // Toggle send button active state based on input content
    const sendBtn = document.getElementById('sendBtn');
    if (sendBtn) {
      if (this.value.trim().length > 0) {
        sendBtn.classList.add('active');
      } else {
        sendBtn.classList.remove('active');
      }
    }
  });

  // Greeting
  const hour = new Date().getHours();
  const greet = hour < 12 ? 'Good morning ✦' : hour < 17 ? 'Good afternoon ✦' : 'Good evening ✦';
  const el = document.getElementById('chatGreeting');
  if (el) el.textContent = greet;

  // Check backend & update memory count
  api.health().then(h => {
    const mood = document.getElementById('moodText');
    if (h.ollama) {
      if (mood) mood.textContent = h.model_ready ? 'Ready' : 'Ollama connected';
    } else {
      if (mood) mood.textContent = 'Start Ollama';
    }
  }).catch(() => {
    const mood = document.getElementById('moodText');
    if (mood) mood.textContent = 'Backend offline';
  });

  updateMemoryCount();
  _loadUserInitial();    // Pull user's initial from semantic memory for avatar
  startGovernorSocket(); // Real-time Governor connection
  startAutonomyLoop();   // Polling fallback
}