/* js/graph.js — High-performance Canvas + D3 force graph
   Handles 100k+ nodes via Canvas rendering (not SVG).
   Color-coded by type, clustered, zoomable, interactive.
*/

const NODE_COLORS = {
  project: '#3D9970',
  preference: '#6A9470',
  person: '#4A85A0',
  feeling: '#8878C0',
  habit: '#B7E5CD',
  concept: '#A89880',
  event: '#B85A48',
  document: '#C09040',
  fact: '#5E8BB0',
  insight: '#9B6B8C',
};

const NODE_ICONS = {
  project: '◈', preference: '♡', person: '◉',
  feeling: '✦', habit: '⟳', concept: '◆', event: '◇',
  document: '📄', fact: '💡', insight: '✧',
};

const NODE_LABELS = {
  project: 'Projects', preference: 'Preferences', person: 'People',
  feeling: 'Feelings', habit: 'Habits', concept: 'Concepts',
  event: 'Events', document: 'Documents', fact: 'Facts',
  insight: 'Insights',
};

let graphSim = null;
let _selectedNode = null;
let _canvasTransform = d3.zoomIdentity;
let _hoveredNode = null;

async function renderGraph() {
  const container = document.getElementById('graph-wrap');
  if (!container) return;
  container.innerHTML = '';

  // Legend
  const legend = document.getElementById('graph-legend');
  if (legend) {
    legend.innerHTML = Object.entries(NODE_LABELS).map(([k, v]) => `
      <div class="legend-item" title="${v}">
        <div class="legend-dot" style="background:${NODE_COLORS[k]}"></div>
        <span>${v}</span>
      </div>`).join('');
  }

  // Fetch graph data
  let graphData = { nodes: [], edges: [] };
  try { graphData = await api.getGraph(); } catch { }
  if (!graphData.nodes || graphData.nodes.length === 0) {
    graphData = getDemoGraph();
  }

  const W = container.offsetWidth || 600;
  const H = container.offsetHeight || 420;
  const dpr = window.devicePixelRatio || 1;

  // Canvas-based rendering for performance
  const canvas = document.createElement('canvas');
  canvas.width = W * dpr;
  canvas.height = H * dpr;
  canvas.style.width = W + 'px';
  canvas.style.height = H + 'px';
  canvas.style.cursor = 'grab';
  canvas.style.borderRadius = '12px';
  container.appendChild(canvas);

  const ctx = canvas.getContext('2d');
  ctx.scale(dpr, dpr);

  // Valid links only
  const nodeIds = new Set(graphData.nodes.map(n => n.id));
  const links = (graphData.edges || [])
    .filter(e => nodeIds.has(e.source) && nodeIds.has(e.target))
    .map(e => ({ ...e }));

  // Node radius scaling — adaptive to dataset size
  const maxW = d3.max(graphData.nodes, d => d.weight || 1) || 1;
  const nodeCount = graphData.nodes.length;
  const baseMax = nodeCount > 500 ? 14 : nodeCount > 100 ? 20 : 30;
  const baseMin = nodeCount > 500 ? 4 : nodeCount > 100 ? 6 : 8;
  const rScale = d3.scaleSqrt().domain([0, maxW]).range([baseMin, baseMax]);

  // Adaptive simulation params
  const chargeStrength = nodeCount > 1000 ? -30 : nodeCount > 200 ? -60 : -90;
  const linkDist = nodeCount > 1000 ? 30 : nodeCount > 200 ? 45 : 60;

  // Force simulation
  if (graphSim) graphSim.stop();
  graphSim = d3.forceSimulation(graphData.nodes)
    .force('link', d3.forceLink(links).id(d => d.id)
      .distance(d => linkDist + (1 - (d.strength || 0.5)) * 40)
      .strength(d => Math.min((d.strength || 0.5) * 0.6, 0.8))
    )
    .force('charge', d3.forceManyBody().strength(chargeStrength))
    .force('center', d3.forceCenter(W / 2, H / 2))
    .force('collision', d3.forceCollide().radius(d => rScale(d.weight || 1) + 4))
    .alphaDecay(nodeCount > 500 ? 0.04 : 0.02)
    .on('tick', draw);

  // Zoom
  const zoom = d3.zoom()
    .scaleExtent([0.05, 12])
    .on('zoom', (e) => { _canvasTransform = e.transform; draw(); });

  const d3Canvas = d3.select(canvas);
  d3Canvas.call(zoom);

  // Drag
  d3Canvas.call(d3.drag()
    .subject(dragSubject)
    .on('start', dragStart)
    .on('drag', dragged)
    .on('end', dragEnd)
  );

  // Click
  canvas.addEventListener('click', (e) => {
    const node = findNode(e);
    if (node) { _selectedNode = node; showNodeDetail(node, graphData); }
    else { _selectedNode = null; clearNodeDetail(); }
    draw();
  });

  // Hover
  canvas.addEventListener('mousemove', (e) => {
    const node = findNode(e);
    _hoveredNode = node;
    canvas.style.cursor = node ? 'pointer' : 'grab';
    if (node) showTooltipCanvas(e, node); else hideTooltip();
    draw();
  });

  canvas.addEventListener('mouseleave', () => { _hoveredNode = null; hideTooltip(); draw(); });

  function findNode(e) {
    const rect = canvas.getBoundingClientRect();
    const mx = _canvasTransform.invertX(e.clientX - rect.left);
    const my = _canvasTransform.invertY(e.clientY - rect.top);
    for (let i = graphData.nodes.length - 1; i >= 0; i--) {
      const n = graphData.nodes[i];
      const r = rScale(n.weight || 1);
      const dx = mx - (n.x || 0), dy = my - (n.y || 0);
      if (dx * dx + dy * dy < (r + 4) * (r + 4)) return n;
    }
    return null;
  }

  function dragSubject(e) {
    const rect = canvas.getBoundingClientRect();
    const mx = _canvasTransform.invertX(e.x);
    const my = _canvasTransform.invertY(e.y);
    for (let i = graphData.nodes.length - 1; i >= 0; i--) {
      const n = graphData.nodes[i];
      const r = rScale(n.weight || 1);
      const dx = mx - (n.x || 0), dy = my - (n.y || 0);
      if (dx * dx + dy * dy < (r + 8) * (r + 8)) {
        n.x = _canvasTransform.applyX(n.x);
        n.y = _canvasTransform.applyY(n.y);
        return n;
      }
    }
    return null;
  }

  function dragStart(e) {
    if (!e.active) graphSim.alphaTarget(0.3).restart();
    e.subject.fx = _canvasTransform.invertX(e.x);
    e.subject.fy = _canvasTransform.invertY(e.y);
    canvas.style.cursor = 'grabbing';
  }
  function dragged(e) {
    e.subject.fx = _canvasTransform.invertX(e.x);
    e.subject.fy = _canvasTransform.invertY(e.y);
  }
  function dragEnd(e) {
    if (!e.active) graphSim.alphaTarget(0);
    e.subject.fx = null; e.subject.fy = null;
    canvas.style.cursor = 'grab';
  }

  // ── CANVAS DRAW ──
  function draw() {
    ctx.save();
    ctx.clearRect(0, 0, W, H);
    ctx.fillStyle = '#FDFAF5';
    ctx.fillRect(0, 0, W, H);
    ctx.translate(_canvasTransform.x, _canvasTransform.y);
    ctx.scale(_canvasTransform.k, _canvasTransform.k);

    const hasSel = !!_selectedNode;

    // Edges — colored by source node type
    for (const l of links) {
      const sx = l.source.x || 0, sy = l.source.y || 0;
      const tx = l.target.x || 0, ty = l.target.y || 0;
      const str = l.strength || 0.5;
      let alpha = 0.2 + str * 0.35;
      const srcType = (typeof l.source === 'object') ? l.source.type : null;
      const edgeColor = (srcType && NODE_COLORS[srcType]) || '168, 152, 128'; // fallback warm gray
      if (hasSel) {
        const sid = typeof l.source === 'object' ? l.source.id : l.source;
        const tid = typeof l.target === 'object' ? l.target.id : l.target;
        alpha = (sid === _selectedNode.id || tid === _selectedNode.id) ? 0.6 + str * 0.3 : 0.04;
      }
      ctx.beginPath();
      ctx.moveTo(sx, sy); ctx.lineTo(tx, ty);
      // Use source node color for edges
      const ec = NODE_COLORS[srcType] || '#C8BCAA';
      ctx.strokeStyle = alpha < 0.1 ? `rgba(200, 188, 170, ${alpha})` : ec + Math.round(alpha * 255).toString(16).padStart(2, '0');
      ctx.lineWidth = Math.max(0.5, str * 2.5);
      ctx.stroke();
    }

    // Nodes
    for (const n of graphData.nodes) {
      const x = n.x || 0, y = n.y || 0;
      const r = rScale(n.weight || 1);
      const color = NODE_COLORS[n.type] || '#A89880';
      const isHov = _hoveredNode && _hoveredNode.id === n.id;
      const isSel = _selectedNode && _selectedNode.id === n.id;

      let nodeAlpha = 1;
      if (hasSel && !isSel) {
        const connected = links.some(l => {
          const sid = typeof l.source === 'object' ? l.source.id : l.source;
          const tid = typeof l.target === 'object' ? l.target.id : l.target;
          return (sid === _selectedNode.id && tid === n.id) || (tid === _selectedNode.id && sid === n.id);
        });
        nodeAlpha = connected ? 0.9 : 0.15;
      }
      ctx.globalAlpha = nodeAlpha;

      // Glow ring
      if (isSel || isHov) {
        ctx.beginPath(); ctx.arc(x, y, r + 5, 0, Math.PI * 2);
        ctx.fillStyle = color + '25'; ctx.fill();
        ctx.strokeStyle = color; ctx.lineWidth = 1.5; ctx.stroke();
      }

      // Circle — solid color fill for clear color coding
      ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2);
      ctx.fillStyle = color + '55'; ctx.fill();
      ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.stroke();

      // Label
      if (r > 10 || _canvasTransform.k > 1.5) {
        const fs = Math.min(r * 0.48, 11);
        ctx.font = `500 ${fs}px "DM Sans", sans-serif`;
        ctx.fillStyle = color;
        ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
        const lbl = n.label.length > 14 ? n.label.slice(0, 13) + '…' : n.label;
        ctx.fillText(lbl, x, y);
      }
      ctx.globalAlpha = 1;
    }
    ctx.restore();
  }

  graphSim.on('end', () => fitGraphCanvas(d3Canvas, zoom, graphData.nodes, W, H));
  draw();
}

