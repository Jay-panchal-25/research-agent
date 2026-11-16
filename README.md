# Research Agent 🔬

An autonomous deep research assistant with WhatsApp integration. It conducts verified multi-source web and academic research, cross-checks claims against source quotes, and delivers insightful summaries along with executive, cited PDF reports.

---

## ✨ Features

- **💬 Natural Human Chat:** Responds warmly and conversationally to greetings and small talk—no repetitive canned sales pitches.
- **⚡ Pure Async Python Pipeline:** High performance, zero heavy agent-framework overhead. Native `asyncio.gather` for parallel research across sub-questions.
- **🌐 Multi-Source Deep Research:** Searches both real-time web sources (DuckDuckGo) and academic papers (arXiv & Semantic Scholar).
- **🛡️ Fact-Checked & Quote-Grounded:** Extracts and verifies claims against verbatim source text before writing reports.
- **📄 Executive PDF Dossiers:** Automatically compiles beautifully formatted research reports with callout cards, running headers, and clickable citation links using ReportLab.
- **🔄 Rate-Limit & Failure Resilient:** Automatic fallback to high-capacity models (`llama-3.3-70b-versatile`) if primary models experience rate limits or capacity constraints.

---

## 🏛️ Architecture

Built with a clean layered architecture (**Controller ➔ Router ➔ Services ➔ Database ➔ Models**):

```
WhatsApp Cloud API (Meta)
  │
  ▼
Webhook Controller (Signature verification, allowlist check, deduplication)
  │
  ▼
Redis Queue (ARQ Worker)
  │
  ▼
Research Controller (Pure Async Python Pipeline)
  ├── 1. Router      ──> Chat (Natural) / Clarify / Follow-up / Deep Research
  ├── 2. Planner     ──> Decomposes complex queries into focused sub-questions
  ├── 3. Researchers ──> Parallel workers (asyncio.gather) searching web & literature
  ├── 4. Scraper     ──> SSRF-guarded HTML/PDF extraction & Qdrant semantic indexing
  ├── 5. Extractor   ──> Extracts factual claims grounded in verbatim quotes
  ├── 6. Critic      ──> Evaluates sufficiency and fills knowledge gaps
  ├── 7. Verifier    ──> Batch checks claims against source quotes
  └── 8. Writer      ──> Generates natural summary & executive ReportLab PDF
```

---

## 📂 Project Structure

```
research-agent/
├── app/
│   ├── controllers/          # Business logic & pipeline orchestration
│   │   ├── research_controller.py
│   │   └── webhook_controller.py
│   ├── database/             # Storage & Vector database clients
│   │   ├── mongo.py          # MongoDB conversation & report storage
│   │   └── qdrant.py         # Qdrant vector database client
│   ├── models/               # Domain data models & Pydantic schemas
│   │   ├── research.py
│   │   └── whatsapp.py
│   ├── prompts/              # Prompt templates (chat, planner, summary, writer)
│   ├── routers/              # FastAPI HTTP endpoints
│   │   ├── health.py
│   │   └── webhook.py
│   ├── services/             # Core capabilities (LLM, embeddings, PDF generator)
│   │   ├── tools/            # Web & academic search, page fetching, safety guards
│   │   ├── budget.py         # Deadline & resource usage tracking
│   │   ├── llm.py            # LiteLLM client with auto-fallbacks
│   │   ├── report.py         # Executive PDF compiler (ReportLab)
│   │   └── whatsapp.py       # WhatsApp Cloud API client
│   ├── config.py             # Settings & environment configuration
│   ├── logging_config.py     # Clean structured logging with filename & line numbers
│   ├── main.py               # FastAPI app & in-process ARQ worker
│   └── worker.py             # Standalone ARQ worker settings
├── scripts/
│   ├── doctor.py             # Preflight health check for all keys & services
│   ├── send_test_message.py  # Webhook simulation script
│   └── test_pipeline.py      # End-to-end pipeline dry-run test
├── docker-compose.yml        # Redis, MongoDB, and Qdrant containers
├── pyproject.toml            # Project metadata
└── requirements.txt          # Python dependencies
```

---

## 🚀 Quick Start Guide

### 1. Prerequisites
- **Python 3.10+**
- **Docker Desktop** (for Redis, MongoDB, and Qdrant)
- **API Keys:**
  - [Groq API Key](https://console.groq.com/keys) (Fast LLM inference)
  - [Google Gemini API Key](https://aistudio.google.com/) (Vector embeddings)
  - [Meta for Developers](https://developers.facebook.com/) (WhatsApp Cloud API)

---

### 2. Setup

1. **Start Database Containers:**
   ```powershell
   docker compose up -d
   ```

2. **Setup Virtual Environment:**
   ```powershell
   python -m venv venv
   .\venv\Scripts\activate
   pip install -r requirements.txt
   ```

3. **Configure Environment Variables:**
   ```powershell
   copy .env.example .env
   ```
   Fill in your API keys in `.env`:
   - `GROQ_API_KEY`: Your Groq key
   - `EMBEDDING_API_KEY`: Your Gemini key
   - `WA_ACCESS_TOKEN`: Meta WhatsApp System User token (set to "Never expire")
   - `WA_PHONE_NUMBER_ID`: Your WhatsApp Phone Number ID
   - `META_APP_SECRET`: Your Meta App Secret
   - `WA_VERIFY_TOKEN`: Your chosen verification token
   - `ALLOWED_NUMBERS`: Your phone number with country code (e.g., `919876543210`)

4. **Verify Preflight Health:**
   ```powershell
   python -m scripts.doctor
   ```
   Ensure all checks report `[ OK ]`.

---

### 3. Running the Server

Start the API and in-process background worker:
```powershell
uvicorn app.main:app --reload --port 8000
```

In a separate terminal, expose your local server with ngrok:
```powershell
ngrok http 8000
```

In the **Meta Developer Portal ➔ WhatsApp ➔ Configuration**:
- Set **Callback URL** to `https://<your-ngrok-domain>/webhook`
- Set **Verify Token** to match `WA_VERIFY_TOKEN` in `.env`
- Under **Webhook fields**, subscribe to **`messages`**

---

## 🔑 Meta WhatsApp Configuration

### Getting a Permanent Token (Never Expires)
1. Go to **[Meta Business Settings](https://business.facebook.com/settings)** ➔ **Users** ➔ **System Users**.
2. Click **Add** ➔ Name: `research-bot` ➔ Role: **Admin system user**.
3. Click **Assign Assets**:
   - Under **Apps**, select your App ➔ Toggle **Manage App (Full control)**.
   - Under **WhatsApp Accounts**, select your WhatsApp account ➔ Toggle **Full control**.
   - Click **Save Changes**.
4. Click **Generate New Token**:
   - Select your App.
   - Set **Token Expiration** to **Never**.
   - Select permissions:
     - `whatsapp_business_messaging`
     - `whatsapp_business_management`
   - Click **Generate Token**.
5. Copy the generated token into `WA_ACCESS_TOKEN` in `.env`.

---

## 🛠️ Testing

- **Verify all connections & keys:**
  ```powershell
  python -m scripts.doctor
  ```
- **Test pipeline locally without WhatsApp API:**
  ```powershell
  python -m scripts.test_pipeline
  ```
- **Simulate incoming signed WhatsApp webhook:**
  ```powershell
  python -m scripts.send_test_message "Compare RAG vs Fine-Tuning in 2026"
  ```
