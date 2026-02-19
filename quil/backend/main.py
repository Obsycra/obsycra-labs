"""
Quil Backend v7
===============
API for Chat, Memory, Ingestion, Autonomy, Workshop Queue, Governor WebSocket,
OS Context hooks, and Tool Execution Guard.

Changes from v6:
- ReflAct gate before every chat inference (State→Goal→Tone alignment)
- Emotional calibration injected into every streaming Ollama call
- Workshop steps use _llm_think() to capture + store reasoning traces
- /os/context endpoint receives Rust active-window signals
- Tool execution guard with allowlist + pattern blocking
- CORS locked to Tauri origin only (not wildcard)
- OLLAMA_OPTIONS propagated to streaming calls
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional
import asyncio
import json
import re
import httpx
import uvicorn

from memory.manager import MemoryManager, OLLAMA_OPTIONS

# ─── LIFESPAN ─────────────────────────────────────────────────────────────────

memory = MemoryManager()

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start background tasks on startup, clean up on shutdown."""
    dream_task = asyncio.create_task(memory.dream_loop())
    governor_task = asyncio.create_task(_governor_loop())
    print("🪶  Quil backend ready (v7).")
    yield
    # Shutdown: cancel background tasks
    dream_task.cancel()
    governor_task.cancel()
    print("🪶  Quil backend shutting down.")

app = FastAPI(title="Quil Backend", version="0.7.0", lifespan=lifespan)

# CORS — covers all Tauri v1/v2 WebView origins across platforms.
# Tauri v1 macOS  : tauri://localhost
# Tauri v2 macOS  : tauri://localhost  or  https://tauri.localhost
# Tauri v1 Windows: https://tauri.localhost
# file:// loads   : no Origin header sent → middleware passes through
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "tauri://localhost",
        "https://tauri.localhost",
        "http://localhost:1420",
        "http://127.0.0.1:1420",
    ],
    # Catch tauri:// schemes AND any 127.0.0.1:PORT the Tauri dev server picks
    allow_origin_regex=r"tauri://.*|https://tauri\..*|http://127\.0\.0\.1:\d+|http://localhost:\d+",
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def _log_origin(request: Request, call_next):
    origin = request.headers.get("origin", "<none>")
    if request.method == "OPTIONS" or origin != "<none>":
        print(f"[CORS] {request.method} {request.url.path}  origin={origin}")
    return await call_next(request)

OLLAMA_BASE   = "http://127.0.0.1:11434"   # Always loopback, never 0.0.0.0
DEFAULT_MODEL = "qwen3:4b"

# ─── TOOL EXECUTION GUARD ─────────────────────────────────────────────────────
# Allowlist + blocklist for any future tool-calling feature.
# All LLM-requested tool calls MUST pass _validate_tool_call() before execution.

ALLOWED_TOOLS = {"read_file", "search_memory", "list_directory", "web_search"}
BLOCKED_CMD_PATTERNS = [
    r"rm\s+-[rRf]", r"sudo\s+", r"curl\s+.*\|\s*(ba)?sh",
    r"wget\s+.*-O\s*/", r"chmod\s+[0-7]{3,4}", r"dd\s+if=",
    r">\s*/(?:etc|usr|bin|sbin|var)", r"mkfs\.", r":(){ :|:& };:",
]

def _validate_tool_call(tool_name: str, args: dict) -> tuple[bool, str]:
    """Returns (allowed, reason). Every tool call must pass this guard."""
    if tool_name not in ALLOWED_TOOLS:
        return False, f"Tool '{tool_name}' is not in the allowlist."
    for val in args.values():
        if isinstance(val, str):
            for pattern in BLOCKED_CMD_PATTERNS:
                if re.search(pattern, val, re.IGNORECASE):
                    return False, f"Blocked dangerous pattern: {pattern}"
    return True, "ok"

# ─── WEBSOCKET BROADCAST (Governor → UI) ──────────────────────────────────────

_ws_clients: list[WebSocket] = []

async def _broadcast(event: dict):
    dead = []
    for ws in _ws_clients:
        try:
            await ws.send_json(event)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _ws_clients.remove(ws)

@app.websocket("/ws/events")
async def ws_events(websocket: WebSocket):
    await websocket.accept()
    _ws_clients.append(websocket)
    try:
        while True:
            # Keep connection alive; client sends pings
            await websocket.receive_text()
    except WebSocketDisconnect:
        if websocket in _ws_clients:
            _ws_clients.remove(websocket)

