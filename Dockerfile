# Copyright (c) 2026 Huawei Technologies Co., Ltd.
# All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

# OpenAN Orchestration Center Container Image
# Multi-stage build for OpenShift / Kubernetes / Cloud Run deployment.
#
# Build:
#   docker build -t orchestration-center:latest .
#
# Run (local, file persistence):
#   docker run -p 5001:5001 orchestration-center:latest
#
# Run (local, PostgreSQL):
#   docker run -e PERSISTENCE_MODE=postgresql \
#     -e DB_HOST=host -e DB_USERNAME=user -e DB_PASSWORD=pass \
#     -p 5001:5001 orchestration-center:latest

FROM python:3.12-slim AS builder

USER root

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libc6-dev \
    libpq-dev \
    libssl-dev \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt

RUN python3 -m venv /opt/venv --copies \
    && . /opt/venv/bin/activate \
    && pip install --no-cache-dir --default-timeout=180 --retries=10 -r /tmp/requirements.txt \
    && rm -rf /tmp/requirements.txt /root/.cache/pip

FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends bash libpq5 && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    ORCH_IP=0.0.0.0 \
    ORCH_PORT=5001 \
    ORCH_ENABLE_HTTPS=false \
    ORCH_FORWARDED_ALLOW_IPS="*" \
    PERSISTENCE_MODE=file

COPY . /opt/orchestration-center/

RUN useradd -m appuser \
    && mkdir -p /opt/orchestration-center/log /opt/orchestration-center/run /opt/orchestration-center/data \
    && mkdir -p /opt/orchestration-center/etc/ssl \
    && chmod +x /opt/orchestration-center/bin/*.sh /opt/orchestration-center/docker-entrypoint.sh \
    && chown -R appuser:appuser /opt/orchestration-center /opt/venv

WORKDIR /opt/orchestration-center

USER appuser

EXPOSE 5001

ENTRYPOINT ["/opt/orchestration-center/docker-entrypoint.sh"]
CMD ["serve"]