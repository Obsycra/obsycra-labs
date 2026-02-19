"""
Quil Memory Manager v5
======================
1. CACHING: 5-minute TTL cache for all read queries.
2. AUTONOMY: Proactive alert surfacing with OS context awareness.
3. BATCH INGESTION: Single LLM call per episode (was 6). Event-boundary safe.
4. HYBRID RETRIEVAL: Graph spreading-activation + lazy entropy-gated vector search.
5. CONTEXT PAGING: MemGPT-style 6k token budget with 70% eviction threshold.
6. EMOTIONAL CALIBRATION: Dynamic temperature/repeat_penalty from sentiment history.
7. EAFT: Entropy-based training queue for continual local learning.
8. BIOLOGICAL FORGETTING: Chronological edge decay on unused graph links.
"""

import json
import math
import uuid
import asyncio
import sqlite3
import re
import time
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional
import httpx

# ─── CONFIG ───────────────────────────────────────────────────────────────────

DB_PATH     = Path.home() / ".quil" / "memory.db"
VECTOR_PATH = Path.home() / ".quil" / "vectors"
OLLAMA_BASE = "http://localhost:11434"

CHAT_MODEL  = "qwen3:4b"
THINK_MODEL = "qwen3:4b"
EMBED_MODEL = "nomic-embed-text"

# Context paging: hard ceiling 6k tokens, evict when 70% full
CONTEXT_PAGE_THRESHOLD = 0.70
MAX_CONTEXT_TOKENS     = 6144

# Ollama inference options applied to every call (M4 / 16GB optimized)
OLLAMA_OPTIONS = {
    "num_ctx": 6144,        # Keep well below 32k to protect unified memory
    "num_predict": 2048,    # Allow full responses — 1024 was truncating mid-sentence
    "kv_cache_type": "q8_0", # INT8 KV keys — ~40% less KV cache memory vs FP16
    "num_gpu": 99,           # Route all layers to Metal (Apple Silicon)
    "num_thread": 4,         # Leave cores for OS scheduler
    "repeat_penalty": 1.15,  # Discourage repetitive phrasing
}

# Separate options for internal memory/JSON calls — keep short to save GPU time
_MEMORY_OPTIONS = {**OLLAMA_OPTIONS, "num_predict": 512}

# Emotion → (temperature, repeat_penalty, top_p)
EMOTION_PARAMS: dict[str, tuple[float, float, float]] = {
    "stressed":  (0.30, 1.30, 0.85),
    "anxious":   (0.35, 1.25, 0.87),
    "sad":       (0.45, 1.10, 0.90),
    "negative":  (0.40, 1.20, 0.88),
    "neutral":   (0.70, 1.10, 0.95),
    "curious":   (0.75, 1.05, 0.95),
    "positive":  (0.72, 1.05, 0.95),
    "excited":   (0.80, 1.00, 0.97),
}

_last_retrieval_ids: list = []
_mem_cache = {}

# ─── THREAD-LOCAL SQLite CONNECTION POOL ──────────────────────────────────────
# One persistent connection per thread — avoids per-call open/close overhead.
_db_local = threading.local()

def _get_conn() -> sqlite3.Connection:
    if not hasattr(_db_local, "conn") or _db_local.conn is None:
        conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA cache_size=-32000")   # 32 MB page cache
        conn.execute("PRAGMA temp_store=MEMORY")
        _db_local.conn = conn
    return _db_local.conn

# ─── CACHE DECORATOR ──────────────────────────────────────────────────────────

def cached(ttl_seconds=300):
    def decorator(func):
        async def wrapper(self, *args, **kwargs):
            # Create a simple cache key from function name and args
            key = f"{func.__name__}:{str(args)}:{str(kwargs)}"
            now = time.time()
            if key in _mem_cache:
                val, timestamp = _mem_cache[key]
                if now - timestamp < ttl_seconds:
                    return val
            
            result = await func(self, *args, **kwargs)
            _mem_cache[key] = (result, now)
            return result
        return wrapper
    return decorator

# ─── ASYNC SQL HELPERS (thread-local connection pool) ────────────────────────

async def _run_sql(query: str, args=(), fetchall=True, commit=False):
    loop = asyncio.get_running_loop()

    def _exec():
        conn = _get_conn()
        c = conn.cursor()
        c.execute(query, args)
        result = c.fetchall() if fetchall else c.fetchone()
        if commit:
            conn.commit()
            _mem_cache.clear()
        return result

    return await loop.run_in_executor(None, _exec)


async def _run_many(query: str, args_list: list):
    loop = asyncio.get_running_loop()

    def _exec():
        conn = _get_conn()
        c = conn.cursor()
        c.executemany(query, args_list)
        conn.commit()
        _mem_cache.clear()

    return await loop.run_in_executor(None, _exec)


# ─── MEMORY MANAGER ───────────────────────────────────────────────────────────