# ─── GOVERNOR (Always-active background worker) ───────────────────────────────

async def _governor_loop():
    """
    Runs continuously. Every 45 s it:
    1. Surfaces unsurfaced prospective items (reminders) based on urgency tiers
    2. Checks if the dream cycle should run early (many new episodes)
    3. Cleans up old completed/stale prospective items
    4. Broadcasts any pending alerts over WebSocket
    """
    print("🏛  Governor started.")
    while True:
        await asyncio.sleep(45)
        try:
            alerts = await memory.get_pending_alerts()
            for alert in alerts:
                await _broadcast({"type": "alert", "payload": alert})

            # If 5+ new episodes since last dream, trigger an early dream cycle
            from memory.manager import _run_sql
            count_row = await _run_sql(
                "SELECT COUNT(*) FROM episodic WHERE timestamp > datetime('now','-1 hour')",
                fetchall=False
            )
            if count_row and count_row[0] >= 5:
                asyncio.create_task(memory._dream_generate_insight())
                asyncio.create_task(memory._dream_promote_patterns())
                await _broadcast({"type": "status", "payload": {"msg": "Dream cycle triggered early"}})

            # Auto-clean completed prospective items older than 7 days
            await _run_sql(
                "DELETE FROM prospective WHERE done=1 AND created_at < datetime('now','-7 days')",
                commit=True
            )
            # Auto-mark stale prospective items (older than 30 days, never surfaced) as done
            await _run_sql(
                "UPDATE prospective SET done=1 WHERE done=0 AND surfaced=0 "
                "AND created_at < datetime('now','-30 days')",
                commit=True
            )

        except Exception as e:
            print(f"Governor error: {e}")

@app.get("/health")
async def health():
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{OLLAMA_BASE}/api/tags")
            models = [m["name"] for m in r.json().get("models", [])]
            model_ready = any(DEFAULT_MODEL.split(":")[0] in m for m in models)
            return {
                "status": "ok",
                "ollama": True,
                "model_ready": model_ready,
                "default_model": DEFAULT_MODEL
            }
    except Exception as e:
        return {"status": "degraded", "ollama": False, "error": str(e)}


# ─── INGESTION (DOCUMENT UPLOAD) ──────────────────────────────────────────────

class IngestRequest(BaseModel):
    filename: str
    text: str

@app.post("/ingest")
async def ingest_document(req: IngestRequest):
    """Standard ingest — returns when complete."""
    count = await memory.ingest_file(req.filename, req.text)
    return {"status": "ok", "chunks_created": count, "filename": req.filename}

@app.post("/ingest/stream")
async def ingest_document_stream(req: IngestRequest):
    """Streaming ingest — sends SSE progress events for large files."""
    async def _gen():
        stored = [0]
        total_chunks = [1]

        async def progress_cb(done, total):
            stored[0] = done
            total_chunks[0] = total
            pct = int(done / max(total, 1) * 100)
            yield f"data: {json.dumps({'type': 'progress', 'done': done, 'total': total, 'pct': pct})}\n\n"

        # Run ingest with a progress generator
        raw_chunks = memory._split_sections(req.text)
        total = len(raw_chunks)
        total_chunks[0] = total

        yield f"data: {json.dumps({'type': 'start', 'total': total, 'filename': req.filename})}\n\n"

        count = 0
        batch = []
        for i, chunk_text in enumerate(raw_chunks):
            chunk_id = f"doc_{req.filename}_{i}"
            try:
                import uuid as _uuid
                chunk_id = f"doc_{req.filename}_{_uuid.uuid4().hex[:6]}"
                vec = await memory._embed(chunk_text)
                if vec and memory._vtable is not None:
                    vec = (vec + [0.0] * 768)[:768]
                    batch.append({"id": chunk_id, "text": f"[{req.filename}] {chunk_text}",
                                  "memory_type": "document", "vector": vec})
            except Exception: pass

            if len(batch) >= 10:
                try:
                    memory._vtable.add(batch)
                    count += len(batch)
                    batch = []
                    pct = int(count / max(total, 1) * 100)
                    yield f"data: {json.dumps({'type': 'progress', 'done': count, 'total': total, 'pct': pct})}\n\n"
                    await asyncio.sleep(0.02)
                except Exception: batch = []

        if batch:
            try: memory._vtable.add(batch); count += len(batch)
            except: pass

        asyncio.create_task(memory._extract_doc_facts(req.filename, req.text[:4000]))
        yield f"data: {json.dumps({'type': 'done', 'chunks_created': count, 'filename': req.filename})}\n\n"

    return StreamingResponse(_gen(), media_type="text/event-stream")


