FROM ubuntu:22.04
ENV DEBIAN_FRONTEND=noninteractive

# Base system deps (includes PostgreSQL server, not just client; nginx fronts
# the in-container worker pool; software-properties-common enables deadsnakes)
RUN apt-get update && apt-get install -y \
    curl wget git ca-certificates gnupg software-properties-common \
    python3 python3-pip rsync nginx \
    postgresql postgresql-client \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libdbus-1-3 libatspi2.0-0 \
    libx11-6 libxcomposite1 libxdamage1 libxext6 \
    libxfixes3 libxrandr2 libgbm1 libxcb1 \
    libxkbcommon0 libpango-1.0-0 libcairo2 libasound2 \
    && rm -rf /var/lib/apt/lists/*

# System Python 3.12 (deadsnakes) so /opt/venv is backed by an interpreter that
# physically lives in the image. Building the venv from a uv-managed download
# leaves a `cpython-3.12-...` alias symlink that can point at the build host's
# HOME (e.g. /home/<user>/.local/share/uv/...), which then dangles under
# enroot/pyxis. A system interpreter removes that whole class of breakage.
RUN add-apt-repository -y ppa:deadsnakes/ppa && apt-get update && \
    apt-get install -y python3.12 python3.12-venv python3.12-dev \
    && rm -rf /var/lib/apt/lists/*

# uv
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

# Node.js 22
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g npm@10

# Python venv with ORS + task dependencies (built from the system 3.12 above)
RUN uv venv /opt/venv --python /usr/bin/python3.12 --python-preference only-system && \
    uv pip install --python /opt/venv/bin/python \
    "openreward>=0.1.95" \
    "mcp>=1.0.0" \
    "pydantic>=2.0" \
    psycopg2-binary \
    openpyxl \
    python-docx \
    python-pptx \
    "pypdf>=5,<7" \
    pyyaml \
    aiofiles \
    termcolor \
    psutil \
    arxiv \
    bibtexparser \
    canvasapi \
    prompt_toolkit

ENV PATH="/opt/venv/bin:$PATH"
ENV VIRTUAL_ENV="/opt/venv"
ENV LOCAL_SERVERS_PATH=/opt/local_servers

# Freeze "today" to the benchmark's authoring window. Seeded train data is keyed
# to fixed dates (2026-03-10/12/15) and the 12306 server rejects past dates, so
# without this the rail tasks are unsolvable once wall-clock drifts past them.
# preprocess and evaluation both receive this same frozen launch_time, so
# relative-date tasks stay internally consistent. Override per-run with
# `-e OPENREWARD_FROZEN_DATE=YYYY-MM-DD` (or empty to use real wall-clock).
ENV OPENREWARD_FROZEN_DATE=2026-03-08

# Copy and build MCP servers
COPY local_servers/ /opt/local_servers/

# Build the Node.js MCP servers one at a time: `npm ci` installs exactly what a
# server's package-lock.json pins (`npm install` for the two without one), then
# `npm run build`. Any failure stops the image build with an error naming the
# server. (They used to install in parallel into one npm cache, where two npm
# processes can pick the same temporary file and both fail, and `|| true` and a
# bare `wait` dropped every failure, so an image could ship without a server.)
RUN set -e; \
    for server in \
        12306-mcp \
        Calendar-Autoauth-MCP-Server \
        filesystem \
        google-forms-mcp \
        HowToCook-mcp \
        mcp-canvas-lms \
        mcp-npx-fetch \
        playwright-mcp \
        servers \
        youtube-mcp-server \
        woocommerce-mcp \
        notion-mcp-server; do \
        echo "=== npm: $server ==="; \
        cd "/opt/local_servers/$server"; \
        if [ -f package-lock.json ]; then install=ci; else install=install; fi; \
        npm "$install" --no-audit --no-fund \
            || { echo "ERROR: MCP server $server: npm $install failed" >&2; exit 1; }; \
        npm run build \
            || { echo "ERROR: MCP server $server: npm run build failed" >&2; exit 1; }; \
    done

# Install the browser for the Node Playwright MCP package. Otherwise
# browser_install tries to download at runtime, which is slow and often
# unavailable on HPC nodes.
RUN cd /opt/local_servers/playwright-mcp && npm run build && \
    node --max-old-space-size=96 --test tests/runtime-limits.mjs && \
    npx playwright install chromium

# Build the Python MCP servers one at a time with `uv sync --locked`, which
# installs what each uv.lock pins and fails if a lock no longer matches its
# pyproject.toml. Any failure stops the image build with an error naming the
# server. Preferring system interpreters keeps every server on the Python it got
# from the parallel build this replaces (no uv-managed Python was installed yet
# when each server picked one); a server whose .python-version the system lacks
# still gets a uv-managed interpreter.
RUN set -e; \
    for server in \
        arxiv-mcp-server \
        arxiv-latex-mcp \
        yahoo-finance-mcp \
        emails-mcp \
        mcp-google-sheets \
        mcp-snowflake-server \
        mcp-scholarly \
        Office-Word-MCP-Server \
        Office-PowerPoint-MCP-Server \
        excel-mcp-server \
        pdf-tools-mcp \
        mcp-youtube-transcript \
        cli-mcp-server; do \
        echo "=== uv: $server ==="; \
        cd "/opt/local_servers/$server"; \
        UV_PYTHON_PREFERENCE=system uv sync --locked \
            || { echo "ERROR: MCP server $server: uv sync --locked failed" >&2; exit 1; }; \
    done

# Normalize any uv-managed python aliases to RELATIVE symlinks. The Python MCP
# sub-servers run via `uv run`, which may create per-server venvs against a
# uv-managed interpreter; if the alias `cpython-3.12-...-gnu` was recorded as an
# absolute path into the build host's HOME it dangles at runtime. Re-point each
# generic alias at its sibling versioned dir relatively so the image is
# self-contained regardless of where it was built.
RUN base="$HOME/.local/share/uv/python"; \
    if [ -d "$base" ]; then \
        for d in "$base"/cpython-3.*-linux-*-gnu; do \
            [ -d "$d" ] || continue; \
            v="$(basename "$d")"; \
            gen="$(echo "$v" | sed -E 's/^(cpython-3\.[0-9]+)\.[0-9]+(-.*)$/\1\2/')"; \
            if [ "$gen" != "$v" ]; then ln -sfn "$v" "$base/$gen"; fi; \
        done; \
    fi

# Fail here, naming the server, if any server in configs/mcp_servers lacks its
# entry point after the builds above: the built script, or the project venv and
# command its launch runs. The list comes from the configs that discovery and
# server.py launch, so a server added there is checked without editing this.
# (2>&1 keeps the error after the per-server lines, at the end of the log that a
# failed step shows; BuildKit can reorder stdout and stderr written together.)
COPY configs/ /app/configs/
COPY discover_tools.py /app/discover_tools.py
RUN python3 /app/discover_tools.py --check-entry-points 2>&1

# Create the eigent superuser (peer auth as the postgres OS user).
USER postgres
RUN service postgresql start && \
    psql -c "CREATE USER eigent WITH PASSWORD 'camel' SUPERUSER;" && \
    service postgresql stop
USER root

# Allow all local connections without password (trust auth) so subsequent
# build steps and the runtime env server can connect as eigent.
RUN PG_HBA=$(find /etc/postgresql -name pg_hba.conf) && \
    sed -i 's/peer$/trust/' "$PG_HBA" && \
    sed -i 's/scram-sha-256$/trust/' "$PG_HBA" && \
    sed -i 's/md5$/trust/' "$PG_HBA"

# High-concurrency Postgres tuning. With N workers each running 4-8 MCP server
# subprocesses per session, the default max_connections=100 is exhausted almost
# immediately under 128-wide rollouts ("sorry, too many clients already").
# This is the baked default; entrypoint.sh can override max_connections at
# runtime via OPENREWARD_PG_MAX_CONNECTIONS without a rebuild.
RUN PG_CONF=$(find /etc/postgresql -name postgresql.conf) && { \
        echo ""; \
        echo "# --- toolathlon high-concurrency tuning ---"; \
        echo "max_connections = 1000"; \
        echo "shared_buffers = 512MB"; \
        echo "work_mem = 8MB"; \
        echo "max_locks_per_transaction = 256"; \
        echo "listen_addresses = 'localhost'"; \
    } >> "$PG_CONF"

# Copy project files
WORKDIR /app
COPY tasks/ /app/tasks/
COPY db/ /app/db/
COPY runtime_patches/ /app/runtime_patches/
COPY server.py /app/server.py
COPY http_fixtures.py task_setup.py task_process.py /app/
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# Bake the seed and the MCP tool catalog into the image:
#   1. Restore init.sql.gz into `toolathlon_template`.
#   2. Run discover_tools.py against the template so tool_schemas.json ships
#      in the image (no runtime discovery, no runtime DB restore). It fails the
#      build when a server in configs/mcp_servers cannot be asked for its tools
#      or lists none: server.py offers a task only the tools in this catalog.
#   3. Mark the seed as a Postgres template so per-session DBs can clone it
#      with `CREATE DATABASE ... TEMPLATE toolathlon_template`.
RUN service postgresql start && \
    until pg_isready -U eigent 2>/dev/null; do sleep 0.5; done && \
    psql -U eigent -d postgres -c "CREATE DATABASE toolathlon_template OWNER eigent;" && \
    gunzip -c /app/db/init.sql.gz | psql -U eigent -d toolathlon_template -v ON_ERROR_STOP=1 && \
    psql -U eigent -d toolathlon_template -v ON_ERROR_STOP=1 -f /app/db/normalize_wc_categories.sql && \
    PGHOST=localhost PGDATABASE=toolathlon_template PG_DATABASE=toolathlon_template \
        python3 /app/discover_tools.py 2>&1 && \
    psql -U eigent -d postgres -c "UPDATE pg_database SET datistemplate = true WHERE datname = 'toolathlon_template';" && \
    service postgresql stop

EXPOSE 8080
CMD ["/app/entrypoint.sh"]
