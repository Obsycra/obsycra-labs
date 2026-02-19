<p align="center">
  <img src="https://img.shields.io/badge/🪶_Quil-Private_AI_Companion-2A2118?style=for-the-badge&labelColor=3D9970" alt="Quil" />
</p>

<h1 align="center">🪶 Quil</h1>

<p align="center">
  <strong>A private, local AI companion that learns you over time.</strong><br/>
  Everything runs on your device. Nothing leaves your machine.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/status-beta-amber?style=flat-square" alt="Status" />
  <img src="https://img.shields.io/badge/license-MIT-green?style=flat-square" alt="License" />
  <img src="https://img.shields.io/badge/platform-macOS-blue?style=flat-square" alt="Platform" />
  <img src="https://img.shields.io/badge/LLM-Qwen3_4B-purple?style=flat-square" alt="Model" />
</p>

---

## What is Quil?

Quil is a **desktop AI assistant** that runs entirely on your computer. No cloud. No API keys. No subscriptions. Unlike ChatGPT or other hosted AI tools, Quil:

- 🧠 **Remembers you** across sessions using 7 types of memory modeled after human cognition
- 📈 **Improves over time** by automatically refining its own prompts based on what works
- ⚡ **Runs tasks in the background** so you can delegate work like "build me a resume" and keep chatting
- 🗺️ **Shows you its memory** through an interactive graph of everything it has learned
- 🔒 **Stays private** -- your conversations, documents, and data never leave your machine

### Who is this for?

| You are... | Quil gives you... |
|:-----------|:------------------|
| **A deep worker** | An AI that retains context across sessions -- no re-explaining yourself |
| **Privacy-conscious** | Zero cloud dependency. Your data stays on your SSD. |
| **A developer** | A reference architecture for local memory, agents, and self-improving prompts |
| **Curious about AI** | A transparent system where you can see *how* the AI thinks and learns |

---

## Demo

> *Chat with Quil. Upload a document. Watch it break a task into steps. Explore the memory graph.*

```
┌─────────────────────────────────────────────────────┐
│  🪶 Quil                                            │
├──────────┬──────────────────────────────────────────┤
│          │  Good evening ✦                          │
│  Chat    │                                          │
│          │  You: Build me a resume                  │
│ Workshop │                                          │
│          │  Quil: I'll work on that in the          │
│ -Memory- │  Workshop. You'll see it when it's done. │
│          │                                          │
│  Brain   │  +- Workshop ---------------------------+ │
│          │  | ✓ Analyze requirements               | │
│ -Intel-  │  | ⟳ Draft professional summary        | │
│          │  | ○ Format skills & experience         | │
│ How I    │  | ○ Review and polish                  | │
│ Think    │  +-------------------------------------+ │
│          │                                          │
│  Ready   │  [Message Quil...                  ⬆]   │
└──────────┴──────────────────────────────────────────┘
```

---

## Features

### 💬 Chat
Talk to Quil like any AI assistant. It remembers what you said last week.

### 🔧 Workshop (Background Tasks)
Give Quil a complex task. It will:
1. **Plan** -- Break it into 3-6 steps
2. **Execute** -- Process each step with live progress updates
3. **Deliver** -- Assemble a polished final result

Queue multiple tasks. Cancel anytime. Results persist across restarts.

### 🧠 Brain (Memory Explorer)
An interactive force-directed graph showing every memory, fact, and connection Quil has formed. Canvas-rendered for smooth performance with thousands of nodes. Click any node to inspect it.

### ✨ Self-Improving Prompts
Every hour, Quil runs a background optimization cycle:
- Evaluates its own response quality
- Detects recurring mistakes
- Rewrites its own instructions
- Logs every change as a readable diff

### 📄 Document Ingestion
Upload PDFs, text files, code, or markdown. Quil splits them into semantic sections, embeds them for search, and extracts key facts into memory.

---

## Memory Architecture

| Layer | What it stores | How it's used |
|:------|:---------------|:-------------|
| **Working** | Current conversation | Live context window (~12 turns) |
| **Episodic** | Past conversations | Timestamped summaries with sentiment |
| **Semantic** | Facts about you | "Prefers concise answers" |
| **Procedural** | Behavioral rules | Auto-promoted from repeated feedback |
| **Prospective** | Future tasks | "Push to GitHub this week" |
| **Emotional** | Mood over time | Weekly sentiment chart |
| **Contextual** | Situational patterns | "After 10pm, use reflective tone" |