# ─── AUTONOMY (PROACTIVE ALERTS) ──────────────────────────────────────────────

@app.get("/autonomy/check")
async def check_autonomy():
    """
    Called by Frontend every 30s.
    Returns list of messages Quil wants to send PROACTIVELY.
    """
    alerts = await memory.get_pending_alerts()
    return {"alerts": alerts}


# ─── CHAT ─────────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message:    str
    session_id: Optional[str]  = "default"
    stream:     Optional[bool] = True

# ── ReflAct Gate ──────────────────────────────────────────────────────────────
async def _reflact_gate(query: str, context: dict) -> dict:
    """
    ReflAct Stage 1-2: State construction → Goal + Tone alignment.
    Runs BEFORE the main LLM call. Uses /no_think for speed (target <800ms).
    Returns {"inferred_goal": str, "tone_needed": str}.
    """
    recent = "; ".join(e["summary"] for e in context.get("recent_episodes", []))
    facts  = "; ".join(f"{f['label']}: {f['value']}" for f in context.get("facts", []))

    result = await memory._llm_json(
        f"Session context: {recent or 'first turn.'}\n"
        f"Known facts: {facts or 'none.'}\n"
        f"User message: {query}\n\n"
        f"Return JSON:\n"
        f"{{\"inferred_goal\": \"one sentence\", \"tone_needed\": \"concise|detailed|empathetic\"}}",
        timeout=8
    )
    return result if result else {"inferred_goal": "", "tone_needed": "concise"}

def _needs_reflact(msg: str) -> bool:
    """
    Zero-latency heuristic gate. Skip the ReflAct LLM call for messages
    that are clearly simple (greetings, short replies, single-word commands).
    Eliminates ~800ms of unnecessary TTFT overhead on conversational turns.

    Fires when:
      - Message is > 20 chars (long enough to have intent structure)
      - AND contains a question/emphasis marker OR has 5+ words
    """
    q = msg.strip()
    if len(q) <= 20:
        return False
    if len(q.split()) < 5 and not any(c in q for c in "?!"):
        return False
    return True


@app.post("/chat")
async def chat(req: ChatRequest):
    context = await memory.build_context(req.message, req.session_id)

    # ReflAct: align on goal + tone before constructing the final prompt.
    # Skipped for short/simple messages to eliminate ~800ms TTFT overhead.
    if _needs_reflact(req.message):
        state = await _reflact_gate(req.message, context)
        goal  = state.get("inferred_goal", "")
        tone  = state.get("tone_needed", "concise")
    else:
        goal, tone = "", "concise"

    system_prompt = memory.get_composed_prompt(context)
    if goal and tone:
        system_prompt += f"\n\nInferred goal this turn: {goal}. Preferred tone: {tone}."

    history  = await memory.get_working_memory(req.session_id)
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(history)
    # /no_think appended here for chat — model suppresses <think> at source
    messages.append({"role": "user", "content": f"/no_think\n{req.message}"})

    if req.stream:
        return StreamingResponse(
            _stream_ollama(messages, req.message, req.session_id),
            media_type="text/event-stream",
        )
    return {"response": "Stream mode required"}