class MemoryManager:

    def __init__(self):
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        VECTOR_PATH.mkdir(parents=True, exist_ok=True)
        self._init_db_sync()
        self._init_vectors()
        self._ensure_default_prompts_sync()

    # ── STARTUP INIT (Sync) ───────────────────────────────────────────────────

    def _init_db_sync(self):
        conn = sqlite3.connect(str(DB_PATH))
        
        # [CRITICAL] Enable Write-Ahead Logging for concurrency
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        
        c = conn.cursor()

        tables = [
            """CREATE TABLE IF NOT EXISTS episodic (
                id TEXT PRIMARY KEY, session_id TEXT, timestamp TEXT,
                user_message TEXT, assistant_response TEXT,
                summary TEXT, tags TEXT, sentiment TEXT, sentiment_score REAL
            )""",
            """CREATE TABLE IF NOT EXISTS semantic (
                id TEXT PRIMARY KEY, category TEXT, label TEXT, value TEXT,
                confidence REAL, source_episode_ids TEXT, last_updated TEXT
            )""",
            """CREATE TABLE IF NOT EXISTS procedural (
                id TEXT PRIMARY KEY, rule TEXT, reasoning TEXT,
                evidence_count INTEGER DEFAULT 1, created_at TEXT
            )""",
            """CREATE TABLE IF NOT EXISTS prospective (
                id TEXT PRIMARY KEY, content TEXT, urgency TEXT DEFAULT 'later',
                due_hint TEXT, done INTEGER DEFAULT 0,
                created_at TEXT, source_episode_id TEXT, surfaced INTEGER DEFAULT 0
            )""",
            """CREATE TABLE IF NOT EXISTS emotional (
                id TEXT PRIMARY KEY, date TEXT, day_label TEXT,
                score REAL, dominant_emotion TEXT
            )""",
            """CREATE TABLE IF NOT EXISTS contextual (
                id TEXT PRIMARY KEY, trigger_desc TEXT, pattern TEXT,
                icon TEXT DEFAULT '💡', confidence REAL DEFAULT 0.5,
                observation_count INTEGER DEFAULT 1
            )""",
            """CREATE TABLE IF NOT EXISTS memory_graph_nodes (
                id TEXT PRIMARY KEY, label TEXT, type TEXT,
                description TEXT, weight REAL DEFAULT 1.0,
                last_activated TEXT
            )""",
            """CREATE TABLE IF NOT EXISTS memory_graph_edges (
                source TEXT, target TEXT, strength REAL DEFAULT 0.5,
                activation_count INTEGER DEFAULT 1,
                last_activated TEXT,
                PRIMARY KEY (source, target)
            )""",
            """CREATE TABLE IF NOT EXISTS working_memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT,
                role TEXT, content TEXT, timestamp TEXT
            )""",
            """CREATE TABLE IF NOT EXISTS prompt_layers (
                id TEXT PRIMARY KEY, title TEXT, subtitle TEXT, content TEXT,
                badge TEXT, badge_type TEXT, last_updated TEXT
            )""",
            """CREATE TABLE IF NOT EXISTS prompt_history (
                id TEXT PRIMARY KEY, title TEXT, date TEXT,
                explanation TEXT, diff_remove TEXT, diff_add TEXT
            )""",
            """CREATE TABLE IF NOT EXISTS reflection_bank (
                id TEXT PRIMARY KEY, trigger_summary TEXT,
                critique TEXT, lesson TEXT,
                embedding_id TEXT, created_at TEXT
            )""",
            """CREATE TABLE IF NOT EXISTS dream_log (
                id TEXT PRIMARY KEY, timestamp TEXT, action TEXT, detail TEXT
            )""",
            """CREATE TABLE IF NOT EXISTS workshop_jobs (
                id TEXT PRIMARY KEY, topic TEXT, session_id TEXT,
                status TEXT DEFAULT 'queued', steps_json TEXT DEFAULT '[]',
                result TEXT, created_at TEXT, started_at TEXT, completed_at TEXT
            )""",
        ]

        for t in tables:
            c.execute(t)
            
        # Ensure 'surfaced' column exists in prospective (migration for older DBs)
        try:
            c.execute("ALTER TABLE prospective ADD COLUMN surfaced INTEGER DEFAULT 0")
        except:
            pass # Column already exists

        c.execute("CREATE INDEX IF NOT EXISTS idx_ep_ts  ON episodic(timestamp DESC)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_wm_sid ON working_memory(session_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_eg_src ON memory_graph_edges(source)")

        conn.commit()
        conn.close()

    def _init_vectors(self):
        try:
            import lancedb
            import pyarrow as pa

            self._ldb = lancedb.connect(str(VECTOR_PATH))

            schema = pa.schema([
                pa.field("id", pa.string()),
                pa.field("text", pa.string()),
                pa.field("memory_type", pa.string()),
                pa.field("vector", pa.list_(pa.float32(), 768)),
            ])

            if "embeddings" not in self._ldb.table_names():
                self._ldb.create_table("embeddings", schema=schema)
            self._vtable = self._ldb.open_table("embeddings")

        except ImportError:
            print("⚠️  LanceDB not installed — pip install lancedb")
            self._ldb = self._vtable = None

    def _ensure_default_prompts_sync(self):
        conn = sqlite3.connect(str(DB_PATH))
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM prompt_layers")
        if c.fetchone()[0] == 0:
            now = datetime.now(timezone.utc).isoformat()
            c.executemany("INSERT INTO prompt_layers VALUES (?,?,?,?,?,?,?)", [
                ("core", "Core Personality", "Quil's foundational character",
                 "You are Quil — a warm, calm, private AI companion that lives entirely on this device. "
                 "You are thoughtful, direct, and genuinely care about the user. "
                 "CRITICAL RULES:\n"
                 "- Be concise. Answer in 2-4 sentences unless the user asks for depth.\n"
                 "- Never ramble. Never pad your response with filler.\n"
                 "- Never make up information. If you don't know, say so.\n"
                 "- Skip preamble — lead with the answer, not 'Great question!' or 'Sure!'\n"
                 "- NEVER show your reasoning, thinking steps, or internal planning in the response. Your thinking is private.\n"
                 "- NEVER start with 'We are given...', 'Let me think...', 'The user said...', 'This seems like...', or any meta-commentary.\n"
                 "- For greetings and simple exchanges: plain warm prose only, no lists or headers.\n"
                 "- Use **bold**, bullets, and headings ONLY for technical or multi-part answers.\n"
                 "- When referencing uploaded documents or memories, cite specific content — don't paraphrase loosely.",
                 "Core", "core", now),
                ("learned", "Learned Behavior", "Updated automatically",
                 "Lead with the useful thing.",
                 "Auto-evolved", "auto", now),
                ("user", "Your Overrides", "Always highest priority",
                 "", "Your words", "user", now),
            ])
            conn.commit()
        else:
            # Upgrade: patch the core prompt if it still has the old reasoning-leaky version
            c.execute("SELECT content FROM prompt_layers WHERE id='core'")
            row = c.fetchone()
            if row and "NEVER show your reasoning" not in row[0]:
                new_core = (
                    "You are Quil — a warm, calm, private AI companion that lives entirely on this device. "
                    "You are thoughtful, direct, and genuinely care about the user. "
                    "CRITICAL RULES:\n"
                    "- Be concise. Answer in 2-4 sentences unless the user asks for depth.\n"
                    "- Never ramble. Never pad your response with filler.\n"
                    "- Never make up information. If you don't know, say so.\n"
                    "- Skip preamble — lead with the answer, not 'Great question!' or 'Sure!'\n"
                    "- NEVER show your reasoning, thinking steps, or internal planning in the response. Your thinking is private.\n"
                    "- NEVER start with 'We are given...', 'Let me think...', 'The user said...', 'This seems like...', or any meta-commentary.\n"
                    "- For greetings and simple exchanges: plain warm prose only, no lists or headers.\n"
                    "- Use **bold**, bullets, and headings ONLY for technical or multi-part answers.\n"
                    "- When referencing uploaded documents or memories, cite specific content — don't paraphrase loosely."
                )
                c.execute("UPDATE prompt_layers SET content=? WHERE id='core'", (new_core,))
                conn.commit()
                print("🔧 Core prompt upgraded: added anti-reasoning-leak rules.")
        conn.close()

    # ── LLM HELPERS ───────────────────────────────────────────────────────

    def _strip_think(self, text: str) -> str:
        """Strip qwen3/deepseek thinking tokens <think>...</think> from output."""
        text = re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.IGNORECASE)
        text = re.sub(r"<think>[\s\S]*$", "", text, flags=re.IGNORECASE)
        return text.strip()

    async def _llm(self, prompt: str, model: str = None, fmt: str = None,
                   system: str = None, timeout: int = 120,
                   extra_options: dict = None) -> str:
        """
        Standard LLM call. Injects OLLAMA_OPTIONS for M4 optimisation.
        Always strips <think> blocks — use _llm_think() to capture them.
        Internal memory/JSON calls use _MEMORY_OPTIONS (512 tokens) to save GPU time.
        """
        model = model or CHAT_MODEL
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        # /no_think directive: suppresses Qwen3 reasoning tokens at source
        messages.append({"role": "user", "content": f"/no_think\n{prompt}"})

        # Internal JSON calls (fmt=="json") use the shorter memory options
        base_opts = _MEMORY_OPTIONS if fmt == "json" else OLLAMA_OPTIONS
        opts = {**base_opts}
        if extra_options:
            opts.update(extra_options)

        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": opts,
        }
        if fmt:
            payload["format"] = fmt

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.post(f"{OLLAMA_BASE}/api/chat", json=payload)
                if r.status_code != 200:
                    print(f"LLM API Error ({r.status_code}): {r.text}")
                    return ""
                data = r.json()
                if "message" not in data or "content" not in data["message"]:
                    print(f"LLM Error: Unexpected response format: {data}")
                    return ""
                raw = data["message"]["content"]
                return self._strip_think(raw)
        except httpx.TimeoutException:
            print(f"LLM error ({model}): Request timed out after {timeout}s.")
            return ""
        except Exception as e:
            print(f"LLM error ({model}): {type(e).__name__} - {e}")
            return ""

    async def _llm_think(self, prompt: str, system: str = None,
                         timeout: int = 90) -> dict:
        """
        Workshop-only call. Forces <think> mode and CAPTURES the reasoning
        trace rather than discarding it. Returns {"output": str, "reasoning": str}.
        """
        model = THINK_MODEL
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        # No /no_think prefix — we WANT the model to think deeply
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {**OLLAMA_OPTIONS, "temperature": 0.6},
        }
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.post(f"{OLLAMA_BASE}/api/chat", json=payload)
                if r.status_code != 200:
                    return {"output": "", "reasoning": ""}
                raw = r.json().get("message", {}).get("content", "")
                think_match = re.search(r"<think>([\s\S]*?)</think>", raw, re.IGNORECASE)
                reasoning = think_match.group(1).strip() if think_match else ""
                clean = self._strip_think(raw)
                return {"output": clean, "reasoning": reasoning}
        except Exception as e:
            print(f"LLM think error: {e}")
            return {"output": "", "reasoning": ""}

    async def _llm_json(self, prompt: str, model: str = None,
                        system: str = None, timeout: int = 60) -> dict:
        raw = await self._llm(prompt, model=model, fmt="json",
                               system=system, timeout=timeout)
        raw = self._strip_think(raw)
        try:
            clean = re.sub(r"```json\s*|\s*```", "", raw).strip()
            # Find the outermost JSON object
            start = clean.find("{")
            end = clean.rfind("}")
            if start != -1 and end != -1 and end > start:
                clean = clean[start:end+1]
                return json.loads(clean)
        except Exception:
            pass
        return {}

    # ── EMBEDDING + VECTOR SEARCH ─────────────────────────────────────────

    async def _embed(self, text: str) -> Optional[list]:
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.post(f"{OLLAMA_BASE}/api/embed",
                                      json={"model": EMBED_MODEL, "input": text})
                return r.json().get("embeddings", [[]])[0]
        except Exception as e:
            print(f"Embed error: {e}")
            return None

    async def _store_embedding(self, id: str, text: str, memory_type: str,
                                table=None):
        if self._vtable is None: return
        t = table if table is not None else self._vtable
        vector = await self._embed(text)
        if not vector: return
        vector = (vector + [0.0] * 768)[:768]
        try:
            t.add([{"id": id, "text": text, "memory_type": memory_type,
                    "vector": vector}])
        except Exception as e:
            print(f"Vector store error: {e}")

    async def _search_similar(self, query: str, top_k: int = 10,
                               table=None) -> list:
        if self._vtable is None: return []
        t = table if table is not None else self._vtable
        vector = await self._embed(query)
        if not vector: return []
        vector = (vector + [0.0] * 768)[:768]
        try:
            results = t.search(vector).limit(top_k).to_list()
            return [{"id": r["id"], "text": r["text"],
                     "type": r.get("memory_type", "unknown"),
                     "score": float(r.get("_distance", 0.5))}
                    for r in results]
        except Exception as e:
            print(f"Vector search error: {e}")
            return []

    # ── HYBRID RETRIEVAL (GRAPH + VECTORS) ────────────────────────────────

    # Common English stopwords to strip before keyword matching
    _STOPWORDS = {
        "i", "me", "my", "we", "our", "you", "your", "he", "she", "it", "they",
        "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
        "of", "with", "about", "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would", "could",
        "should", "can", "may", "might", "shall", "that", "this", "these", "those",
        "what", "which", "who", "how", "when", "where", "why", "so", "if", "then",
        "just", "also", "like", "get", "got", "want", "need", "think", "know",
        "make", "use", "up", "out", "from", "into", "through", "not", "no", "yes",
        "there", "here", "some", "any", "all", "more", "much", "very", "really",
        "please", "thanks", "ok", "okay", "hi", "hey", "hello", "quil",
    }

    def _extract_keywords_fast(self, text: str) -> list:
        """
        Zero-latency keyword extraction using regex tokenisation + stopword
        filtering. Returns up to 5 normalised slugs that can be matched
        directly against memory_graph_nodes.id (which uses the same format).

        Replaces the previous _llm_json call that added a full LLM round-trip
        (~300–600 ms) to every spreading-activation retrieval.
        """
        # Extract meaningful tokens: letters only, 3–30 chars
        tokens = re.findall(r"\b[a-zA-Z]{3,30}\b", text[:400])
        seen = set()
        keywords = []
        for tok in tokens:
            slug = tok.lower().replace(" ", "_")
            if slug not in self._STOPWORDS and slug not in seen:
                seen.add(slug)
                keywords.append(slug)
            if len(keywords) >= 5:
                break
        return keywords

    async def _spreading_activation_retrieve(self, query: str, context_nodes: list = [],
                                              decay_rate: float = 0.6,
                                              activation_threshold: float = 0.2) -> list:
        """
        2-hop spreading activation over the memory graph.

        Previously loaded the entire edge table into a Python dict on every
        call — O(E) memory and O(E) Python iteration regardless of graph size.
        Now uses targeted SQL queries (WHERE source IN (...)) so only the
        relevant neighbourhood is fetched, keeping this O(k·d) where k is the
        number of seed nodes and d is the average degree.
        """
        # 1. Seed nodes from query keywords
        keywords = self._extract_keywords_fast(query)
        initial_energy: dict[str, float] = {}

        if keywords:
            placeholders = ','.join('?' * len(keywords))
            rows = await _run_sql(
                f"SELECT id FROM memory_graph_nodes "
                f"WHERE id IN ({placeholders}) OR label IN ({placeholders})",
                tuple(keywords) * 2
            )
            for (r,) in (rows or []):
                initial_energy[r] = 1.0

        for nid in context_nodes:
            if nid not in initial_energy:
                initial_energy[nid] = 0.8

        if not initial_energy:
            # No seed nodes → fall through to vector search only
            return []

        final_energy: dict[str, float] = dict(initial_energy)
        current_frontier: dict[str, float] = dict(initial_energy)

        # 2 spreading hops, SQL-bounded per hop
        for _hop in range(2):
            if not current_frontier:
                break
            frontier_ids = list(current_frontier.keys())
            placeholders = ','.join('?' * len(frontier_ids))

            # Fetch only edges originating from or targeting frontier nodes
            edge_rows = await _run_sql(
                f"SELECT source, target, strength FROM memory_graph_edges "
                f"WHERE source IN ({placeholders}) OR target IN ({placeholders})",
                tuple(frontier_ids) * 2
            )

            next_frontier: dict[str, float] = {}
            for src, tgt, strength in (edge_rows or []):
                # Determine which end is in the frontier and which is the neighbour
                pairs = []
                if src in current_frontier:
                    pairs.append((src, tgt))
                if tgt in current_frontier:
                    pairs.append((tgt, src))
                for active_node, neighbour in pairs:
                    energy = current_frontier[active_node]
                    if energy < activation_threshold:
                        continue
                    transfer = energy * float(strength) * decay_rate
                    final_energy[neighbour] = final_energy.get(neighbour, 0.0) + transfer
                    next_frontier[neighbour] = next_frontier.get(neighbour, 0.0) + transfer

            current_frontier = next_frontier

        # 3. Fetch descriptions for the top activated nodes
        sorted_nodes = sorted(final_energy.items(), key=lambda x: x[1], reverse=True)
        top_ids = [n for n, _ in sorted_nodes[:6]]

        graph_memories = []
        if top_ids:
            placeholders = ','.join('?' * len(top_ids))
            results = await _run_sql(
                f"SELECT id, label, description, type FROM memory_graph_nodes "
                f"WHERE id IN ({placeholders})",
                tuple(top_ids)
            )
            for r in (results or []):
                graph_memories.append({
                    "id": r[0],
                    "text": f"[{r[3]}] {r[1]}: {r[2]}",
                    "type": "associative",
                    "score": final_energy.get(r[0], 0.0),
                })

        return graph_memories

    def _score_relevance(self, query: str, candidates: list, max_keep: int = 5) -> list:
        """
        Zero-latency reranker using lexical overlap + existing vector distance.

        Scoring formula:
          final_score = 0.6 * lexical_overlap + 0.4 * (1 - normalised_distance)

        lexical_overlap: fraction of query keywords that appear in the candidate text.
        distance: LanceDB _distance (lower = closer). Graph hits have score=energy
        (higher = more activated), so we treat them differently.

        Replaces the previous LLM JSON call that added ~300–600 ms to every
        retrieval on every chat turn.
        """
        if not candidates:
            return []

        query_words = set(self._extract_keywords_fast(query))

        for c in candidates:
            text_lower = (c.get("text") or "").lower()
            # Lexical overlap: how many query keywords appear in the candidate
            if query_words:
                overlap = sum(1 for w in query_words if w in text_lower) / len(query_words)
            else:
                overlap = 0.0

            # Normalise the raw score field:
            # - Vector hits: _distance (0 = identical, ~1 = unrelated) → invert
            # - Graph hits: activation energy (higher = more relevant) → clamp 0–1
            raw = float(c.get("score", 0.5))
            if c.get("type") == "associative":
                dist_component = min(1.0, raw)        # energy, already 0–1 range
            else:
                dist_component = max(0.0, 1.0 - raw)  # invert distance

            c["score"] = round(0.6 * overlap + 0.4 * dist_component, 4)

        candidates.sort(key=lambda x: x["score"], reverse=True)
        return candidates[:max_keep]

    # ── FILE INGESTION (SECTION-AWARE, BATCHED) ───────────────────────────

    def _split_sections(self, content: str, max_chunk: int = 900) -> list:
        """
        Section-aware chunker. Splits on markdown headings first,
        then on blank-line paragraph breaks, then falls back to
        sliding window for any oversized sections.
        """
        # Try to split on heading lines (# / ## / ###)
        sections = re.split(r'(?m)^(#{1,3}\s.+)$', content)
        chunks = []
        current = ""
        current_header = ""

        for part in sections:
            if re.match(r'^#{1,3}\s', part):
                current_header = part.strip()
                continue
            block = (f"{current_header}\n{part}" if current_header else part).strip()
            if not block:
                continue

            # If block fits, keep as one chunk
            if len(block) <= max_chunk:
                if block:
                    chunks.append(block)
            else:
                # Split oversized blocks by blank lines (paragraphs)
                paragraphs = re.split(r'\n{2,}', block)
                acc = ""
                for para in paragraphs:
                    para = para.strip()
                    if not para:
                        continue
                    if len(acc) + len(para) + 2 <= max_chunk:
                        acc = (acc + "\n\n" + para).strip()
                    else:
                        if acc:
                            chunks.append(acc)
                        # Para itself too big? sliding window
                        if len(para) > max_chunk:
                            overlap = 80
                            s = 0
                            while s < len(para):
                                chunks.append(para[s:s + max_chunk])
                                s += max_chunk - overlap
                            acc = ""
                        else:
                            acc = para
                if acc:
                    chunks.append(acc)

        return [c for c in chunks if len(c.strip()) >= 40]

    async def ingest_file(self, filename: str, content: str,
                          progress_cb=None) -> int:
        print(f"📖 Ingesting {filename} ({len(content):,} chars)...")
        if not self._ldb:
            return 0

        raw_chunks = self._split_sections(content)
        print(f"   → {len(raw_chunks)} sections identified")

        stored = 0
        batch = []

        for i, chunk_text in enumerate(raw_chunks):
            chunk_id = f"doc_{filename}_{uuid.uuid4().hex[:6]}"
            try:
                vec = await self._embed(chunk_text)
                if vec:
                    vec = (vec + [0.0] * 768)[:768]
                    batch.append({
                        "id": chunk_id,
                        "text": f"[{filename}] {chunk_text}",
                        "memory_type": "document",
                        "vector": vec,
                    })
            except Exception as e:
                print(f"   Skipped chunk {i}: {e}")

            if len(batch) >= 10:
                try:
                    self._vtable.add(batch)
                    stored += len(batch)
                    if progress_cb:
                        await progress_cb(stored, len(raw_chunks))
                    batch = []
                    await asyncio.sleep(0.05)
                except Exception as e:
                    print(f"   Batch write failed: {e}")
                    batch = []

        if batch:
            try:
                self._vtable.add(batch)
                stored += len(batch)
            except Exception as e:
                print(f"   Final batch write failed: {e}")

        # After ingestion, extract high-level summary facts from the document
        asyncio.create_task(self._extract_doc_facts(filename, content[:4000]))

        print(f"✅ Finished {filename}: {stored}/{len(raw_chunks)} chunks stored.")
        return stored

    async def _extract_doc_facts(self, filename: str, preview: str):
        """Extract key facts from the document and store in semantic memory.
        Also creates a graph node for the document so it appears in the memory map."""
        data = await self._llm_json(
            f"Extract 3-7 key FACTUAL details from this document. Only include facts explicitly stated in the text.\n"
            f"DO NOT interpret, add metaphors, or infer meaning beyond what is written.\n"
            f"DO NOT use creative or poetic language. Be literal and precise.\n"
            f"Each fact should be a concrete, specific detail from the document.\n\n"
            f"Document: {filename}\n"
            f"Content:\n{preview}\n\n"
            f"Return JSON: {{\"facts\": [{{\"label\": \"Topic or heading\", \"value\": \"Specific fact from the document\"}}], "
            f"\"summary\": \"One sentence summary of the document\"}}\n"
            f"If the content is too short or unclear, return: {{\"facts\": [], \"summary\": \"\"}}",
            model=THINK_MODEL, timeout=40
        )
        facts = data.get("facts", [])
        if not facts:
            return
        import hashlib
        now = datetime.now(timezone.utc).isoformat()
        inserts = [
            # Deterministic ID: "doc_" + hash of filename+label so re-ingesting
            # the same document updates existing facts rather than duplicating them.
            ("doc_" + hashlib.md5(f"{filename}:{f.get('label','')}".lower().encode()).hexdigest()[:12],
             "document", f.get("label", "Fact"), f.get("value", ""), 0.75, "[]", now)
            for f in facts if f.get("label") and f.get("value")
        ]
        if inserts:
            await _run_many(
                "INSERT INTO semantic (id, category, label, value, confidence, source_episode_ids, last_updated) "
                "VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "  value=excluded.value, "
                "  confidence=MIN(0.99, confidence+0.05), "
                "  last_updated=excluded.last_updated",
                inserts
            )

        # Create a graph node for the document so it shows up in the memory map
        doc_summary = data.get("summary", f"Document: {filename}")
        now_ts = datetime.now(timezone.utc).isoformat()
        doc_node_id = f"doc_{filename.lower().replace(' ', '_').replace('.', '_')}"
        await _run_sql(
            "INSERT INTO memory_graph_nodes "
            "  (id, label, type, description, weight, last_activated) "
            "VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "  weight=weight+0.5, "
            "  description=excluded.description, "
            "  last_activated=excluded.last_activated",
            (doc_node_id, filename, "document", doc_summary[:200], 2.0, now_ts), commit=True
        )

    # ── AUTONOMY (PROACTIVE ALERTS) ───────────────────────────────────────

    async def get_pending_alerts(self) -> list:
        """
        Surfaces prospective items that are due based on their urgency tier.
        Max 2 alerts per call to avoid flooding the UI.

        Urgency tiers:
          urgent → surface immediately (any unsurfaced item)
          soon   → surface after 24 h from creation
          later  → surface after 72 h from creation
        """
        now = datetime.now(timezone.utc)
        rows = await _run_sql(
            "SELECT id, content, urgency, due_hint, created_at "
            "FROM prospective WHERE done=0 AND surfaced=0 "
            "ORDER BY CASE urgency WHEN 'urgent' THEN 1 WHEN 'soon' THEN 2 ELSE 3 END, created_at"
        )

        alerts = []
        for item_id, content, urgency, due_hint, created_at in (rows or []):
            if len(alerts) >= 2:
                break

            # Determine minimum age before surfacing
            min_age_hours = {"urgent": 0, "soon": 24, "later": 72}.get(urgency, 24)

            try:
                created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                age_hours = (now - created).total_seconds() / 3600
            except Exception:
                age_hours = min_age_hours  # treat as eligible on parse error

            if age_hours < min_age_hours:
                continue

            alerts.append({
                "type": "reminder",
                "content": f"Hey, just a reminder: {content}",
                "id": item_id,
                "urgency": urgency,
            })
            await _run_sql(
                "UPDATE prospective SET surfaced=1 WHERE id=?",
                (item_id,), commit=True
            )

        return alerts

    # ── WORKING MEMORY & CONTEXT ──────────────────────────────────────────

    def _estimate_tokens(self, messages: list) -> int:
        """Fast approximation: 1 token ≈ 4 chars. No external deps."""
        return sum(len(m.get("content", "")) for m in messages) // 4

    async def get_working_memory(self, session_id: str, max_turns: int = 20) -> list:
        """
        MemGPT-style paged retrieval. Fetches up to max_turns pairs then
        evicts the oldest turns until the estimated token count sits under
        CONTEXT_PAGE_THRESHOLD * MAX_CONTEXT_TOKENS. Prevents 16GB M4 from
        swapping when a long session fills the 6k context window.
        """
        rows = await _run_sql(
            "SELECT role, content FROM working_memory "
            "WHERE session_id = ? ORDER BY id DESC LIMIT ?",
            (session_id, max_turns * 2)
        )
        messages = [{"role": r[0], "content": r[1]} for r in reversed(rows or [])]

        budget = int(MAX_CONTEXT_TOKENS * CONTEXT_PAGE_THRESHOLD)
        # Evict oldest user+assistant pairs until we're within budget
        while self._estimate_tokens(messages) > budget and len(messages) >= 2:
            messages = messages[2:]   # drop the oldest user→assistant pair

        return messages

    async def _save_working_memory(self, session_id: str, role: str, content: str):
        await _run_sql(
            "INSERT INTO working_memory (session_id, role, content, timestamp) VALUES (?,?,?,?)",
            (session_id, role, content, datetime.now(timezone.utc).isoformat()),
            commit=True
        )

    # ── LAZY RAG — HEURISTIC GATE ─────────────────────────────────────────

    def _needs_vector_search(self, query: str) -> bool:
        """
        Zero-latency heuristic gate that decides whether to run the full
        LanceDB vector search. Replaces the previous 4×serial Ollama calls
        that added ~1–1.5 s of TTFT overhead on every turn.

        Triggers vector search when the query looks knowledge-seeking:
          - Direct questions (who/what/when/where/why/how)
          - Contains an unknown proper noun (capitalized mid-sentence)
          - Long message (user is explaining something → doc retrieval helps)
          - Explicit search / recall requests ("remember", "look up", etc.)
        """
        q = query.strip()
        q_lower = q.lower()

        # Explicit recall signals
        RECALL_WORDS = {"remember", "recall", "find", "look up", "search",
                        "what did", "when did", "where did", "who is",
                        "tell me about", "do you know", "have i", "show me",
                        "document", "file", "uploaded", "from the", "in the",
                        "according to", "based on", "what does", "summarize"}
        if any(q_lower.startswith(w) or f" {w} " in q_lower for w in RECALL_WORDS):
            return True

        # Question words at the start
        QUESTION_STARTERS = ("who ", "what ", "when ", "where ", "why ", "how ",
                              "which ", "is ", "are ", "was ", "were ", "can ",
                              "could ", "should ", "would ", "do ", "does ", "did ")
        if q_lower.startswith(QUESTION_STARTERS) or q.endswith("?"):
            return True

        # Long message — user is giving context, likely worth matching against docs
        if len(q) > 200:
            return True

        # Unknown proper noun heuristic: capitalised word not at sentence start
        words = q.split()
        if len(words) > 3:
            for w in words[1:]:
                cleaned = w.strip(",.?!\"'")
                if cleaned and cleaned[0].isupper() and cleaned.lower() not in q_lower[:10]:
                    return True

        return False

    async def _compute_response_entropy_post(self, query: str) -> float:
        """
        Kept for EAFT gate (post-response use only).
        4 short samples — never called inside the chat hot path.
        """
        samples = []
        async with httpx.AsyncClient(timeout=20) as client:
            for _ in range(4):
                try:
                    r = await client.post(f"{OLLAMA_BASE}/api/generate", json={
                        "model": CHAT_MODEL,
                        "prompt": query[:200],
                        "stream": False,
                        "options": {
                            **OLLAMA_OPTIONS,
                            "num_predict": 15,
                            "temperature": 1.0,
                        },
                    })
                    samples.append(r.json().get("eval_count", 8))
                except Exception:
                    samples.append(8)

        if len(samples) < 2:
            return 0.0
        mean = sum(samples) / len(samples)
        variance = sum((x - mean) ** 2 for x in samples) / len(samples)
        return math.log1p(variance)

    async def build_context(self, query: str, session_id: str,
                             force_rag: bool = False) -> dict:
        """
        Lazy RAG: graph spreading-activation always runs (cheap, ~5–20 ms).
        Full LanceDB vector search only fires when the heuristic gate says
        the query is knowledge-seeking, OR force_rag=True (Workshop tasks).
        No LLM calls in this path — TTFT impact is now near-zero.
        """
        global _last_retrieval_ids

        # Step 1: Cheap graph retrieval — always runs
        graph_hits = await self._spreading_activation_retrieve(
            query, context_nodes=_last_retrieval_ids
        )

        # Step 2: Heuristic gate — zero-latency, no LLM calls
        if force_rag or self._needs_vector_search(query):
            vector_hits = await self._search_similar(query, top_k=6)
            # Merge, dedup
            seen = {h["id"] for h in graph_hits}
            for v in vector_hits:
                if v["id"] not in seen:
                    graph_hits.append(v)
                    seen.add(v["id"])
        else:
            print("⚡ L-RAG: heuristic gate — skipping vector search")

        relevant = self._score_relevance(query, graph_hits, max_keep=6)
        _last_retrieval_ids = [r["id"] for r in relevant]

        ep_rows = await _run_sql(
            "SELECT summary, timestamp FROM episodic ORDER BY timestamp DESC LIMIT 3"
        )
        recent_episodes = [{"summary": r[0], "timestamp": r[1]} for r in (ep_rows or [])]

        fact_rows = await _run_sql(
            "SELECT label, value FROM semantic ORDER BY confidence DESC LIMIT 5"
        )
        facts = [{"label": r[0], "value": r[1]} for r in (fact_rows or [])]

        lessons = await self._get_reflection_lessons(query)

        # Contextual patterns: high-confidence, repeatedly observed situational rules
        ctx_rows = await _run_sql(
            "SELECT pattern FROM contextual WHERE confidence >= 0.6 AND observation_count >= 2 "
            "ORDER BY confidence DESC LIMIT 3"
        )
        contextual = [r[0] for r in (ctx_rows or []) if r[0]]

        return {
            "relevant": relevant,
            "recent_episodes": recent_episodes,
            "facts": facts,
            "lessons": lessons,
            "contextual": contextual,
        }

    def get_composed_prompt(self, context: dict) -> str:
        conn = _get_conn()
        c = conn.cursor()
        c.execute("SELECT id, content FROM prompt_layers")
        layers = {r[0]: r[1] for r in c.fetchall()}

        parts = [layers.get("core", ""), layers.get("learned", ""), layers.get("user", "")]

        # Output formatting instructions — only apply structure when content warrants it
        parts.append(
            "FORMATTING RULES (apply with judgment, not mechanically):\n"
            "- For greetings, simple questions, or short exchanges: respond in plain prose, 1-3 sentences. No headers, no bullets.\n"
            "- For multi-part or technical answers: use **bold** for key terms, bullet points for lists, ## headings for sections.\n"
            "- NEVER show your reasoning process, planning steps, or internal thinking in your response.\n"
            "- NEVER start with 'We are given...', 'Let me think...', 'The user said...', or any meta-commentary.\n"
            "- Respond directly as Quil — lead with the answer, nothing else."
        )

        lessons = context.get("lessons", [])
        if lessons:
            parts.append("Past lessons (avoid repeating these mistakes):\n" +
                         "\n".join(f"- {l}" for l in lessons))

        if context.get("contextual"):
            parts.append("**Situational Context (observed behavioral patterns):**\n" +
                         "\n".join(f"- {p}" for p in context["contextual"]))

        if context["facts"]:
            parts.append("**Known Facts About the User:**\n" + "\n".join(f"- {f['label']}: {f['value']}" for f in context["facts"]))
        if context["relevant"]:
            # Separate document context from other memories for clarity
            doc_ctx = [r for r in context["relevant"] if "document" in r.get("type", "").lower() or r.get("text", "").startswith("[")]
            mem_ctx = [r for r in context["relevant"] if r not in doc_ctx]
            if doc_ctx:
                parts.append("**Relevant Document Content (from uploaded files — use this to answer questions about documents):**\n" +
                             "\n".join(f"- {r['text']}" for r in doc_ctx))
            if mem_ctx:
                parts.append("**Relevant Memories:**\n" + "\n".join(f"- {r['text']}" for r in mem_ctx))

        return "\n\n".join(p for p in parts if p.strip())


    # ── EPISODE SAVING (Background Tasks) ─────────────────────────────────

    async def save_episode(self, session_id: str, user_message: str, assistant_response: str):
        """
        Event-boundary ingestion. Working memory is written immediately (no LLM).
        All enrichment (facts, graph, emotion, tasks, reflection, contextual)
        is handled by a SINGLE background LLM call in _batch_ingest_episode —
        replacing the previous 6 separate LLM calls that were thrashing the GPU.
        """
        await self._save_working_memory(session_id, "user", user_message)
        await self._save_working_memory(session_id, "assistant", assistant_response)

        episode_id = str(uuid.uuid4())
        # Fire single batch task — returns immediately, runs in background
        asyncio.create_task(
            self._batch_ingest_episode(episode_id, session_id, user_message, assistant_response)
        )

    async def _batch_ingest_episode(self, episode_id: str, session_id: str,
                                     user_message: str, assistant_response: str):
        """
        Single LLM call replacing the previous 6 individual _llm_json calls.
        Extracts everything needed for all memory tables in one round-trip,
        then dispatches cheap SQL writes in parallel.
        """
        u_short = user_message[:350].rsplit(" ", 1)[0] if len(user_message) > 350 else user_message
        a_short = assistant_response[:350].rsplit(" ", 1)[0] if len(assistant_response) > 350 else assistant_response

        data = await self._llm_json(
            f"You are a memory extractor for a personal AI assistant. Read this conversation and fill the JSON fields below.\n\n"
            f"=== CONVERSATION ===\n"
            f"User: {u_short}\n"
            f"Assistant: {a_short}\n"
            f"===================\n\n"
            f"RULES:\n"
            f"1. summary: ONE sentence (max 15 words) describing what was discussed.\n"
            f"2. tags: 1-3 topic words from the conversation. Return [] if nothing specific.\n"
            f"3. sentiment: user's tone — one of: positive, negative, neutral, excited, stressed. Default 'neutral'.\n"
            f"4. facts: Personal facts about the USER — name, job, preferences, tools they use, things they like/dislike, "
            f"   skills, goals, opinions they expressed. Include things the user mentioned about themselves even casually "
            f"   (e.g. 'I use Python', 'I'm building a startup', 'I prefer dark mode'). "
            f"   Each fact: {{\"label\": \"short label\", \"value\": \"what they said\"}}. Return [] if nothing personal was shared.\n"
            f"5. tasks: ONLY if the user EXPLICITLY asked to be reminded or stated a to-do ('remind me', 'I need to', 'don't forget'). "
            f"   Return [] for normal conversations.\n"
            f"6. emotional_score: -1.0 to 1.0. Default 0.0. Use 0.4+ for positive/excited messages, -0.4 or lower for stressed/negative.\n"
            f"7. dominant_emotion: positive, negative, neutral, curious, excited, stressed, anxious, or sad. Default 'neutral'. "
            f"   Be accurate — if the user seems enthusiastic, say 'excited'; if asking lots of questions, say 'curious'.\n"
            f"8. quality_score: 0.0-1.0. Default 0.80. Lower if the assistant was vague, unhelpful, missed the point, or gave incomplete info.\n"
            f"9. quality_issue: describe the issue if score < 0.85, otherwise null.\n"
            f"10. quality_lesson: what Quil should do differently if score < 0.85, otherwise null.\n"
            f"11. procedural_rule: If this conversation reveals a clear behavioral rule Quil should always follow "
            f"    (e.g. 'Always ask for clarification before writing code', 'User prefers bullet lists over prose for comparisons'), "
            f"    write it as an imperative sentence. Return null if no clear rule emerges.\n\n"
            f"Return ONLY valid JSON:\n"
            f"{{\n"
            f"  \"summary\": \"...\",\n"
            f"  \"tags\": [],\n"
            f"  \"sentiment\": \"neutral\",\n"
            f"  \"facts\": [],\n"
            f"  \"tasks\": [],\n"
            f"  \"emotional_score\": 0.0,\n"
            f"  \"dominant_emotion\": \"neutral\",\n"
            f"  \"quality_score\": 0.80,\n"
            f"  \"quality_issue\": null,\n"
            f"  \"quality_lesson\": null,\n"
            f"  \"procedural_rule\": null\n"
            f"}}",
            model=CHAT_MODEL, timeout=35
        )

        # ── Extract & validate fields ──
        summary = data.get("summary", "").strip()
        if not summary or len(summary) < 10:
            preview = user_message[:80].strip()
            summary = f"User: {preview}…" if len(user_message) > 80 else f"User: {user_message.strip()}"

        tags = [t for t in data.get("tags", []) if isinstance(t, str) and t.strip()]
        sentiment = data.get("sentiment", "neutral")
        if sentiment not in ("positive", "negative", "neutral", "excited", "stressed"):
            sentiment = "neutral"

        now = datetime.now(timezone.utc).isoformat()

        # ── Write episodic row ──
        await _run_sql(
            "INSERT INTO episodic VALUES (?,?,?,?,?,?,?,?,?)",
            (episode_id, session_id, now, user_message, assistant_response,
             summary, json.dumps(tags), sentiment, 0.5),
            commit=True
        )

        # ── Dispatch cheap SQL writes concurrently (no more LLM calls) ──
        write_tasks = [
            self._write_facts_from_batch(data.get("facts", []), now),
            self._update_graph_triplets(tags, summary),
            self._write_emotional_from_batch(data, now),
            self._write_tasks_from_batch(data.get("tasks", []), episode_id, now),
            self._write_reflection_from_batch(data, user_message),
            self._extract_contextual(user_message, assistant_response),
            self._write_procedural_from_batch(data.get("procedural_rule"), now),
        ]
        await asyncio.gather(*write_tasks, return_exceptions=True)

        # ── EAFT gate: queue training pair if entropy was high ──
        asyncio.create_task(self._eaft_gate(episode_id, user_message, assistant_response))

    async def _write_facts_from_batch(self, facts: list, now: str):
        """
        Upsert semantic facts using a deterministic ID derived from the
        normalised label. This means 'Prefers Python' learned on day 1 and
        re-confirmed on day 30 updates the same row (raising confidence)
        instead of creating duplicate entries with random UUIDs.

        Strict guards: rejects vague, too-short, hallucinated, or formulaic values
        that the model invents when no real fact was stated.
        """
        import hashlib

        # Phrases that indicate the model invented a fact rather than extracting one
        _HALLUCINATION_SIGNALS = [
            "lantern", "metaphor", "like a", "reminds me of", "seems to",
            "appears to", "might be", "could be", "probably", "perhaps",
            "it seems", "it appears", "i think", "i believe", "unclear",
            "unknown", "not specified", "not mentioned", "no information",
            "user did not", "was not stated", "example", "placeholder",
        ]

        inserts = []
        for f in facts:
            label = f.get("label", "").strip()
            value = f.get("value", "").strip()

            # Reject missing, too-short, or numeric-only values
            if not label or not value:
                continue
            if len(label) < 3 or len(value) < 5:
                continue
            # Reject values that are suspiciously generic or templated
            if value.lower() in ("...", "value", "unknown", "n/a", "none", "null", "true", "false"):
                continue
            # Reject hallucination signals
            val_lower = value.lower()
            if any(sig in val_lower for sig in _HALLUCINATION_SIGNALS):
                continue
            # Reject if the value is just a copy of the label
            if label.lower().strip() == val_lower:
                continue

            # Deterministic ID: hash of normalised label so upsert deduplicates
            fact_id = "fact_" + hashlib.md5(label.lower().strip().encode()).hexdigest()[:12]
            inserts.append((fact_id, "fact", label, value, 0.8, "[]", now))
        if inserts:
            await _run_many(
                "INSERT INTO semantic (id, category, label, value, confidence, source_episode_ids, last_updated) "
                "VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "  value=excluded.value, "
                "  confidence=MIN(0.99, confidence+0.05), "
                "  last_updated=excluded.last_updated",
                inserts
            )

    async def _write_emotional_from_batch(self, data: dict, now: str):
        score = float(data.get("emotional_score", 0.0))
        emotion = data.get("dominant_emotion", "neutral")
        today = datetime.now(timezone.utc).date().isoformat()
        day_label = datetime.now(timezone.utc).strftime("%a")
        existing = await _run_sql(
            "SELECT id, score FROM emotional WHERE date=?", (today,), fetchall=False
        )
        if existing:
            blended = round((existing[1] + score) / 2, 3)
            await _run_sql(
                "UPDATE emotional SET score=?, dominant_emotion=? WHERE date=?",
                (blended, emotion, today), commit=True
            )
        else:
            await _run_sql(
                "INSERT INTO emotional VALUES (?,?,?,?,?)",
                (str(uuid.uuid4()), today, day_label, round(score, 3), emotion),
                commit=True
            )

    async def _write_tasks_from_batch(self, tasks: list, episode_id: str, now: str):
        # Very strict: most conversations have ZERO tasks. Only store when
        # the user clearly and explicitly stated an intention or reminder.
        _TASK_REJECT_SIGNALS = [
            "...", "task", "to do", "follow up", "none", "n/a", "todo",
            "example", "placeholder", "something", "anything", "stuff",
            "things", "general", "various", "check", "look into",
        ]
        inserts = []
        for t in tasks[:2]:  # max 2 per episode — model over-generates
            content = t if isinstance(t, str) else t.get("content", "")
            content = content.strip()
            if not content or len(content) < 15:
                continue
            content_lower = content.lower()
            # Reject generic/templated task content
            if content_lower in _TASK_REJECT_SIGNALS:
                continue
            if any(sig == content_lower for sig in _TASK_REJECT_SIGNALS):
                continue
            # Reject tasks that are just descriptions of what was discussed
            if content_lower.startswith(("the user", "user asked", "discussed", "talked about")):
                continue
            # Reject duplicates (check existing prospective)
            existing = await _run_sql(
                "SELECT COUNT(*) FROM prospective WHERE content=? AND done=0",
                (content,), fetchall=False
            )
            if existing and existing[0] > 0:
                continue
            urgency = "later"
            if isinstance(t, dict):
                urgency = t.get("urgency", "later")
            if urgency not in ("urgent", "soon", "later"):
                urgency = "later"
            due_hint = t.get("due_hint", "") if isinstance(t, dict) else ""
            inserts.append((
                str(uuid.uuid4()), content, urgency,
                due_hint, 0, now, episode_id, 0
            ))
        if inserts:
            await _run_many("INSERT INTO prospective VALUES (?,?,?,?,?,?,?,?)", inserts)

    async def _write_reflection_from_batch(self, data: dict, user_message: str):
        quality = float(data.get("quality_score", 1.0))
        issue = data.get("quality_issue")
        lesson = data.get("quality_lesson")
        # Log lessons whenever quality < 0.85 AND an issue/lesson was identified.
        # This catches more imperfect responses, feeding the procedural pipeline.
        if quality < 0.85 and issue and lesson and len(lesson) > 10:
            await _run_sql(
                "INSERT INTO reflection_bank VALUES (?,?,?,?,?)",
                (str(uuid.uuid4()), user_message[:120], issue, lesson,
                 datetime.now(timezone.utc).isoformat()),
                commit=True
            )

    async def _write_procedural_from_batch(self, rule: Optional[str], now: str):
        """
        Directly store a procedural rule extracted by the batch ingest LLM.
        This seeds the procedural table immediately rather than waiting for
        the dream promotion cycle (which requires reflection_bank accumulation).
        """
        if not rule or not isinstance(rule, str):
            return
        rule = rule.strip()
        if len(rule) < 15 or len(rule) > 200:
            return
        # Reject vague or templated rules
        _REJECT = ["null", "n/a", "none", "example", "placeholder", "to do", "task"]
        if rule.lower() in _REJECT or rule.lower().startswith("user "):
            return
        # Check for near-duplicate (same first 40 chars)
        existing = await _run_sql(
            "SELECT COUNT(*) FROM procedural WHERE rule=?",
            (rule,), fetchall=False
        )
        if existing and existing[0] > 0:
            # Bump evidence count instead of inserting duplicate
            await _run_sql(
                "UPDATE procedural SET evidence_count=evidence_count+1 WHERE rule=?",
                (rule,), commit=True
            )
            return
        await _run_sql(
            "INSERT INTO procedural VALUES (?,?,?,?,?)",
            (str(uuid.uuid4()), rule,
             "Extracted directly from conversation context", 1, now),
            commit=True
        )

    # ── KEEP legacy methods for ingest/dream paths that still call them directly ──

    async def _extract_semantic_facts(self, episode_id: str, text: str):
        """Legacy: called only from _extract_doc_facts. Batch ingest uses _write_facts_from_batch."""
        data = await self._llm_json(
            f"Extract ONLY concrete, verifiable facts explicitly stated in the text.\n"
            f"No inference, no metaphors. Direct quotes or paraphrases only.\n\n"
            f"Text: {text[:400]}\n\n"
            f"Return JSON: {{\"facts\": [{{\"label\": \"Name\", \"value\": \"John\"}}]}}\n"
            f"If no clear facts, return: {{\"facts\": []}}",
            model=THINK_MODEL, timeout=30
        )
        facts = data.get("facts", [])
        if not facts:
            return
        now = datetime.now(timezone.utc).isoformat()
        await self._write_facts_from_batch(facts, now)

    async def _update_graph_triplets(self, tags: list, summary: str):
        """
        Upsert graph nodes for each tag extracted from an episode, then
        create/strengthen edges between co-occurring tag pairs.

        Edge quality gate: edges are only created after a tag pair has been
        seen together at least twice (co_count >= 2 in the node weight proxy).
        This prevents a single noisy episode from wiring unrelated concepts
        together permanently — the main cause of the 'everything connects to
        everything' graph degradation.

        Node description stores the full summary sentence so the spreading-
        activation results actually mean something when surfaced in context.
        """
        now = datetime.now(timezone.utc).isoformat()
        # Upsert nodes first so weight is current before edge decision
        for tag in tags:
            nid = tag.lower().replace(" ", "_")
            await _run_sql(
                "INSERT INTO memory_graph_nodes "
                "  (id, label, type, description, weight, last_activated) "
                "VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "  weight=weight+0.2, "
                "  description=excluded.description, "
                "  last_activated=excluded.last_activated",
                (nid, tag, "concept", summary, 1.0, now), commit=True
            )

        # Only wire edges between pairs that have both been seen before
        # (weight > 1.0 means the node existed prior to this episode).
        # We check weight after the upsert: a brand-new node starts at 1.0;
        # on its second appearance it will be 1.2+, so threshold is > 1.0.
        for i, t1 in enumerate(tags):
            for t2 in tags[i+1:]:
                n1 = t1.lower().replace(" ", "_")
                n2 = t2.lower().replace(" ", "_")
                # Both nodes must have weight > 1.0 (seen at least once before)
                row1 = await _run_sql(
                    "SELECT weight FROM memory_graph_nodes WHERE id=?",
                    (n1,), fetchall=False
                )
                row2 = await _run_sql(
                    "SELECT weight FROM memory_graph_nodes WHERE id=?",
                    (n2,), fetchall=False
                )
                if not row1 or not row2:
                    continue
                if row1[0] <= 1.0 or row2[0] <= 1.0:
                    # One or both are brand-new this episode — skip edge creation
                    continue
                await _run_sql(
                    "INSERT INTO memory_graph_edges "
                    "  (source, target, strength, activation_count, last_activated) "
                    "VALUES (?,?,?,?,?) "
                    "ON CONFLICT(source, target) DO UPDATE SET "
                    "  strength=MIN(1.0, strength+0.1), "
                    "  activation_count=activation_count+1, "
                    "  last_activated=excluded.last_activated",
                    (n1, n2, 0.5, 1, now), commit=True
                )

    # ── LEGACY STUBS (replaced by _batch_ingest_episode — kept for safety) ──

    async def _score_emotional(self, *args, **kwargs):
        """Deprecated: emotional scoring is now done in _write_emotional_from_batch."""
        pass

    async def _extract_prospective(self, *args, **kwargs):
        """Deprecated: task extraction is now done in _write_tasks_from_batch."""
        pass

    async def _maybe_store_reflection(self, *args, **kwargs):
        """Deprecated: reflection is now done in _write_reflection_from_batch."""
        pass

    async def _get_reflection_lessons(self, query: str, top_k: int = 3) -> list:
        """Retrieve the most relevant past lessons for the current query."""
        rows = await _run_sql(
            "SELECT trigger_summary, lesson FROM reflection_bank ORDER BY created_at DESC LIMIT 20"
        )
        if not rows:
            return []
        query_lower = query.lower()
        scored = []
        for trigger, lesson in rows:
            overlap = sum(1 for w in query_lower.split() if w in (trigger or "").lower())
            scored.append((overlap, lesson))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [lesson for _, lesson in scored[:top_k] if lesson]

    # ── EMOTIONAL CALIBRATION ─────────────────────────────────────────────

    async def get_emotional_inference_params(self) -> dict:
        """
        Reads the last 3 emotional records with recency weighting (3-2-1)
        and returns calibrated Ollama options for temperature, repeat_penalty,
        and top_p. Called before every streaming chat inference call.
        """
        rows = await _run_sql(
            "SELECT score, dominant_emotion FROM emotional ORDER BY date DESC LIMIT 3"
        )
        if not rows:
            return {"temperature": 0.70, "repeat_penalty": 1.10, "top_p": 0.95}

        weights = [3, 2, 1]
        total_w = sum(weights[:len(rows)])
        blended_score = sum(r[0] * w for r, w in zip(rows, weights)) / total_w
        dominant = rows[0][1]  # Most recent emotion drives the profile

        base_t, base_rp, base_tp = EMOTION_PARAMS.get(dominant, EMOTION_PARAMS["neutral"])
        # Score nudges temperature slightly: very negative score → clamp lower
        score_delta = (blended_score - 0.5) * 0.15
        final_temp = round(max(0.15, min(1.0, base_t + score_delta)), 3)

        print(f"🎭 Emotional calibration: {dominant} score={blended_score:.2f} → temp={final_temp}")
        return {
            "temperature": final_temp,
            "repeat_penalty": base_rp,
            "top_p": base_tp,
        }

    # ── EAFT: ENTROPY-ADAPTIVE FINE-TUNING GATE ───────────────────────────

    async def _eaft_gate(self, episode_id: str, user_message: str,
                          assistant_response: str):
        """
        EAFT checkpoint. Runs after every episode (post-response, never in hot path).
        If the model's entropy on the user query exceeds the threshold, the exchange
        is queued as a training pair for the background TTT worker (ttt_worker.py).
        """
        EAFT_THRESHOLD = 2.1
        try:
            entropy = await self._compute_response_entropy_post(user_message)
            if entropy > EAFT_THRESHOLD:
                await self._queue_ttt_job(user_message, assistant_response)
                print(f"🧠 EAFT gate triggered (entropy={entropy:.3f}) — training pair queued.")
        except Exception as e:
            print(f"EAFT gate error: {e}")

    async def _queue_ttt_job(self, prompt: str, completion: str):
        """Appends a training pair to the JSONL queue monitored by ttt_worker.py."""
        ttt_path = Path.home() / ".quil" / "ttt_queue.jsonl"
        entry = {
            "prompt": prompt,
            "completion": completion,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        loop = asyncio.get_running_loop()
        def _write():
            with open(ttt_path, "a") as f:
                f.write(json.dumps(entry) + "\n")
        await loop.run_in_executor(None, _write)

    async def _extract_contextual(self, user_message: str, assistant_response: str):
        """Detect situational patterns (time of day, message type, behavior) and upsert into contextual table."""
        hour = datetime.now(timezone.utc).hour
        msg_len = len(user_message)
        msg_lower = user_message.lower()
        has_code = "```" in user_message or "def " in user_message or "function " in user_message or "class " in user_message
        has_question = user_message.strip().endswith("?")
        is_short = msg_len < 40
        has_link = "http://" in user_message or "https://" in user_message

        # Build context signals
        signals = []

        if hour >= 22 or hour < 5:
            signals.append(("Late night", "After 10pm — usually reflective or wind-down mode", "🌙"))
        elif hour < 10:
            signals.append(("Morning session", "Before 10am — task-oriented, planning mode", "☀️"))
        elif hour >= 17:
            signals.append(("Evening session", "After 5pm — tends to be more relaxed or exploratory", "🌆"))
        if has_code:
            signals.append(("Code shared", "User pasted code — likely wants debugging or review help", "⌨️"))
        if msg_len > 400:
            signals.append(("Long message", "User wrote a lot — likely processing or explaining context", "💭"))
        if is_short and has_question:
            signals.append(("Quick question", "Short direct question — wants a fast, concise answer", "⚡"))
        if has_link:
            signals.append(("Shared a link", "User shared a URL — may want analysis or summary", "🔗"))
        if any(w in msg_lower for w in ["help me", "how do i", "can you show", "explain"]):
            signals.append(("Learning mode", "User is asking for help or explanations", "📚"))
        if any(w in msg_lower for w in ["what do you think", "opinion", "should i", "would you"]):
            signals.append(("Seeking advice", "User wants perspective or recommendations", "🤔"))

        if not signals:
            return

        for trigger, base_pattern, icon in signals:
            existing = await _run_sql(
                "SELECT id, observation_count FROM contextual WHERE trigger_desc=?",
                (trigger,), fetchall=False
            )
            if existing:
                await _run_sql(
                    "UPDATE contextual SET observation_count=observation_count+1, "
                    "confidence=MIN(0.95, confidence+0.03) WHERE id=?",
                    (existing[0],), commit=True
                )
            else:
                await _run_sql(
                    "INSERT INTO contextual VALUES (?,?,?,?,?,?)",
                    (str(uuid.uuid4()), trigger, base_pattern,
                     icon, 0.5, 1),
                    commit=True
                )

    # ── DREAM LOOP ────────────────────────────────────────────────────────

    async def dream_loop(self):
        print("🪶  Quil dream loop started.")
        while True:
            await asyncio.sleep(600)  # Every 10 min (was 30 min) — populate dreams faster
            try:
                await self._dream_prune_graph()
                await self._dream_generate_insight()
                await self._dream_promote_patterns()
                await self._dream_evolve_prompt()
                await self._dream_prune_working_memory()
            except Exception as e:
                print(f"Dream error: {e}")

    async def _dream_prune_graph(self):
        """
        Biological forgetting via chronological edge decay.
        Edges are decayed based on how long ago they were last activated:
          - < 7 days:  no decay (recently used, keep strong)
          - 7-30 days: decay by 10% per dream cycle
          - > 30 days: decay by 25% per dream cycle (aggressive forgetting)
        This prevents the 4B model from drowning in stale context over time.
        """
        now = datetime.now(timezone.utc)

        # Fetch all edges with their last_activated timestamp
        rows = await _run_sql(
            "SELECT source, target, strength, last_activated FROM memory_graph_edges"
        )
        if not rows:
            return

        updates = []
        deletes = []
        for src, tgt, strength, last_activated in rows:
            try:
                if last_activated:
                    last_dt = datetime.fromisoformat(last_activated.replace("Z", "+00:00"))
                    if last_dt.tzinfo is None:
                        last_dt = last_dt.replace(tzinfo=timezone.utc)
                    age_days = (now - last_dt).days
                else:
                    age_days = 999  # unknown age → aggressive decay

                if age_days < 7:
                    decay = 1.0      # Active: no decay
                elif age_days < 30:
                    decay = 0.90     # Stale: gentle 10% decay
                else:
                    decay = 0.75     # Old: aggressive 25% decay

                new_strength = strength * decay
                if new_strength <= 0.05:
                    deletes.append((src, tgt))
                elif decay < 1.0:
                    updates.append((round(new_strength, 4), src, tgt))
            except Exception:
                continue

        if updates:
            await _run_many(
                "UPDATE memory_graph_edges SET strength=? WHERE source=? AND target=?",
                updates
            )
        for src, tgt in deletes:
            await _run_sql(
                "DELETE FROM memory_graph_edges WHERE source=? AND target=?",
                (src, tgt), commit=True
            )

        # ── Node pruning: remove stale low-weight nodes with no edges ──
        # A node with weight < 0.5 that has no remaining edges is orphaned
        # noise — it will never be activated and wastes spreading-activation cycles.
        orphan_result = await _run_sql(
            "DELETE FROM memory_graph_nodes WHERE weight < 0.5 "
            "AND id NOT IN (SELECT source FROM memory_graph_edges UNION SELECT target FROM memory_graph_edges)",
            commit=True
        )

        print(f"🧹 Dream: decayed {len(updates)} edges, pruned {len(deletes)} dead edges, removed orphan nodes.")


    async def _dream_generate_insight(self):
        rows = await _run_sql("SELECT summary FROM episodic ORDER BY timestamp DESC LIMIT 15")
        if not rows or len(rows) < 2: return  # Need at least 2 episodes
        text = " | ".join(r[0] for r in rows if r[0])
        
        # Check how many insights already exist to avoid flooding
        insight_count = await _run_sql(
            "SELECT COUNT(*) FROM semantic WHERE category='insight'", fetchall=False
        )
        if insight_count and insight_count[0] >= 20:
            return

        data = await self._llm_json(
            f"You are analyzing a user's recent conversation summaries to find a recurring behavioral pattern.\n\n"
            f"RULES:\n"
            f"- The pattern should appear in at least 2 of the summaries.\n"
            f"- Be specific and actionable (e.g. 'User frequently asks about Python debugging').\n"
            f"- Keep it under 20 words.\n"
            f"- If no clear pattern exists, return {{\"insight\": null}}.\n\n"
            f"Recent conversations:\n{text[:800]}\n\n"
            f"Return JSON: {{\"insight\": \"User tends to...\" or null}}",
            model=THINK_MODEL, timeout=45
        )
        
        insight = data.get("insight")
        if not insight or insight == "null" or len(insight) < 10 or len(insight) > 150:
            return
        # Check for duplicates
        existing = await _run_sql(
            "SELECT COUNT(*) FROM semantic WHERE category='insight' AND value=?",
            (insight,), fetchall=False
        )
        if existing and existing[0] > 0:
            return

        await _run_sql(
            "INSERT INTO semantic (id, category, label, value, confidence, source_episode_ids, last_updated) VALUES (?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), "insight", "Pattern", insight, 0.6, "[]", datetime.now(timezone.utc).isoformat()),
            commit=True
        )
        await _run_sql(
            "INSERT INTO dream_log VALUES (?,?,?,?)", 
            (str(uuid.uuid4()), datetime.now(timezone.utc).isoformat(), "insight_generated", insight),
            commit=True
        )

    async def _dream_promote_patterns(self):
        """Promote recurring reflection_bank lessons (2+ occurrences) to procedural rules."""
        rows = await _run_sql(
            "SELECT lesson, COUNT(*) as cnt FROM reflection_bank GROUP BY lesson HAVING cnt >= 2"
        )
        if not rows:
            return

        now = datetime.now(timezone.utc).isoformat()
        for lesson, count in rows:
            # Check if this lesson is already in procedural
            existing = await _run_sql(
                "SELECT id, evidence_count FROM procedural WHERE rule=?",
                (lesson,), fetchall=False
            )
            if existing:
                await _run_sql(
                    "UPDATE procedural SET evidence_count=? WHERE id=?",
                    (max(existing[1], count), existing[0]), commit=True
                )
            else:
                await _run_sql(
                    "INSERT INTO procedural VALUES (?,?,?,?,?)",
                    (str(uuid.uuid4()), lesson,
                     "Promoted from reflection bank after repeated failures", count, now),
                    commit=True
                )
                await _run_sql(
                    "INSERT INTO dream_log VALUES (?,?,?,?)",
                    (str(uuid.uuid4()), now, "rule_promoted",
                     f"Promoted to procedural: {lesson[:80]}"),
                    commit=True
                )

    async def _dream_evolve_prompt(self):
        """ADAS-style: rewrite the 'learned' layer based on reflection bank + episodic patterns."""
        reflection_rows = await _run_sql(
            "SELECT lesson FROM reflection_bank ORDER BY created_at DESC LIMIT 10"
        )
        episode_rows = await _run_sql(
            "SELECT summary FROM episodic ORDER BY timestamp DESC LIMIT 10"
        )

        # Need at least some episodic data — reflection bank alone is not required
        if not episode_rows or len(episode_rows) < 3:
            return

        current_learned_row = await _run_sql(
            "SELECT content FROM prompt_layers WHERE id='learned'",
            fetchall=False
        )
        current_learned = current_learned_row[0] if current_learned_row else ""

        lessons_text = "\n".join(f"- {r[0]}" for r in (reflection_rows or [])) or "(none yet)"
        episodes_text = "\n".join(f"- {r[0]}" for r in (episode_rows or []))

        data = await self._llm_json(
            f"You are improving an AI's learned behavior prompt.\n"
            f"Current learned prompt:\n{current_learned}\n\n"
            f"Recent interaction patterns:\n{episodes_text}\n\n"
            f"Lessons from past mistakes:\n{lessons_text}\n\n"
            f"Write an improved version of the learned prompt (2-4 sentences, imperative style).\n"
            f"JSON: {{\"new_prompt\": \"...\", \"change_title\": \"...\", \"explanation\": \"...\"}}",
            model=THINK_MODEL, timeout=60
        )

        new_prompt = data.get("new_prompt", "").strip()
        if not new_prompt or new_prompt == current_learned:
            return

        now = datetime.now(timezone.utc).isoformat()
        await _run_sql(
            "UPDATE prompt_layers SET content=?, last_updated=? WHERE id='learned'",
            (new_prompt, now), commit=True
        )
        await _run_sql(
            "INSERT INTO prompt_history VALUES (?,?,?,?,?,?)",
            (str(uuid.uuid4()),
             data.get("change_title", "Auto-evolved learned behavior"),
             now,
             data.get("explanation", "Updated based on conversation patterns."),
             current_learned[:120] if current_learned else None,
             new_prompt[:120]),
            commit=True
        )
        await _run_sql(
            "INSERT INTO dream_log VALUES (?,?,?,?)",
            (str(uuid.uuid4()), now, "prompt_evolved",
             data.get("change_title", "Learned layer updated")),
            commit=True
        )
        print(f"✨ Prompt evolved: {data.get('change_title', 'Updated')}")

    async def _dream_prune_working_memory(self):
        """
        Delete working_memory rows older than 30 days.
        Working memory is only meaningful in the context of an active session —
        keeping years of raw turn pairs wastes storage and slows session loads.
        Episodic memory (the compressed summaries) is the long-term record.
        """
        deleted = await _run_sql(
            "DELETE FROM working_memory WHERE timestamp < datetime('now', '-30 days')",
            commit=True
        )
        print("🧹 Dream: pruned working_memory rows older than 30 days.")

    # ── READ APIs (CACHED) ────────────────────────────────────────────────

    @cached(ttl_seconds=300)
    async def get_graph(self) -> dict:
        nodes = await _run_sql("SELECT id, label, type, description, weight FROM memory_graph_nodes")
        edges = await _run_sql("SELECT source, target, strength FROM memory_graph_edges")
        return {
            "nodes": [{"id": r[0], "label": r[1], "type": r[2], "description": r[3], "weight": r[4]} for r in (nodes or [])],
            "edges": [{"source": r[0], "target": r[1], "strength": r[2]} for r in (edges or [])],
        }

    @cached(ttl_seconds=120)
    async def get_episodic_memories(self, limit: int = 50) -> list:
        rows = await _run_sql(
            "SELECT id, timestamp, summary, tags, sentiment, user_message "
            "FROM episodic ORDER BY timestamp DESC LIMIT ?", (limit,)
        )
        return [{
            "id": r[0], "timestamp": r[1], "summary": r[2],
            "tags": json.loads(r[3] or "[]"), "sentiment": r[4],
            "user_message": (r[5] or "")[:120],
        } for r in (rows or [])]
    
    @cached(ttl_seconds=120)
    async def get_semantic_facts(self) -> list:
        rows = await _run_sql("SELECT id, category, label, value FROM semantic")
        return [{"id": r[0], "category": r[1], "label": r[2], "value": r[3]} for r in (rows or [])]

    @cached(ttl_seconds=120)
    async def get_prompt_layers(self) -> list:
        rows = await _run_sql("SELECT id, title, subtitle, content, badge, badge_type FROM prompt_layers")
        return [{"id": r[0], "title": r[1], "subtitle": r[2], "content": r[3], "badge": r[4], "badge_type": r[5]} for r in (rows or [])]
    
    @cached(ttl_seconds=120)
    async def get_dream_log(self, limit: int = 20) -> list:
        rows = await _run_sql("SELECT timestamp, action, detail FROM dream_log ORDER BY timestamp DESC LIMIT ?", (limit,))
        return [{"timestamp": r[0], "action": r[1], "detail": r[2]} for r in (rows or [])]

    @cached(ttl_seconds=120)
    async def get_prompt_update_history(self) -> list:
        rows = await _run_sql("SELECT id, title, date, explanation, diff_remove, diff_add FROM prompt_history ORDER BY date DESC")
        return [{"id": r[0], "title": r[1], "date": r[2], "explanation": r[3], "diff_remove": r[4], "diff_add": r[5]} for r in (rows or [])]

    @cached(ttl_seconds=120)
    async def get_procedural_rules(self) -> list:
        rows = await _run_sql("SELECT id, rule, reasoning, evidence_count, created_at FROM procedural ORDER BY evidence_count DESC")
        return [{"id": r[0], "rule": r[1], "reasoning": r[2], "evidence_count": r[3], "created_at": r[4]} for r in (rows or [])]

    @cached(ttl_seconds=60) # Less cache time as this updates often
    async def get_prospective_items(self) -> list:
        rows = await _run_sql(
            "SELECT id, content, urgency, due_hint, done, created_at FROM prospective ORDER BY "
            "CASE urgency WHEN 'urgent' THEN 1 WHEN 'soon' THEN 2 ELSE 3 END, created_at DESC"
        )
        return [{"id": r[0], "content": r[1], "urgency": r[2], "due_hint": r[3], "done": bool(r[4]), "created_at": r[5]} for r in (rows or [])]

    @cached(ttl_seconds=120)
    async def get_emotional_timeline(self) -> list:
        rows = await _run_sql("SELECT date, day_label, score, dominant_emotion FROM emotional ORDER BY date DESC LIMIT 14")
        return [{"date": r[0], "day_label": r[1], "score": r[2], "dominant_emotion": r[3]} for r in (rows or [])]

    @cached(ttl_seconds=120)
    async def get_contextual_patterns(self) -> list:
        rows = await _run_sql("SELECT id, trigger_desc, pattern, icon, confidence, observation_count FROM contextual ORDER BY confidence DESC")
        return [{"id": r[0], "trigger": r[1], "pattern": r[2], "icon": r[3], "confidence": r[4], "observation_count": r[5]} for r in (rows or [])]

    # ── WRITES (No Cache) ────────────────────────────────────────────────

    async def mark_prospective_done(self, item_id: str):
        await _run_sql("UPDATE prospective SET done=1 WHERE id=?", (item_id,), commit=True)
    
    async def update_prompt_layer(self, layer_id: str, content: str):
        await _run_sql("UPDATE prompt_layers SET content=? WHERE id=?", (content, layer_id), commit=True)
    
    async def delete_semantic_fact(self, fact_id: str):
        await _run_sql("DELETE FROM semantic WHERE id=?", (fact_id,), commit=True)

    async def update_semantic_fact(self, fact_id: str, value: str):
        await _run_sql("UPDATE semantic SET value=? WHERE id=?", (value, fact_id), commit=True)