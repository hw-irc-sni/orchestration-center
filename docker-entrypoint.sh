#!/bin/bash
set -e

APP_HOME="${APP_HOME:-/opt/orchestration-center}"
cd "$APP_HOME"

export PATH="/opt/venv/bin:$PATH"

# ─────────────────────────────────────────────────────────────────────
# Cloud Run / Kubernetes environment variable → config file override
# The application reads from config files, not env vars.
# This bridge writes env var values into the config files so they
# take effect at runtime.
# ─────────────────────────────────────────────────────────────────────

SERVER_CONF="etc/conf/server.conf"
DB_CONF="etc/conf/db_config.json"

# --- server.conf overrides (using # as sed delimiter to handle paths safely) ---
if [ -n "${ORCH_IP}" ]; then
    sed -i "s#^ip=.*#ip=${ORCH_IP}#" "${SERVER_CONF}"
    echo "Config override: ip=${ORCH_IP}"
fi

# Cloud Run injects PORT env var
if [ -n "${PORT}" ]; then
    sed -i "s#^port=.*#port=${PORT}#" "${SERVER_CONF}"
    echo "Config override: port=${PORT} (Cloud Run)"
elif [ -n "${ORCH_PORT}" ]; then
    sed -i "s#^port=.*#port=${ORCH_PORT}#" "${SERVER_CONF}"
    echo "Config override: port=${ORCH_PORT}"
fi

if [ -n "${ORCH_ENABLE_HTTPS}" ]; then
    sed -i "s#^enable_https=.*#enable_https=${ORCH_ENABLE_HTTPS}#" "${SERVER_CONF}"
    echo "Config override: enable_https=${ORCH_ENABLE_HTTPS}"
fi

if [ -n "${ORCH_FORWARDED_ALLOW_IPS}" ]; then
    sed -i "s#^forwarded_allow_ips=.*#forwarded_allow_ips=\"${ORCH_FORWARDED_ALLOW_IPS}\"#" "${SERVER_CONF}"
    echo "Config override: forwarded_allow_ips=${ORCH_FORWARDED_ALLOW_IPS}"
fi

if [ -n "${AGENT_REGISTRY_URL}" ]; then
    sed -i "s#^agent_registry_url=.*#agent_registry_url=${AGENT_REGISTRY_URL}#" "${SERVER_CONF}"
    echo "Config override: agent_registry_url=${AGENT_REGISTRY_URL}"
fi

# When HTTPS is disabled, also disable cert verification
if [ "${ORCH_ENABLE_HTTPS}" = "false" ]; then
    sed -i "s#^verify_client=.*#verify_client=false#" "${SERVER_CONF}"
    echo "Config override: HTTPS disabled -> verify_client=false"
fi

# --- db_config.json overrides ---
if [ -n "${PERSISTENCE_MODE}" ]; then
    sed -i "s#^persistence_mode=.*#persistence_mode=${PERSISTENCE_MODE}#" "${SERVER_CONF}"
    echo "Config override: persistence_mode=${PERSISTENCE_MODE}"
fi

if [ -n "${DB_HOST}" ] || [ -n "${DB_PORT}" ] || [ -n "${DB_NAME}" ] || [ -n "${DB_USERNAME}" ] || [ -n "${DB_PASSWORD}" ]; then
    python3 -c "
import json, os
path = '${DB_CONF}'
with open(path, 'r') as f:
    cfg = json.load(f)
if os.environ.get('DB_HOST'):     cfg['host']     = os.environ['DB_HOST']
if os.environ.get('DB_PORT'):     cfg['port']     = os.environ['DB_PORT']
if os.environ.get('DB_NAME'):     cfg['database'] = os.environ['DB_NAME']
if os.environ.get('DB_USERNAME'): cfg['user']     = os.environ['DB_USERNAME']
if os.environ.get('DB_PASSWORD'): cfg['password'] = os.environ['DB_PASSWORD']
with open(path, 'w') as f:
    json.dump(cfg, f, indent=2)
"
    echo "Config override: db_config.json updated from environment variables"
fi

# --- LLM configuration ---
# No bridge needed: LLM_<CAPABILITY>_<FIELD> variables (LLM_CHAT_PROVIDER, LLM_CHAT_MODEL,
# LLM_CHAT_API_KEY, LLM_CHAT_URL, LLM_CHAT_BASE_URL, ...) are read straight from the
# environment by common/llm/config/env_overrides.py, and the a2a-t-sdk env file at
# etc/conf/a2at.env is regenerated from the resolved config on every startup. Nothing
# is written into llm_config.json, so no secret lands in a file inside the image.

# Ensure required directories exist
mkdir -p log run data

if [ "${1}" = "serve" ]; then
    echo "Starting orchestration-center service..."
    exec python3 -m orchestrate.start
fi

exec "$@"