def _strip_leaked_reasoning(text: str) -> str:
    """
    Strips plaintext reasoning blocks that some model versions emit without
    <think> tags. These look like a long internal monologue ending with a
    double-newline or a clear "Final response:" / "---" separator before the
    actual answer.

    Strategy: if the first non-empty line looks like reasoning (starts with a
    known meta-commentary pattern), find the last clearly-separated paragraph
    and return only that. Otherwise return the text unchanged.
    """
    stripped = text.strip()
    if not stripped:
        return stripped

    _REASONING_STARTS = (
        "okay,", "alright,", "let me", "let's", "i need to", "i should",
        "we are", "the user", "this seems", "this is a", "since this",
        "first,", "first i", "so,", "right,", "now,",
        "critical rules", "checking", "looking at",
    )

    first_line = stripped.split("\n")[0].strip().lower()
    is_reasoning = any(first_line.startswith(p) for p in _REASONING_STARTS)

    if not is_reasoning:
        return stripped

    # Look for common separator patterns the model uses before its final answer
    separators = [
        "\n\n\n",           # triple newline
        "\n---\n",          # markdown hr
        "\nFinal response:", # explicit label
        "\nFinal answer:",
        "\nResponse:",
        "\nAnswer:",
    ]
    for sep in separators:
        idx = stripped.rfind(sep)
        if idx != -1:
            candidate = stripped[idx + len(sep):].strip()
            if candidate:
                return candidate

    # Fallback: take the last double-newline-separated paragraph
    paragraphs = [p.strip() for p in stripped.split("\n\n") if p.strip()]
    if len(paragraphs) > 1:
        # The last paragraph is almost always the actual answer
        last = paragraphs[-1]
        # Sanity check: last paragraph shouldn't itself look like reasoning
        last_low = last.lower()
        if not any(last_low.startswith(p) for p in _REASONING_STARTS):
            return last

    return stripped


async def _stream_ollama(messages: list, user_message: str, session_id: str):
    """
    Streams tokens from Ollama with true token-by-token streaming.

    Pipeline:
    - Tokens stream in live from Ollama — no artificial delay.
    - <think>...</think> blocks are discarded as they arrive (zero buffering
      for non-think tokens — true TTFT).
    - If the model emits plaintext reasoning (no <think> tags), the full
      response is collected, stripped post-hoc, and flushed immediately.
      Detection: if the first ~400 chars match a reasoning pattern, we switch
      to collect-then-flush mode for that response.
    """
    _REASONING_STARTS = (
        "okay,", "alright,", "let me", "let's ", "i need to", "i should",
        "we are", "the user", "this seems", "this is a", "since this",
        "first,", "first i", "so, ", "right,", "now, ",
        "critical rules", "checking ", "looking at",
    )

    full_response = ""
    in_think = False
    think_buf = ""

    # Leaked-reasoning detection state
    # We buffer the first 400 chars silently to check for reasoning leak.
    # Once we know it's clean, we flush the buffer and stream normally.
    # If it's a reasoning leak, we collect everything and strip at the end.
    _probe_buf = ""          # buffer during the probe window
    _probe_done = False      # True once we've made the clean/leak decision
    _is_leak = False         # True if we detected a reasoning leak
    _leak_buf = ""           # accumulate full response if leak detected
    _PROBE_WINDOW = 80       # chars — enough to catch "Okay, ..." / "Let me ..." patterns

    emo_params = await memory.get_emotional_inference_params()

    try:
        async with httpx.AsyncClient(timeout=180) as client:
            async with client.stream(
                "POST", f"{OLLAMA_BASE}/api/chat",
                json={
                    "model": DEFAULT_MODEL,
                    "messages": messages,
                    "stream": True,
                    "options": {**OLLAMA_OPTIONS, **emo_params},
                },
            ) as r:
                async for line in r.aiter_lines():
                    if not line:
                        continue
                    try:
                        data  = json.loads(line)
                        token = data.get("message", {}).get("content", "")
                        if not token:
                            continue
                        full_response += token

                        # ── <think> block filter (standard path) ─────────
                        if not in_think:
                            combined = think_buf + token
                            think_start = combined.lower().find("<think>")
                            if think_start != -1:
                                before = combined[:think_start]
                                in_think = True
                                think_buf = combined[think_start:]
                                # Route any pre-think text through the normal path
                                token = before
                                if not token:
                                    continue
                            else:
                                think_buf = ""
                        else:
                            think_buf += token
                            close_idx = think_buf.lower().find("</think>")
                            if close_idx != -1:
                                after = think_buf[close_idx + 8:]
                                in_think = False
                                think_buf = ""
                                token = after
                                if not token:
                                    continue
                            else:
                                continue  # still in think block, discard

                        # ── Leaked-reasoning detection ────────────────────
                        if _is_leak:
                            # Already confirmed leak — just accumulate
                            _leak_buf += token
                            continue

                        if not _probe_done:
                            _probe_buf += token
                            if len(_probe_buf) >= _PROBE_WINDOW:
                                _probe_done = True
                                probe_low = _probe_buf.lstrip().lower()
                                if any(probe_low.startswith(p) for p in _REASONING_STARTS):
                                    # Reasoning leak — switch to collect mode
                                    _is_leak = True
                                    _leak_buf = _probe_buf
                                    _probe_buf = ""
                                else:
                                    # Clean — flush the buffered probe content now
                                    yield f"data: {json.dumps({'type': 'token', 'content': _probe_buf})}\n\n"
                                    _probe_buf = ""
                            # Still in probe window — keep buffering silently
                            continue

                        # Normal streaming — token is clean
                        yield f"data: {json.dumps({'type': 'token', 'content': token})}\n\n"

                    except Exception:
                        continue

                # ── End of stream: flush anything remaining ───────────────
                if not _probe_done and _probe_buf:
                    # Short response — never hit the probe window
                    probe_low = _probe_buf.lstrip().lower()
                    if any(probe_low.startswith(p) for p in _REASONING_STARTS):
                        _is_leak = True
                        _leak_buf = _probe_buf
                    else:
                        yield f"data: {json.dumps({'type': 'token', 'content': _probe_buf})}\n\n"

                if _is_leak and _leak_buf:
                    # Strip the reasoning block and emit the clean answer
                    clean = _strip_leaked_reasoning(_leak_buf.strip())
                    if clean:
                        yield f"data: {json.dumps({'type': 'token', 'content': clean})}\n\n"

        clean_response = _strip_leaked_reasoning(memory._strip_think(full_response))
        await memory.save_episode(session_id, user_message, clean_response)
        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    except Exception as e:
        yield f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"


