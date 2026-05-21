# Ekspertika — Python Backend

FastAPI service that powers the Ekspertika compliance checker for Lithuanian
construction documents (STR / HN / LST). PDF in, structured JSON out. The
Next.js frontend (`../nextjs-fe/`) is the only intended caller.

- Entry point: `app/api/main.py` (`POST /api/v1/check`, `GET /health`)
- Stack: Python 3.11+, FastAPI, uvicorn, OpenAI, optional Supabase
- Dependency manager: [`uv`](https://docs.astral.sh/uv/)

Project docs live in the sibling vault — see `vault/docs/technical-overview.md`
(symlink, not committed).

## Run locally

```bash
uv sync                  # runtime only — what the FastAPI service needs
uv sync --extra legacy   # add legacy CLI (main.py) + Streamlit UI + Excel export deps
cp .env.example .env     # then fill in OPENAI_API_KEY etc.
uv run uvicorn app.api.main:app --reload
# → http://127.0.0.1:8000/health
```

The deployed Docker image deliberately omits the `legacy` extra so that
`streamlit`, `openpyxl`, `tiktoken`, and `rich` (and their pandas / pyarrow /
numpy / altair / pydeck transitive deps) stay out of the runtime image.

Other entry points (legacy, not used by the FE — require `uv sync --extra legacy`):

```bash
uv run python main.py file.pdf                 # CLI: PDF → Excel
uv run streamlit run ui/streamlit_app.py       # local UI
uv run pytest                                  # tests
uv run ruff check .                             # lint
```

### STR source downloads (RAG ingest prep)

`scripts/download_str.py` pulls Lithuanian STR PDFs from e-tar.lt into
`data/str_texts/` and updates `data_pipeline/str_registry.json` with the
resolved `etar_url`s. Used for the Stage-2 RAG pipeline (`python-be-e6n.5`
Chroma ingest); the full 30-STR scrape lives in `python-be-e6n.9`.

Requires the `scraping` extra (`httpx`, `beautifulsoup4`, `lxml`) — kept out
of the deployed runtime image.

```bash
uv sync --extra scraping
uv run python scripts/download_str.py             # 5 representative must-haves
uv run python scripts/download_str.py --all-known # 8 acts (5 must-have + stretch)
uv run python scripts/download_str.py --codes "STR 2.02.01:2004,STR 2.05.05:2005"

uv sync                                            # back to runtime-only deps
```

Downloaded PDFs are gitignored — re-run the script to re-populate
`data/str_texts/`.

### RAG knowledge base (Epic 2)

The compliance checker can retrieve real STR regulation text from a local
Chroma vector store instead of relying on the model's training-cutoff
knowledge. One-shot ingest:

```bash
uv sync --extra rag --extra scraping        # rag = chromadb, scraping = bs4 for downloads
uv run python scripts/download_str.py        # if data/str_texts/ is empty
uv run python scripts/ingest_str.py          # extract → chunk → embed → upsert into vector_store/

# Optional flags
uv run python scripts/ingest_str.py --codes "STR 2.02.01:2004,STR 2.05.05:2005"
uv run python scripts/ingest_str.py --reset  # drop the str_chunks collection first
```

The vector store lives at `vector_store/` (gitignored — regenerable from the
PDFs + registry). `chromadb` is a dev-only `[rag]` extra and stays out of the
deployed runtime image; it'll graduate into `[project].dependencies` once
`RAGChecker` (`python-be-e6n.7`) is wired into the API path.

`tests/services/test_vector_store.py` requires the `rag` extra to be
installed (otherwise the module is skipped automatically).

To enable the RAG checker end-to-end:

```bash
uv sync --extra rag                          # chromadb runtime dep
uv run python scripts/ingest_str.py          # populate vector_store/
export USE_RAG=true                          # flip the flow toggle
# (optional) export RAG_TOP_K=5              # default top-K per query
```

When `USE_RAG=true`, both flows (`app.flows.compliance_flow`,
`app.flows.supabase_flow`) default to `RAGChecker` instead of `BasicChecker`
when the caller doesn't pass an explicit `checker=`. With it false (default)
the pipeline runs identically to before.

## Deployment

The service ships as a single Docker image. Railway is the primary target;
Render works the same way as a fallback.

### Required environment variables

| Var                    | Required           | Notes                                                                 |
| ---------------------- | ------------------ | --------------------------------------------------------------------- |
| `OPENAI_API_KEY`       | yes                | OpenAI auth                                                           |
| `ALLOWED_ORIGINS`      | yes (in prod)      | Comma-separated CORS origins, e.g. `https://ekspertika.vercel.app`    |
| `INTERNAL_API_TOKEN`   | yes (in prod)      | Shared bearer token between Next.js and this service                  |
| `NEXT_PUBLIC_SUPABASE_URL` | optional        | Same URL the FE uses; `SUPABASE_URL` also accepted as override        |
| `SUPABASE_SERVICE_ROLE_KEY` | optional      | Service-role key (server-side only — never ship to FE)                |
| `COMPLIANCE_MODEL`     | optional           | Defaults to `gpt-4o-mini`                                             |

`PORT` is injected by Railway/Render — don't set it manually.

### Local Docker build & run

```bash
docker build -t ekspertika-be:local .
docker run --rm -p 8000:8000 \
  -e OPENAI_API_KEY=$OPENAI_API_KEY \
  -e ALLOWED_ORIGINS=http://localhost:3000 \
  ekspertika-be:local
curl http://127.0.0.1:8000/health
```

### Railway (primary)

1. Push this repo to GitHub.
2. In Railway: **New Project → Deploy from GitHub repo** → pick `python-be`.
3. Railway auto-detects `railway.toml` + `Dockerfile`. No further build config
   needed.
4. **Variables** tab: add the env vars from the table above.
5. Deploy. Railway exposes the public URL once `/health` returns 200.

After deploy, sanity-check:

```bash
curl https://<your-service>.up.railway.app/health
```

### Render (fallback)

Render reads the same `Dockerfile`. Create a **Web Service**, point it at this
repo, leave the Start Command **empty** (Dockerfile's `CMD` wins), set the env
vars in the dashboard, and pick the cheapest paid plan that allows persistent
HTTP. Healthcheck path: `/health`.