function fitGraphCanvas(canvas, zoom, nodes, W, H) {
  if (!nodes.length) return;
  const margin = 50;
  const xs = nodes.map(n => n.x).filter(x => x != null);
  const ys = nodes.map(n => n.y).filter(y => y != null);
  if (!xs.length) return;
  const x0 = Math.min(...xs) - margin, x1 = Math.max(...xs) + margin;
  const y0 = Math.min(...ys) - margin, y1 = Math.max(...ys) + margin;
  const scale = Math.min(0.92, Math.min(W / (x1 - x0), H / (y1 - y0)));
  const tx = W / 2 - scale * ((x0 + x1) / 2);
  const ty = H / 2 - scale * ((y0 + y1) / 2);
  canvas.transition().duration(700)
    .call(zoom.transform, d3.zoomIdentity.translate(tx, ty).scale(scale));
}

function showNodeDetail(d, graphData) {
  let panel = document.getElementById('node-detail');
  if (!panel) {
    panel = document.createElement('div');
    panel.id = 'node-detail';
    const wrap = document.getElementById('graph-wrap');
    if (wrap) wrap.appendChild(panel);
  }
  const connected = (graphData.edges || [])
    .filter(e => {
      const sid = typeof e.source === 'object' ? e.source.id : e.source;
      const tid = typeof e.target === 'object' ? e.target.id : e.target;
      return sid === d.id || tid === d.id;
    })
    .map(e => {
      const otherId = (typeof e.source === 'object' ? e.source.id : e.source) === d.id
        ? (typeof e.target === 'object' ? e.target.id : e.target)
        : (typeof e.source === 'object' ? e.source.id : e.source);
      const other = graphData.nodes.find(n => n.id === otherId);
      return other ? `<span class="chip" style="background:${NODE_COLORS[other.type] || '#A89880'}22;color:${NODE_COLORS[other.type] || '#A89880'};border:1px solid ${NODE_COLORS[other.type] || '#A89880'}44">${other.label}</span>` : '';
    }).filter(Boolean);

  const color = NODE_COLORS[d.type] || '#A89880';
  const icon = NODE_ICONS[d.type] || '◆';
  const typeLabel = NODE_LABELS[d.type] || d.type || 'Concept';

  panel.innerHTML = `
    <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px">
      <span style="font-size:1.1rem;color:${color}">${icon}</span>
      <strong style="font-size:.88rem;color:var(--ink)">${d.label}</strong>
      <span style="margin-left:auto;font-size:.72rem;color:${color};background:${color}18;padding:2px 7px;border-radius:10px">${typeLabel}</span>
    </div>
    ${d.description ? `<p style="font-size:.8rem;color:var(--ink-2);margin:0 0 10px;line-height:1.5">${d.description}</p>` : ''}
    ${d.weight ? `<div style="font-size:.72rem;color:var(--ink-3);margin-bottom:8px">Activation weight: ${(d.weight || 1).toFixed(2)}</div>` : ''}
    ${connected.length ? `<div style="font-size:.75rem;color:var(--ink-3);margin-bottom:5px">Connected to:</div><div style="display:flex;flex-wrap:wrap;gap:4px">${connected.join('')}</div>` : ''}
    <button onclick="clearNodeDetail()"
      style="margin-top:10px;border:none;background:none;color:var(--ink-3);font-size:.75rem;cursor:pointer;padding:0">✕ close</button>`;
  panel.style.opacity = '0';
  setTimeout(() => { panel.style.opacity = '1'; }, 20);
}

