# NOTE: cron-in-container scheduling is a best-guess default — this repo has
# no visibility into jelly-plate's actual convention (e.g. how
# mealie-ingest/arr-stack are scheduled there). Swap this out for an
# external scheduler invoking `uv run python main.py` if that better matches
# the existing setup.
#
# This tag's version must match the `playwright` version pinned in uv.lock —
# the base image bundles browser binaries for one specific driver version,
# and Playwright refuses to launch on a mismatch. If `uv lock --upgrade`
# (or `uv add playwright==...`) bumps the locked version, bump this tag too.
FROM mcr.microsoft.com/playwright/python:v1.62.0-jammy

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends cron \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY . .

RUN touch /var/log/studio-hunter-crash.log \
    && cp docker/crontab /etc/cron.d/studio-hunter \
    && chmod 0644 /etc/cron.d/studio-hunter \
    && crontab /etc/cron.d/studio-hunter

# Follow both the app's own rotating log (data/studio-hunter.log, mounted
# volume) and the crash-only fallback so `docker logs` shows everything.
CMD ["sh", "-c", "mkdir -p data && touch data/studio-hunter.log && cron && tail -f data/studio-hunter.log /var/log/studio-hunter-crash.log"]
