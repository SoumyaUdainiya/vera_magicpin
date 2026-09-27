# Magicpin Vera Bot — Deterministic HTTP Bot

Rule-based FastAPI bot implementing the 5 required endpoints
(`/v1/healthz`, `/v1/metadata`, `/v1/context`, `/v1/tick`, `/v1/reply`).
No LLM/API key required — fast, deterministic, in-memory state.

## Run locally
```bash
pip install -r requirements.txt
uvicorn bot:app --host 0.0.0.0 --port 8080
```

## Deploy on Render (fastest path)
1. Push `bot.py`, `requirements.txt`, `render.yaml` to a new GitHub repo (or a Render "Blueprint" / "Web Service" pointing at a zip/repo).
2. On Render: **New +** → **Web Service** → connect the repo.
3. Render auto-detects `render.yaml`. If asked manually, set:
   - Build command: `pip install -r requirements.txt`
   - Start command: `uvicorn bot:app --host 0.0.0.0 --port $PORT`
4. Deploy. Wait for "Live" status (~1-2 min).
5. Your public URL will look like `https://magicpin-vera-bot.onrender.com`.

## Submission URL format
```
https://<your-render-subdomain>.onrender.com
```
(Judge will hit `https://<your-render-subdomain>.onrender.com/v1/healthz`, etc.)

## Test healthz
```bash
curl https://<your-render-subdomain>.onrender.com/v1/healthz
```
