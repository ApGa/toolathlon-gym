#!/bin/bash
set -e

# =============================================================================
# Toolathlon Gym launcher (in-container scale-out).
#
# A single uvicorn process orchestrating all sessions (each spawning 4-8 MCP
# subprocesses) is the throughput bottleneck under high concurrency. Instead we
# run N worker copies of server.py on internal ports behind nginx, which routes
# every request to the worker that owns the session via consistent hashing on
# the X-Session-ID header (ORS session state is in-process, so affinity is
# mandatory). One shared, tuned PostgreSQL backs all workers; per-session DBs
# are namespaced by uuid so they never collide.
#
# Tunables (env):
#   OPENREWARD_PORT / PORT          public port nginx listens on (default 8080)
#   OPENREWARD_WORKERS              worker count (default: clamp(nproc, 4..16))
#   OPENREWARD_WORKER_BASE_PORT     first internal worker port (default 8100)
#   OPENREWARD_PG_MAX_CONNECTIONS   postgres max_connections (default 1000)
#   OPENREWARD_FROZEN_DATE          freeze "today" as YYYY-MM-DD (rail/date tasks)
# =============================================================================

PUBLIC_PORT="${OPENREWARD_PORT:-${PORT:-8080}}"
BASE_PORT="${OPENREWARD_WORKER_BASE_PORT:-8100}"
PG_MAX_CONN="${OPENREWARD_PG_MAX_CONNECTIONS:-1000}"

WORKERS="${OPENREWARD_WORKERS:-0}"
if ! [ "$WORKERS" -ge 1 ] 2>/dev/null; then
    n="$(nproc 2>/dev/null || echo 4)"
    WORKERS="$n"
    [ "$WORKERS" -lt 4 ] && WORKERS=4
    [ "$WORKERS" -gt 16 ] && WORKERS=16
fi

echo "[entrypoint] public_port=${PUBLIC_PORT} workers=${WORKERS} base_port=${BASE_PORT} pg_max_connections=${PG_MAX_CONN} frozen_date=${OPENREWARD_FROZEN_DATE:-<wall-clock>}"

# --- Postgres hardening for remapped-uid runtimes (enroot/pyxis) -------------
# Under a single-UID user namespace the data dir / ssl key can appear root-owned
# to the postgres backend, which then refuses to start. Make ownership/perms
# correct defensively so no external `unshare`/`chmod` wrapper is needed.
chown -R postgres:postgres /var/lib/postgresql 2>/dev/null || true
chown -R postgres:postgres /etc/ssl/private 2>/dev/null || true
chmod 0600 /etc/ssl/private/ssl-cert-snakeoil.key 2>/dev/null || true

# --- Apply runtime max_connections override ----------------------------------
PG_CONF="$(find /etc/postgresql -name postgresql.conf | head -1)"
if [ -n "$PG_CONF" ]; then
    if grep -qE '^[[:space:]]*max_connections[[:space:]]*=' "$PG_CONF"; then
        sed -i "s/^[[:space:]]*max_connections[[:space:]]*=.*/max_connections = ${PG_MAX_CONN}/" "$PG_CONF"
    else
        echo "max_connections = ${PG_MAX_CONN}" >> "$PG_CONF"
    fi
fi

# --- Start PostgreSQL --------------------------------------------------------
service postgresql start
until pg_isready -U eigent 2>/dev/null; do sleep 1; done

# --- GC any leftover per-session DBs (crash recovery for long-lived pods) ----
psql -U eigent -d postgres -tAc \
    "SELECT datname FROM pg_database WHERE datname LIKE 's\\_%' ESCAPE '\\'" |
    while IFS= read -r db; do
        [ -z "$db" ] && continue
        echo "[entrypoint] dropping orphan DB: $db"
        psql -U eigent -d postgres -c "DROP DATABASE IF EXISTS \"$db\" WITH (FORCE);" || true
    done

# --- Launch worker processes -------------------------------------------------
pids=()
upstream_servers=""
for i in $(seq 0 $((WORKERS - 1))); do
    p=$((BASE_PORT + i))
    echo "[entrypoint] starting worker $i on 127.0.0.1:${p}"
    # Unset OPENREWARD_PORT so each worker binds its own internal PORT; nginx
    # owns the public port.
    env -u OPENREWARD_PORT PORT="$p" python3 /app/server.py &
    pids+=("$!")
    upstream_servers="${upstream_servers}        server 127.0.0.1:${p} max_fails=0;
"
done

# --- Render nginx config with session-affinity routing -----------------------
mkdir -p /run /var/log/nginx
cat > /etc/nginx/nginx.conf <<EOF
user root;
worker_processes auto;
pid /run/nginx.pid;
error_log /dev/stderr warn;
events { worker_connections 8192; }
http {
    access_log off;
    # ORS sessions are stateful and live in one worker's memory, so all
    # requests for a session id must hit the same backend.
    upstream ors_backends {
        hash \$http_x_session_id consistent;
${upstream_servers}    }
    server {
        listen ${PUBLIC_PORT};
        client_max_body_size 0;
        location / {
            proxy_pass http://ors_backends;
            proxy_http_version 1.1;
            proxy_set_header Connection "";
            proxy_set_header Host \$host;
            # SSE/streaming endpoints (/call, /create_session): no buffering.
            proxy_buffering off;
            proxy_cache off;
            proxy_read_timeout 3600s;
            proxy_send_timeout 3600s;
            chunked_transfer_encoding off;
        }
    }
}
EOF

nginx -t

# --- Supervise: bring the container down if any worker or nginx dies ---------
shutdown() {
    echo "[entrypoint] shutting down"
    nginx -s quit 2>/dev/null || true
    kill "${pids[@]}" 2>/dev/null || true
    exit 0
}
trap shutdown TERM INT

echo "[entrypoint] starting nginx on :${PUBLIC_PORT}"
nginx -g 'daemon off;' &
pids+=("$!")

# If any supervised process exits, tear the rest down so the orchestrator
# restarts cleanly rather than serving from a half-dead container.
wait -n "${pids[@]}"
code=$?
echo "[entrypoint] a supervised process exited (code=${code}); shutting down"
nginx -s quit 2>/dev/null || true
kill "${pids[@]}" 2>/dev/null || true
exit "$code"
