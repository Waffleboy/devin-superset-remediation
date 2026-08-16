# uv-based image: same packaging path as local dev (uv + uv.lock), reproducible.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# Node/npm so the live scanner can run `npm audit` over a mounted Superset
# frontend checkout (surfaces the dompurify bump and the deck.gl DoS chain).
# pip-audit is a project dependency. Skipped work is a no-op in DEMO_MODE.
RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs npm \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies first, from the lockfile, in a cached layer. `--no-dev`
# keeps ruff/mypy out of the runtime image; `--frozen` fails if uv.lock is stale
# rather than silently re-resolving.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY orchestrator/ ./orchestrator/
COPY playbooks/ ./playbooks/
COPY scripts/ ./scripts/

# Put the uv-managed virtualenv on PATH so `uvicorn`/`python` resolve to it.
ENV PATH="/app/.venv/bin:$PATH"

WORKDIR /app/orchestrator
EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