# ─── MEMORY API ───────────────────────────────────────────────────────────────

@app.get("/memory/graph")
async def get_memory_graph(): return await memory.get_graph()

@app.get("/memory/episodic")
async def get_episodic(limit: int = 50): return await memory.get_episodic_memories(limit)

@app.get("/memory/semantic")
async def get_semantic(): return await memory.get_semantic_facts()

@app.get("/memory/procedural")
async def get_procedural(): return await memory.get_procedural_rules()

@app.get("/memory/prospective")
async def get_prospective(): return await memory.get_prospective_items()

@app.get("/memory/emotional")
async def get_emotional(): return await memory.get_emotional_timeline()

@app.get("/memory/contextual")
async def get_contextual(): return await memory.get_contextual_patterns()

@app.get("/memory/dreams")
async def get_dream_log(limit: int = 20): return await memory.get_dream_log(limit)

@app.delete("/memory/semantic/{fact_id}")
async def delete_semantic_fact(fact_id: str):
    await memory.delete_semantic_fact(fact_id)
    return {"deleted": fact_id}

@app.put("/memory/semantic/{fact_id}")
async def update_semantic_fact(fact_id: str, body: dict):
    await memory.update_semantic_fact(fact_id, body.get("value", ""))
    return {"updated": fact_id}

@app.put("/memory/prospective/{item_id}/done")
async def mark_prospective_done(item_id: str):
    await memory.mark_prospective_done(item_id)
    return {"done": item_id}

class ProspectiveAddRequest(BaseModel):
    content: str
    urgency: Optional[str] = "soon"

@app.post("/memory/prospective")
async def add_prospective_item(req: ProspectiveAddRequest):
    """Manually add a task/reminder."""
    import uuid as _uuid
    from datetime import datetime as _dt, timezone as _tz
    item_id = str(_uuid.uuid4())
    urgency = req.urgency if req.urgency in ("urgent", "soon", "later") else "soon"
    now = _dt.now(_tz.utc).isoformat()
    from memory.manager import _run_sql
    await _run_sql(
        "INSERT INTO prospective VALUES (?,?,?,?,?,?,?,?)",
        (item_id, req.content, urgency, "", 0, now, "", 0),
        commit=True
    )
    return {"id": item_id, "status": "added"}


# ─── PROMPT API ───────────────────────────────────────────────────────────────

@app.get("/prompt/layers")
async def get_prompt_layers(): return await memory.get_prompt_layers()

@app.put("/prompt/layers/{layer_id}")
async def update_prompt_layer(layer_id: str, body: dict):
    await memory.update_prompt_layer(layer_id, body.get("content", ""))
    return {"updated": layer_id}

@app.get("/prompt/history")
async def get_prompt_history(): return await memory.get_prompt_update_history()


# ─── WORKSHOP (Persistent Task Queue with Micro-Tasks) ─────────────────────────

import uuid as _uuid
from datetime import datetime as _dt, timezone as _tz

# In-memory task registry (also persisted to SQLite below)
_workshop_jobs: dict = {}  # job_id → full job dict
_active_job_id: str = None  # Currently executing job
_cancel_requested: set = set()  # job_ids that should be cancelled