Retrieval uses hybrid search: vector similarity (LanceDB) combined with graph spreading activation to surface context that pure embeddings miss.

---

## Tech Stack

| Component | Technology | Why |
|:----------|:-----------|:----|
| Desktop App | **Tauri 2.0** (Rust) | ~5MB binary, native performance |
| Frontend | **Vanilla JS** + HTML/CSS | No framework overhead |
| Memory Graph | **D3.js + Canvas** | 100k+ node performance |
| Backend | **Python FastAPI** | Async, WebSocket streaming |
| LLM | **Qwen3 4B** via Ollama | Runs on 8GB RAM |
| Embeddings | **nomic-embed-text** | Local, 768-dim vectors |
| Vectors | **LanceDB** | Rust-native, embedded, no server |
| Database | **SQLite** + WAL mode | Concurrent reads, zero config |

---

## Getting Started

### Requirements

| Requirement | Minimum |
|:------------|:--------|
| **OS** | macOS 12+ |
| **RAM** | 8GB (16GB recommended) |
| **Python** | 3.11+ |
| **Rust** | Latest stable ([rustup.rs](https://rustup.rs)) |
| **Ollama** | Latest ([ollama.com](https://ollama.com/download)) |

### Setup

```bash
git clone https://github.com/bryanthunsberger/quil.git
cd quil
chmod +x setup.sh dev.sh
./setup.sh
```

`setup.sh` will:
- Create a Python virtualenv and install dependencies
- Install Rust and the Tauri CLI if not already present
- Pull `qwen3:4b` and `nomic-embed-text` models via Ollama

### Run

```bash
./dev.sh
```

Starts Ollama, the FastAPI backend, and the Tauri window in one command.

### Backend only (API testing)

```bash
cd backend && source .venv/bin/activate
python main.py
# Swagger UI available at http://localhost:8765/docs
```

---

## Project Structure

```
quil/
├── backend/                 # Python FastAPI server
│   ├── main.py              # API, WebSocket, Workshop task queue
│   ├── requirements.txt
│   └── memory/
│       ├── manager.py       # 7-layer memory engine + LLM + vectors
│       └── models.py        # Pydantic schemas
├── src/                     # Frontend (served by Tauri)
│   ├── index.html           # App shell
│   ├── css/main.css         # Full stylesheet
│   └── js/
│       ├── api.js           # Backend API client
│       ├── app.js           # Navigation, Workshop, file upload
│       ├── brain.js         # Memory explorer (all 7 types)
│       ├── chat.js          # Chat + streaming + WebSocket
│       ├── graph.js         # Canvas force-directed memory graph
│       └── prompt.js        # Prompt editor + history viewer
├── src-tauri/               # Tauri/Rust native shell
│   ├── src/main.rs
│   └── tauri.conf.json
├── setup.sh                 # One-command setup
├── dev.sh                   # One-command dev mode
└── README.md
```

---

## API

Full Swagger docs at `http://localhost:8765/docs`.

| Method | Endpoint | Description |
|:-------|:---------|:------------|
| `POST` | `/chat` | Send a message (streams SSE tokens) |
| `POST` | `/ingest` | Upload a document to memory |
| `POST` | `/workshop/draft` | Queue a background task |
| `POST` | `/workshop/cancel/{id}` | Cancel a running or queued task |
| `GET`  | `/workshop/jobs` | List all tasks with status |
| `GET`  | `/memory/graph` | Full memory graph (nodes + edges) |
| `GET`  | `/memory/episodic` | Conversation history |
| `GET`  | `/memory/semantic` | Learned facts (editable) |
| `GET`  | `/memory/prospective` | To-do items and reminders |
| `POST` | `/memory/prospective` | Add a task manually |
| `GET`  | `/prompt/layers` | Current prompt configuration |
| `PUT`  | `/prompt/layers/{id}` | Edit a prompt layer |
| `GET`  | `/prompt/history` | Prompt evolution log |
| `WS`   | `/ws/events` | Real-time event stream |

---

## Contributing

Quil is open source. PRs are welcome.

```bash
# Fork, clone, and set up
git clone https://github.com/bryanthunsberger/quil.git
cd quil && ./setup.sh

# Backend with hot reload
cd backend && source .venv/bin/activate
uvicorn main:app --host 127.0.0.1 --port 8765 --reload

# Frontend: edit files in src/ and refresh the Tauri window
```

---

## License

MIT. Use it however you want.

---

<p align="center">
  <em>Built by a human who believes AI should serve people, not platforms.</em>
</p>
