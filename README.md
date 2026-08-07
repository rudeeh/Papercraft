# docRAG

A document question-answering system that extracts text from PDFs using OCR and enables semantic search through a REST API.

## Architecture

- **FastAPI** backend with async processing via Celery
- **Redis** for task queue management
- **doctr** for OCR text extraction
- **Qdrant** vector database for semantic search
- **Sentence Transformers** for generating embeddings (local)
- **Ollama** (local) or **OpenRouter** (cloud) for LLM inference
- **Angular 17** frontend with standalone components

### How It Works

The system uses **two different models** for different purposes:

```
PDF → OCR → Text chunks → Embedding Model → Vectors stored in Qdrant
                                                    ↓
User Query → Embedding Model → Vector search → Top K chunks retrieved
                                                    ↓
                              Retrieved chunks + Query → LLM → Answer
```

1. **Embedding Model** (local, free) - Converts text into vectors for semantic search
2. **LLM Model** (local or cloud) - Generates natural language answers from retrieved context

## Requirements

- Docker & Docker Compose (for backend)
- Node.js 18+ (for frontend development)
- (Optional) Make for build shortcuts

## Project Structure

```
docRAG/
├── app/                    # Backend Python application
│   ├── api/               # FastAPI routes and main app
│   ├── core/              # Configuration
│   ├── services/          # OCR, embeddings, LLM, vector store
│   └── worker/            # Celery tasks
├── frontend-angular/       # Angular 17 frontend (recommended)
│   ├── src/
│   │   ├── app/
│   │   │   ├── components/  # UI components
│   │   │   ├── services/    # API service
│   │   │   └── models/      # TypeScript interfaces
│   │   └── styles.scss      # Global styles
│   ├── Dockerfile
│   └── nginx.conf
├── frontend/              # Legacy vanilla JS frontend
├── scripts/               # Helper scripts
└── docker-compose.yml
```

## Setup

### Backend Configuration

Create a `.env` file to configure your models.

#### LLM Configuration

Choose one of the following LLM providers:

**Option 1: Ollama (Local - Free)**

```env
LLM_PROVIDER=ollama
LLM_MODEL=llama3
```

After starting Docker, pull the model:
```bash
docker-compose exec ollama ollama pull llama3
```

Available Ollama models: `llama3`, `llama3.2`, `mistral`, `codellama`, `phi3`, etc.

**Option 2: OpenRouter (Cloud - Paid)**

```env
LLM_PROVIDER=openrouter
LLM_MODEL=google/gemini-2.0-flash-001
OPENROUTER_API_KEY=sk-or-v1-your-key-here
```

Get your API key at https://openrouter.ai/keys

Popular OpenRouter models:
- `google/gemini-2.0-flash-001` - Fast and cheap
- `anthropic/claude-3.5-sonnet` - High quality
- `meta-llama/llama-3-70b-instruct` - Open source
- `openai/gpt-4o-mini` - Good balance

See all models at https://openrouter.ai/models

#### Embedding Model Configuration

The embedding model runs locally and is configured via the `EMBEDDING_MODEL` environment variable. Default is `all-MiniLM-L6-v2`.

```env
EMBEDDING_MODEL=all-MiniLM-L6-v2
```

To change the embedding model:

1. Update your `.env` file:
   ```env
   EMBEDDING_MODEL=all-mpnet-base-v2
   ```

2. Restart the services:
   ```bash
   docker-compose restart api worker
   ```

3. **Important**: If you change the embedding model after uploading documents, you must re-upload them. Different models produce incompatible vectors.