class WorkshopRequest(BaseModel):
    topic: str
    session_id: Optional[str] = "default"

@app.post("/workshop/draft")
async def workshop_draft(req: WorkshopRequest):
    """Queue a background task. If nothing is running, start immediately."""
    job_id = _uuid.uuid4().hex[:8]
    now = _dt.now(_tz.utc).isoformat()
    job = {
        "job_id": job_id,
        "topic": req.topic,
        "session_id": req.session_id,
        "status": "queued",
        "steps": [],
        "current_step": None,
        "result": None,
        "created_at": now,
        "started_at": None,
        "completed_at": None,
        "minimized": False,
    }
    _workshop_jobs[job_id] = job

    # Persist to SQLite
    from memory.manager import _run_sql
    await _run_sql(
        "INSERT OR REPLACE INTO workshop_jobs (id, topic, session_id, status, steps_json, result, created_at, started_at, completed_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (job_id, req.topic, req.session_id, "queued", "[]", None, now, None, None),
        commit=True
    )

    # Kick the queue processor
    asyncio.create_task(_process_workshop_queue())

    await _broadcast({"type": "workshop_queued", "payload": job})
    return {"job_id": job_id, "status": "queued"}

@app.post("/workshop/cancel/{job_id}")
async def cancel_workshop_job(job_id: str):
    """Cancel a running or queued job."""
    if job_id in _workshop_jobs:
        _cancel_requested.add(job_id)
        job = _workshop_jobs[job_id]
        if job["status"] == "queued":
            job["status"] = "cancelled"
            from memory.manager import _run_sql
            await _run_sql("UPDATE workshop_jobs SET status='cancelled' WHERE id=?", (job_id,), commit=True)
        await _broadcast({"type": "workshop_cancelled", "payload": {"job_id": job_id}})
        return {"cancelled": job_id}
    return {"error": "Job not found"}

@app.get("/workshop/jobs")
async def list_workshop_jobs():
    # Merge persisted jobs on startup
    if not _workshop_jobs:
        from memory.manager import _run_sql
        rows = await _run_sql(
            "SELECT id, topic, session_id, status, steps_json, result, created_at, started_at, completed_at "
            "FROM workshop_jobs ORDER BY created_at DESC LIMIT 50"
        )
        for r in (rows or []):
            _workshop_jobs[r[0]] = {
                "job_id": r[0], "topic": r[1], "session_id": r[2],
                "status": r[3], "steps": json.loads(r[4] or "[]"),
                "current_step": None, "result": r[5],
                "created_at": r[6], "started_at": r[7], "completed_at": r[8],
                "minimized": False,
            }
    return list(_workshop_jobs.values())

@app.get("/workshop/jobs/{job_id}")
async def get_workshop_job(job_id: str):
    return _workshop_jobs.get(job_id, {"status": "not_found"})

async def _process_workshop_queue():
    """Process the next queued job. Only one runs at a time (LLM is single-threaded)."""
    global _active_job_id
    if _active_job_id and _workshop_jobs.get(_active_job_id, {}).get("status") == "running":
        return  # Already processing

    # Find next queued job
    for jid, job in _workshop_jobs.items():
        if job["status"] == "queued" and jid not in _cancel_requested:
            _active_job_id = jid
            asyncio.create_task(_run_workshop_job(jid, job["topic"], job.get("session_id", "default")))
            return

