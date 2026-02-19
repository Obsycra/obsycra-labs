/* js/api.js */
const API = 'http://127.0.0.1:8765';

const api = {
  async health() { const r = await fetch(`${API}/health`); return r.json(); },

  chat(message, sessionId = 'default') {
    return fetch(`${API}/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, session_id: sessionId, stream: true }),
    });
  },

  // Ingestion
  async ingest(filename, text) {
    const r = await fetch(`${API}/ingest`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filename, text })
    });
    return r.json();
  },

  // Autonomy Check (Heartbeat)
  async checkAutonomy() {
    try {
      const r = await fetch(`${API}/autonomy/check`);
      return r.json();
    } catch { return { alerts: [] }; }
  },

  // Dream Trigger
  async triggerDream() {
    return fetch(`${API}/dev/dream`, { method: 'POST' });
  },

  // Memory Getters
  async getGraph() { return (await fetch(`${API}/memory/graph`)).json(); },
  async getEpisodic() { return (await fetch(`${API}/memory/episodic`)).json(); },
  async getSemantic() { return (await fetch(`${API}/memory/semantic`)).json(); },
  async getProcedural() { return (await fetch(`${API}/memory/procedural`)).json(); },
  async getProspective() { return (await fetch(`${API}/memory/prospective`)).json(); },
  async getEmotional() { return (await fetch(`${API}/memory/emotional`)).json(); },
  async getContextual() { return (await fetch(`${API}/memory/contextual`)).json(); },
  async getDreamLog(limit = 30) { return (await fetch(`${API}/memory/dreams?limit=${limit}`)).json(); },

  // Workshop (background autonomous tasks)
  async startWorkshop(topic, sessionId = 'default') {
    const r = await fetch(`${API}/workshop/draft`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ topic, session_id: sessionId }),
    });
    return r.json();
  },
  async getWorkshopJobs() { return (await fetch(`${API}/workshop/jobs`)).json(); },

  // Streaming ingest for large files (returns fetch response for SSE reading)
  ingestStream(filename, text) {
    return fetch(`${API}/ingest/stream`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filename, text }),
    });
  },

  // Prompt Getters
  async getPromptLayers() { return (await fetch(`${API}/prompt/layers`)).json(); },
  async getPromptHistory() { return (await fetch(`${API}/prompt/history`)).json(); },

  // Write Ops
  async updatePromptLayer(id, content) {
    return fetch(`${API}/prompt/layers/${id}`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content }),
    });
  },
  async deleteFact(id) { return fetch(`${API}/memory/semantic/${id}`, { method: 'DELETE' }); },
  async updateFact(id, value) {
    return fetch(`${API}/memory/semantic/${id}`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ value }),
    });
  },
  async markDone(id) { return fetch(`${API}/memory/prospective/${id}/done`, { method: 'PUT' }); },
};