function clearNodeDetail() {
  _selectedNode = null;
  const panel = document.getElementById('node-detail');
  if (panel) panel.style.opacity = '0';
  setTimeout(() => { if (panel) panel.remove(); }, 200);
}

// Tooltip
const tooltip = (() => {
  let el = document.getElementById('graph-tooltip');
  if (!el) { el = document.createElement('div'); el.id = 'graph-tooltip'; document.body.appendChild(el); }
  return el;
})();

function showTooltipCanvas(event, d) {
  const color = NODE_COLORS[d.type] || '#A89880';
  tooltip.innerHTML = `<span style="color:${color}">${NODE_ICONS[d.type] || '◆'} <strong>${d.label}</strong></span><br><span style="opacity:.7">${d.description ? d.description.slice(0, 60) + (d.description.length > 60 ? '…' : '') : ''}</span>`;
  tooltip.style.opacity = '1';
  tooltip.style.left = (event.clientX + 14) + 'px';
  tooltip.style.top = (event.clientY - 10) + 'px';
}
function hideTooltip() { tooltip.style.opacity = '0'; }

function getDemoGraph() {
  return {
    nodes: [
      { id: 'quil', label: 'Quil', type: 'project', weight: 3.0, description: 'Local, private AI companion with persistent memory' },
      { id: 'tauri', label: 'Tauri', type: 'project', weight: 1.5, description: 'Rust-based app framework' },
      { id: 'qwen3', label: 'Qwen3 4B', type: 'project', weight: 1.5, description: 'Local LLM running in Ollama' },
      { id: 'privacy', label: 'Privacy', type: 'preference', weight: 2.2, description: 'All local, zero cloud, zero telemetry' },
      { id: 'warmth', label: 'Warmth', type: 'feeling', weight: 1.3, description: 'Calm, Headspace-like design' },
      { id: 'memory', label: 'Memory', type: 'project', weight: 2.0, description: '7-layer memory architecture' },
      { id: 'lightweight', label: 'Lightweight', type: 'preference', weight: 1.2, description: 'Vanilla JS, small binary' },
      { id: 'opensource', label: 'Open source', type: 'preference', weight: 1.0, description: 'Published on GitHub' },
      { id: 'lancedb', label: 'LanceDB', type: 'concept', weight: 1.0, description: 'Embedded vector store' },
      { id: 'learn', label: 'Learn by doing', type: 'habit', weight: 1.2, description: 'Dives in and figures out' },
      { id: 'feb2026', label: 'Feb 2026', type: 'event', weight: 0.8, description: 'Started building Quil' },
    ],
    edges: [
      { source: 'quil', target: 'tauri', strength: 0.8 },
      { source: 'quil', target: 'qwen3', strength: 0.8 },
      { source: 'quil', target: 'memory', strength: 0.9 },
      { source: 'quil', target: 'privacy', strength: 0.9 },
      { source: 'quil', target: 'warmth', strength: 0.7 },
      { source: 'quil', target: 'opensource', strength: 0.6 },
      { source: 'tauri', target: 'lightweight', strength: 0.7 },
      { source: 'tauri', target: 'privacy', strength: 0.5 },
      { source: 'lancedb', target: 'memory', strength: 0.6 },
      { source: 'lancedb', target: 'privacy', strength: 0.5 },
      { source: 'learn', target: 'quil', strength: 0.5 },
      { source: 'feb2026', target: 'quil', strength: 0.7 },
      { source: 'memory', target: 'lightweight', strength: 0.4 },
    ],
  };
}