async def _run_workshop_job(job_id: str, topic: str, session_id: str):
    """Run a task with micro-task breakdown: Plan → Execute Steps → Final Output."""
    global _active_job_id
    job = _workshop_jobs[job_id]
    job["status"] = "running"
    job["started_at"] = _dt.now(_tz.utc).isoformat()

    from memory.manager import _run_sql
    await _run_sql("UPDATE workshop_jobs SET status='running', started_at=? WHERE id=?",
                   (job["started_at"], job_id), commit=True)
    await _broadcast({"type": "workshop_started", "payload": {"job_id": job_id, "topic": topic}})

    try:
        # ── STEP 1: Generate outline / plan ──
        if job_id in _cancel_requested:
            raise asyncio.CancelledError()

        await _update_step(job_id, "planning", "Analyzing task and creating outline…")

        plan_data = await memory._llm_json(
            f"Break down this task into 4-8 small, specific mini-steps. Each step should be a single focused action that produces a clear output.\n\n"
            f"Task: {topic}\n\n"
            f"RULES:\n"
            f"- Each step must be concrete and achievable (e.g. 'List the 3 main arguments for X' not 'Research X').\n"
            f"- Steps should build on each other sequentially — each one uses the output of the previous.\n"
            f"- First step should always be gathering/identifying the core information.\n"
            f"- Last step should always be assembling the final answer.\n"
            f"- Use action verbs: List, Identify, Compare, Explain, Draft, Evaluate, Summarize.\n"
            f"- Each step description should be 5-15 words.\n\n"
            f"Return JSON: {{\"title\": \"Short task title (3-6 words)\", \"steps\": [\"Step 1 description\", \"Step 2 description\", ...]}}\n",
            model="qwen3:4b", timeout=30
        )

        steps = plan_data.get("steps", [])
        if not steps or len(steps) < 2:
            steps = [
                f"Identify the core question in: {topic[:60]}",
                f"Gather key information and facts",
                f"Analyze and organize findings",
                f"Draft a clear, structured answer",
                f"Review and finalize the output",
            ]
        task_title = plan_data.get("title", topic[:60])

        step_entries = [{"label": s, "status": "pending", "output": None} for s in steps]
        job["steps"] = step_entries
        await _persist_job(job_id)
        await _broadcast({"type": "workshop_plan", "payload": {
            "job_id": job_id, "title": task_title, "steps": step_entries
        }})

        # ── STEP 2: Execute each micro-task with _llm_think() ──
        # Workshop uses think-mode: captures reasoning trace per step.
        # force_rag=True: Workshop always gets full vector search.
        context = await memory.build_context(topic, session_id, force_rag=True)
        accumulated = ""

        # Step-role classifier: routes to slim system prompt per step type
        STEP_ROLES = {
            "extract": ["extract", "find", "identify", "list", "gather", "research"],
            "analyze": ["analyze", "evaluate", "compare", "assess", "check", "review"],
            "write":   ["write", "draft", "create", "compose", "generate", "summarize", "finalize"],
        }
        STEP_SYSTEM = {
            "extract": "You are a precise fact extractor. Output ONLY the requested data. No prose.",
            "analyze": "You are a critical analyst. Output structured observations. Be terse.",
            "write":   "You are a clean technical writer. Output polished, well-structured text.",
        }

        def _classify_step(label: str) -> str:
            low = label.lower()
            for role, kws in STEP_ROLES.items():
                if any(k in low for k in kws):
                    return role
            return "write"

        for i, step in enumerate(step_entries):
            if job_id in _cancel_requested:
                raise asyncio.CancelledError()

            step["status"] = "running"
            job["current_step"] = i
            await _broadcast({"type": "workshop_step_start", "payload": {
                "job_id": job_id, "step_index": i, "label": step["label"]
            }})

            role       = _classify_step(step["label"])
            slim_sys   = STEP_SYSTEM[role]

            # _llm_think captures reasoning trace — stored but not sent to UI
            step_data = await memory._llm_think(
                f"You are completing step {i+1} of {len(step_entries)} for the task: \"{topic}\"\n\n"
                f"YOUR STEP: {step['label']}\n\n"
                f"Work completed so far:\n{accumulated[-600:] if accumulated else '(This is the first step — start fresh.)'}\n\n"
                f"RULES:\n"
                f"- Complete ONLY this one step. Do not skip ahead.\n"
                f"- Be thorough but focused on this specific step.\n"
                f"- Use **bold** for key terms and structure your output with bullet points or numbered lists.\n"
                f"- Output should be 100-300 words for this step.\n"
                f"- Build directly on the previous work if applicable.",
                system=slim_sys, timeout=75
            )

            step_result   = step_data["output"]
            step_reasoning = step_data["reasoning"]

            step["status"]          = "done"
            step["output"]          = step_result
            step["reasoning_trace"] = step_reasoning  # stored, hidden from UI
            accumulated += f"\n\n## {step['label']}\n{step_result}"

            await _persist_job(job_id)
            await _broadcast({"type": "workshop_step_done", "payload": {
                "job_id": job_id, "step_index": i, "label": step["label"],
                "output": step_result[:300]
            }})

        # ── STEP 3: Final assembly ──
        if job_id in _cancel_requested:
            raise asyncio.CancelledError()

        await _update_step(job_id, "finalizing", "Assembling final output…")

        final_data = await memory._llm_think(
            f"Combine the following completed work into a final, polished, well-structured output.\n\n"
            f"Task: {topic}\n\n"
            f"Completed work:\n{accumulated}\n\n"
            f"FORMATTING RULES:\n"
            f"- Use a clear title with ##\n"
            f"- Use ### for sub-sections\n"
            f"- Use **bold** for key terms\n"
            f"- Use bullet points and numbered lists\n"
            f"- Remove redundancy between steps\n"
            f"- Make it read as one coherent document, not separate step outputs\n"
            f"- Be concise but comprehensive",
            system=STEP_SYSTEM["write"], timeout=120
        )
        final = final_data["output"]

        job["status"] = "done"
        job["result"] = final
        job["completed_at"] = _dt.now(_tz.utc).isoformat()
        await _persist_job(job_id)

        await _broadcast({
            "type": "workshop_done",
            "payload": {"job_id": job_id, "topic": topic, "result": final}
        })

    except asyncio.CancelledError:
        job["status"] = "cancelled"
        await _persist_job(job_id)
        await _broadcast({"type": "workshop_cancelled", "payload": {"job_id": job_id}})
    except Exception as e:
        job["status"] = "error"
        job["result"] = str(e)
        await _persist_job(job_id)
        await _broadcast({
            "type": "workshop_error",
            "payload": {"job_id": job_id, "topic": topic, "error": str(e)}
        })
    finally:
        _active_job_id = None
        _cancel_requested.discard(job_id)
        # Process next queued job
        asyncio.create_task(_process_workshop_queue())

