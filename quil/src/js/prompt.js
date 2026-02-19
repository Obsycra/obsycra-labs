/* js/prompt.js — "How I Think" panel */

async function renderPrompt() {
  const body = document.getElementById('promptBody');
  body.innerHTML = '<div style="color:var(--ink-3);font-size:.8rem;padding:8px">Loading…</div>';

  let layers = [], history = [];
  try { layers = await api.getPromptLayers(); } catch { }
  try { history = await api.getPromptHistory(); } catch { }

  if (!layers.length) layers = getDemoLayers();

  const dotColors = { core: '#A89880', learned: '#6A9470', user: '#C97B40' };

  body.innerHTML = `
    ${layers.map((l, i) => `
      <div class="p-layer" data-layer="${l.id}" style="animation-delay:${i * 0.06}s">
        <div class="p-layer-head">
          <div class="p-layer-info">
            <div class="p-layer-dot" style="background:${dotColors[l.id] || '#A89880'}"></div>
            <div>
              <h3>${l.title}</h3>
              <p>${l.subtitle}</p>
            </div>
          </div>
          <span class="p-badge p-badge-${l.badge_type}">${l.badge}</span>
        </div>
        <div class="p-layer-body">
          <textarea class="p-textarea" id="pt-${l.id}" placeholder="${l.id === 'user' ? 'Add anything you want Quil to always remember about how to talk to you…' : ''}">${l.content}</textarea>
        </div>
        <div class="p-layer-foot">
          <span class="p-foot-note">${footerNote(l)}</span>
          <button class="p-save-btn" onclick="saveLayer('${l.id}')">Save</button>
        </div>
      </div>`).join('')}

        <div class="update-log">
      <div class="update-log-head">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" stroke-linecap="round" stroke-linejoin="round"/></svg>
        Quil's prompt update history
      </div>
      ${(history.length ? history : getDemoHistory()).map((h, i) => `
        <div class="update-entry" style="animation-delay:${i * 0.05}s">
          <div class="update-entry-top">
            <strong>${h.title}</strong>
            <time>${formatHistoryDate(h.date)}</time>
          </div>
          <p>${h.explanation}</p>
          ${h.diff_remove || h.diff_add ? `
            <div class="diff-block">
              ${h.diff_remove ? `<div class="diff-remove">- ${h.diff_remove}</div>` : ''}
              ${h.diff_add ? `<div class="diff-add">+ ${h.diff_add}</div>` : ''}
            </div>` : ''}
        </div>`).join('')}
    </div>`;

  // Auto-resize all layer textareas to fit their content
  layers.forEach(l => {
    const ta = document.getElementById(`pt-${l.id}`);
    if (ta) {
      ta.style.height = 'auto';
      ta.style.height = Math.min(ta.scrollHeight, 340) + 'px';
      ta.addEventListener('input', function () {
        this.style.height = 'auto';
        this.style.height = Math.min(this.scrollHeight, 340) + 'px';
      });
    }
  });
} function footerNote(layer) {
  if (layer.id === 'core') return 'Editing this changes Quil\'s fundamental character.';
  if (layer.id === 'learned') {
    const d = layer.last_updated ? new Date(layer.last_updated).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) : 'Never';
    return `Quil last updated this on ${d} · You can edit or reset it.`;
  }
  return 'These override everything else.';
}

async function saveLayer(id) {
  const textarea = document.getElementById(`pt-${id}`);
  if (!textarea) return;
  const content = textarea.value;
  try {
    await api.updatePromptLayer(id, content);
    // Flash save confirmation
    const btn = textarea.closest('.p-layer').querySelector('.p-save-btn');
    if (btn) {
      btn.textContent = 'Saved ✓';
      btn.style.background = 'var(--sage)';
      setTimeout(() => { btn.textContent = 'Save'; btn.style.background = ''; }, 1800);
    }
  } catch {
    alert('Could not save — is the backend running?');
  }
}

function getDemoLayers() {
  return [
    {
      id: 'core', title: 'Core Personality', subtitle: "Quil's foundational character", badge: 'Core', badge_type: 'core', last_updated: null,
      content: "You are Quil — a warm, calm, private AI companion that lives entirely on this device. You're thoughtful and direct. You care about the person you're talking to. You never perform helpfulness — you just help. You don't explain what you're about to do, you do it. You're brief unless the moment calls for depth."
    },
    {
      id: 'learned', title: 'Learned Behavior', subtitle: 'Updated automatically based on what works', badge: 'Auto-evolved', badge_type: 'auto', last_updated: new Date().toISOString(),
      content: "Skip preamble — lead with the useful thing. When asked for options, give one clear recommendation unless explicitly asked for a list. At night (after 10pm), be softer and shorter. When the user is in a build session, be fast and confident — skip the caveats."
    },
    {
      id: 'user', title: 'Your Overrides', subtitle: 'Instructions you\'ve written — always highest priority', badge: 'Your words', badge_type: 'user', last_updated: null,
      content: ''
    },
  ];
}

function getDemoHistory() {
  return [
    {
      id: 'h1', title: 'Added late-night tone rule', date: new Date().toISOString(),
      explanation: 'After reviewing 6 conversations after 10pm, shorter responses got better engagement.',
      diff_remove: null, diff_add: 'At night (after 10pm), be softer and shorter.'
    },
    {
      id: 'h2', title: 'Updated recommendation style', date: new Date(Date.now() - 86400000).toISOString(),
      explanation: 'You rephrased "give me options" to "just pick one" in 4 separate conversations.',
      diff_remove: 'When asked, provide multiple options for the user to choose from.',
      diff_add: 'When asked for options, give one clear recommendation unless explicitly asked for a list.'
    },
  ];
}

function formatHistoryDate(iso) {
  if (!iso) return '';
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}
