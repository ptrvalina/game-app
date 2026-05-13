# Amber

Amber is a lightweight AI compliance copilot for AML teams. It provides a FastAPI service with a browser console, deterministic anomaly scoring, explainable evidence, and LLM-assisted compliance narratives.

## Run locally

```powershell
.\scripts\start.ps1
```

Open `http://127.0.0.1:8000/console`.

## Test locally

```powershell
.\scripts\pytest.ps1
python scripts\verify.py
```

## Deploy on Render

This repository includes `render.yaml` for a Docker-based web service.

Recommended environment:

- `AMBER_ENV=staging`
- `AMBER_ENABLE_CONSOLE=true`
- `AMBER_ENABLE_DOCS=true`
- `OPENAI_API_KEY` or `ANTHROPIC_API_KEY`

Health endpoint: `/health`
Console endpoint: `/console`