async def _update_step(job_id: str, phase: str, message: str):
    """Send a status update for a job phase."""
    await _broadcast({"type": "workshop_status", "payload": {
        "job_id": job_id, "phase": phase, "message": message
    }})

async def _persist_job(job_id: str):
    """Persist job state to SQLite."""
    job = _workshop_jobs.get(job_id)
    if not job: return
    from memory.manager import _run_sql
    await _run_sql(
        "UPDATE workshop_jobs SET status=?, steps_json=?, result=?, started_at=?, completed_at=? WHERE id=?",
        (job["status"], json.dumps(job["steps"]), job.get("result"),
         job.get("started_at"), job.get("completed_at"), job_id),
        commit=True
    )


# ─── OS CONTEXT ENDPOINT (Rust active-window monitor → prospective memory) ────

class OsContextSignal(BaseModel):
    context: str      # "coding" | "meeting" | "planning" | "terminal" | "writing"
    window_title: str
    app: str

_OS_CONTEXT_KEYWORDS: dict[str, list[str]] = {
    "coding":   ["code", "debug", "pr", "commit", "review", "bug", "test", "build"],
    "meeting":  ["meeting", "agenda", "present", "discuss", "sync"],
    "planning": ["plan", "deadline", "schedule", "due", "goal", "milestone"],
    "terminal": ["deploy", "build", "run", "script", "migrate", "install"],
    "writing":  ["write", "draft", "document", "report", "article", "note"],
}

@app.post("/os/context")
async def receive_os_context(signal: OsContextSignal):
    keywords = _OS_CONTEXT_KEYWORDS.get(signal.context, [])
    if not keywords:
        return {"triggered": False, "items_surfaced": 0}
    from memory.manager import _run_sql
    rows = await _run_sql("SELECT id, content FROM prospective WHERE done=0 AND surfaced=0")
    triggered = 0
    for item_id, content in (rows or []):
        if any(kw in content.lower() for kw in keywords):
            await _broadcast({"type": "alert", "payload": {
                "content": f"💡 Since you opened {signal.app}: {content}",
                "id": item_id, "source": "os_trigger",
            }})
            await _run_sql("UPDATE prospective SET surfaced=1 WHERE id=?", (item_id,), commit=True)
            triggered += 1
    # if triggered:
        # print(f"🖥  OS context {signal.context} → surfaced {triggered} item(s).")
    return {"triggered": triggered > 0, "items_surfaced": triggered}


# ─── DEV TRIGGERS ─────────────────────────────────────────────────────────────

@app.post("/dev/dream")
async def dev_dream():
    """Manual trigger for dream cycle."""
    asyncio.create_task(asyncio.gather(
        memory._dream_prune_graph(),
        memory._dream_generate_insight(),
    ))
    return {"triggered": True}


if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8765, reload=False)