Available embedding models (from [Sentence Transformers](https://www.sbert.net/docs/pretrained_models.html)):

| Model | Dimensions | Speed | Quality |
|-------|------------|-------|---------|
| `all-MiniLM-L6-v2` | 384 | Fast | Good (default) |
| `all-MiniLM-L12-v2` | 384 | Medium | Better |
| `all-mpnet-base-v2` | 768 | Slow | Best |
| `paraphrase-MiniLM-L6-v2` | 384 | Fast | Good for paraphrasing |

#### Complete .env Example

```env
# LLM Configuration
LLM_PROVIDER=openrouter          # or "ollama"
LLM_MODEL=google/gemini-2.0-flash-001
OPENROUTER_API_KEY=sk-or-v1-your-key-here

# Embedding Configuration
EMBEDDING_MODEL=all-MiniLM-L6-v2

# Optional: RAG tuning
RAG_TOP_K=5                      # Number of chunks to retrieve
CHUNK_TOKENS=500                 # Size of text chunks
CHUNK_OVERLAP_TOKENS=50          # Overlap between chunks
```

## Running

### Backend (Docker)

Start all backend services:

```bash
docker-compose up -d --build
```

This launches:
- **Redis** - task queue (port 6379)
- **Qdrant** - vector database (port 6333)
- **Ollama** - local LLM inference (port 11434) - only used if `LLM_PROVIDER=ollama`
- **API** - FastAPI backend (port 8000)
- **Worker** - Celery background processing

### Frontend (Angular)

#### Local Development

```bash
cd frontend-angular
npm install
npm start
```

The frontend runs at http://localhost:4200 and connects to the backend at http://localhost:8000.

#### Production Build

```bash
cd frontend-angular
npm run build
```

Output is in `frontend-angular/dist/docrag-frontend/browser/`.

#### Docker (Optional)

Build and run the frontend container:

```bash
cd frontend-angular
docker build -t docrag-frontend .
docker run -p 8080:80 docrag-frontend
```

## Deployment

### Backend on Render

1. Create a new Web Service on Render
2. Connect your repository
3. Set build command: `docker build -t app .`
4. Set start command based on your Dockerfile
5. Add environment variables from your `.env` file

The backend is configured to accept CORS requests from any origin (`allow_origins=["*"]`).

### Frontend on Vercel

1. Import your repository on Vercel
2. Set the root directory to `frontend-angular`
3. Framework preset: Angular
4. Build command: `npm run build`
5. Output directory: `dist/docrag-frontend/browser`

The frontend is pre-configured to connect to `https://docrag-2gvg.onrender.com`. To change this, update:
- `frontend-angular/src/app/services/api.service.ts` - `DEFAULT_API_URL` constant
- `frontend-angular/src/index.html` - default input value

### Keep-Alive for Render Free Tier

The Angular frontend includes automatic keep-alive functionality that pings the backend every 14 minutes to prevent Render's free tier from sleeping (which happens after 15 minutes of inactivity). This only works while the frontend is open in a browser.

For reliable uptime without the frontend open, use an external monitoring service like UptimeRobot to ping your backend's `/api/v1/health` endpoint.

## Usage

### Web Interface

Open the frontend URL in your browser:
- Local: http://localhost:4200
- Deployed: Your Vercel URL

**Features:**
- API URL configuration with online/offline status indicator
- Drag & drop PDF upload with optional force re-processing
- Task status monitoring with auto-refresh
- Chat/query interface with citation display
- Recent tasks tracking with quick actions

### Quick Start Workflow

1. **Upload a PDF**
   - Drag & drop or click to select a PDF file
   - Click "Upload PDF"
   - Note the `task_id` and `doc_id` returned

2. **Monitor Processing**
   - The task ID auto-fills for status checking
   - Status progresses: PENDING → STARTED → SUCCESS

3. **Query Documents**
   - Enter your question in the chat section
   - Optionally specify a `doc_id` to search only that document
   - View the answer and citations with source references

### Command Line

Upload a PDF:
```bash
./scripts/load_small_pdf.sh "/path/to/document.pdf"
```

Query your documents:
```bash
./scripts/query_rag.sh "What is the main topic of the document?"
```

## API Reference

All endpoints are prefixed with `/api/v1`:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/health` | GET | Health check |
| `/api/v1/upload` | POST | Upload a PDF for processing |
| `/api/v1/status/{task_id}` | GET | Check processing status |
| `/api/v1/chat` | POST | Query documents with natural language |

### Example API Calls

**Upload PDF:**
```bash
curl -X POST "http://localhost:8000/api/v1/upload" \
  -F "file=@document.pdf"
```

**Check Status:**
```bash
curl "http://localhost:8000/api/v1/status/{task_id}"
```

**Chat Query:**
```bash
curl -X POST "http://localhost:8000/api/v1/chat" \
  -H "Content-Type: application/json" \
  -d '{"query": "What is this document about?", "doc_id": "optional-doc-id"}'
```

## Development

### Backend

Run tests:
```bash
docker-compose run api pytest
```

View logs:
```bash
docker-compose logs -f api      # API logs
docker-compose logs -f worker   # Worker logs
docker-compose logs -f ollama   # LLM logs
```

### Frontend

The Angular frontend uses:
- Angular 17 with standalone components
- Signals for reactive state management
- SCSS for styling
- Inter font for typography

Key files:
- `src/app/services/api.service.ts` - API communication and state
- `src/app/components/` - UI components (upload, status, chat, recent-tasks, header)
- `src/styles.scss` - Global styles and CSS variables

## Troubleshooting

### Model not found error
```
model 'llama3' not found
```
Pull the model: `docker-compose exec ollama ollama pull llama3`

### API shows "Offline" in frontend
- Check containers are running: `docker-compose ps`
- Check API logs: `docker-compose logs api`
- Verify the API URL is correct in the frontend header
- If deployed, ensure CORS is enabled and the backend is awake

### CORS errors in browser console
The backend has CORS enabled for all origins. If you still see errors:
- Ensure the backend is running and accessible
- Check that the URL doesn't have a trailing slash
- Verify the backend responded (might be sleeping on Render free tier)

### OCR processing fails
Check worker logs: `docker-compose logs -f worker`

### Status stays "PENDING"
- Check worker is running: `docker-compose ps`
- Check worker logs: `docker-compose logs worker`
- Verify Redis and Qdrant are healthy

### Connection errors
Verify Qdrant is running: `curl http://localhost:6333/collections`

### No results when querying
- Ensure document processing completed (status: SUCCESS)
- Check if embeddings were generated in the worker logs
- Try a more specific question

### Changed embedding model but search doesn't work
If you change `EMBEDDING_MODEL` after uploading documents, the old vectors are incompatible. Re-upload your documents to regenerate embeddings with the new model.

### Frontend build fails
```bash
cd frontend-angular
rm -rf node_modules package-lock.json
npm install
npm run build
```

### Render backend sleeping
Render free tier sleeps after 15 minutes of inactivity. The first request after sleeping may take 30-60 seconds. Keep the frontend open to maintain keep-alive pings, or use an external monitoring service.
