# Copyright (c) 2026 Huawei Technologies Co., Ltd.
# All Rights Reserved.
#
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

import asyncio
import json
import os
import signal
from pathlib import Path

import uvicorn
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_rest_routes, create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCard
from fastapi import FastAPI, Request
from starlette.responses import JSONResponse
from google.protobuf.json_format import MessageToDict
from loguru import logger
from typing import List
from urllib.parse import urlparse, urlunparse

from common.custom import HandlerRegistry, InterfaceType
from orchestrate.registry_client.client_factory import AgentRegistryClientFactory
from orchestrate import AgentCardLoader
from orchestrate.workflow_storage_instance import get_workflow_storage
from samples.agents.energy_saving_agent import EnergySavingAgentExecutor
from samples.agents.energy_saving_intent_agent import EnergySavingIntentAgentExecutor
from samples.agents.live_streaming_agent import LiveStreamingAgentExecutor
from samples.agents.assurance_agent import AssuranceAgentExecutor
from samples.agents.ran_agent import RanAgentExecutor
from samples.agents.spn_domain_agent import SpnDomainAgentExecutor
from samples.agents.spn_domain_agent_city2 import SpnDomainAgentCity2Executor
from samples.agents.workbench_agent import WorkbenchAgentExecutor

import time as _time
import secrets as _secrets

# Global list to track all agent executors for graceful shutdown
_agent_executors = []






def _agent_card_to_dict(agent_card: AgentCard) -> dict:
    return MessageToDict(agent_card)


def _override_advertised_host(agent_card: AgentCard, host: str) -> None:
    """Rewrite the host each interface URL advertises (for registration and
    for other containers to call back), independent of what uvicorn binds to.
    Loopback-hardcoded sample agent cards otherwise only work when the caller
    shares the host with the agent process."""
    for iface in agent_card.supported_interfaces:
        if not iface.url:
            continue
        parsed = urlparse(iface.url)
        netloc = f"{host}:{parsed.port}" if parsed.port else host
        iface.url = urlunparse(parsed._replace(netloc=netloc))


def _is_agent_card_changed(local_dict: dict, remote_dict: dict) -> bool:
    local_normalized = json.dumps(local_dict, sort_keys=True, ensure_ascii=False)
    remote_normalized = json.dumps(remote_dict, sort_keys=True, ensure_ascii=False)
    return local_normalized != remote_normalized


async def register_or_update_agent(factory, agent_card: AgentCard) -> dict:
    local_dict = _agent_card_to_dict(agent_card)
    name = agent_card.name
    org = agent_card.provider.organization if agent_card.provider else ""
    try:
        existing = await factory.get(name, org)
    except Exception as e:
        logger.warning(f"Query registry for {name} failed: {e}, falling back to register")
        try:
            return await factory.register(agent_card)
        except Exception as reg_err:
            logger.error(f"Register agent card {name} failed: {reg_err}")
            return None

    if existing is None:
        try:
            result = await factory.register(agent_card)
            logger.info(f"Registered new agent card: {name} (org={org})")
            return result
        except Exception as e:
            logger.error(f"Register agent card {name} failed: {e}")
            return None

    remote_agent_cards = existing.get("agentCards", [])
    if remote_agent_cards:
        remote_dict = remote_agent_cards[0]
        if _is_agent_card_changed(local_dict, remote_dict):
            try:
                result = await factory.update_full(name, org, agent_card)
                logger.info(f"Updated agent card: {name} (org={org}), content changed")
                return result
            except Exception as e:
                logger.error(f"Update agent card {name} failed: {e}")
                return None
        else:
            logger.info(f"Agent card {name} (org={org}) already registered, no changes detected, skipped")
            return existing
    else:
        try:
            result = await factory.register(agent_card)
            logger.info(f"Registered agent card: {name} (org={org})")
            return result
        except Exception as e:
            logger.error(f"Register agent card {name} failed: {e}")
            return None


def pre_insert_psop():
    from common.util.config_util import get_conf
    if get_conf().get("persistence_mode", "file").lower() == "file":
        logger.info("Persistence mode is file, skipping pre_insert_psop")
        return

    storage = get_workflow_storage()
    for wf_id in storage.list_psops():
        psop = storage.load_psop(wf_id)
        if psop is None:
            logger.warning(f"pre_insert_psop: workflow {wf_id} not found, skipping")
            continue
        save_handle = HandlerRegistry.get_handler(InterfaceType.SAVE_PSOP)
        save_handle.handle(psop)


async def start_server(agent_card: AgentCard, port: int, host: str = "127.0.0.1") -> None:
    agent2class = {
        "RAN Energy Saving Agent": EnergySavingAgentExecutor,
        "Energy Saving Intent Agent": EnergySavingIntentAgentExecutor,
        "Live Streaming Agent": LiveStreamingAgentExecutor,
        "Assurance Agent": AssuranceAgentExecutor,
        "RAN Agent": RanAgentExecutor,
        "Transport Workbench Agent": WorkbenchAgentExecutor,
        "SPN Domain Agent City1": SpnDomainAgentExecutor,
        "SPN Domain Agent City2": SpnDomainAgentCity2Executor
    }
    agent_name = agent_card.name
    agent_class = agent2class.get(agent_name)

    if not agent_class:
        logger.info(f"Skipping external agent '{agent_name}': no local executor class defined")
        return

    try:
        agent_impl = agent_class()
        _agent_executors.append(agent_impl)
    except Exception as e:
        logger.error(f"Failed to initialize agent '{agent_name}': {e}")
        return

    request_handler = DefaultRequestHandler(
        agent_executor=agent_impl,
        task_store=InMemoryTaskStore(),
        agent_card=agent_card
    )

    app = FastAPI()

    # --- Auth support: login endpoint for agents declaring securitySchemes ---
    _VALID_TOKENS = {}  # token -> expiry timestamp

    has_security = agent_card.security_schemes and agent_card.security_requirements
    if has_security:
        login_path = "/rest/plat/smapp/v1/oauth/token"
        logger.info(f"Agent '{agent_name}' auth login endpoint: {login_path}")

        @app.api_route(login_path, methods=["PUT", "POST"])
        async def _agent_login(request: Request):
            """Mock login: accept fixed credentials, return accessSession."""
            body = {}
            ct = request.headers.get("content-type", "")
            if "json" in ct:
                try:
                    body = await request.json()
                except Exception:
                    body = {}
            else:
                form = await request.form()
                body = dict(form)
            username = body.get("userName") or body.get("username")
            password = body.get("value") or body.get("password")
            if username == "admin" and password == "Admin@123":
                token = _secrets.token_urlsafe(24)
                _VALID_TOKENS[token] = _time.time() + 3600
                logger.info(f"[Auth] Login succeeded for agent '{agent_name}', token issued")
                return {"accessSession": token}
            logger.warning(f"[Auth] Login failed for agent '{agent_name}': bad credentials")
            return JSONResponse(status_code=401, content={"error": "Invalid credentials"})


    agent_card_routes = create_agent_card_routes(agent_card=agent_card)
    app.routes.extend(agent_card_routes)

    for iface in agent_card.supported_interfaces:
        if not iface.url:
            continue
        parsed = urlparse(iface.url)
        path = parsed.path.rstrip("/") or ""
        if iface.protocol_binding == "JSONRPC":
            jsonrpc_routes = create_jsonrpc_routes(request_handler=request_handler, rpc_url=path)
            app.routes.extend(jsonrpc_routes)
            logger.info(f"Agent '{agent_name}' JSONRPC endpoint: {path}")
        elif iface.protocol_binding == "HTTP+JSON":
            rest_routes = create_rest_routes(request_handler=request_handler, path_prefix=path)
            app.routes.extend(rest_routes)
            logger.info(f"Agent '{agent_name}' REST endpoint: {path}")

    # Enable HTTPS using the orchestration center self-signed certificate so the
    # agent server matches the https:// URLs declared in the agent card.
    ssl_dir = Path(__file__).resolve().parent.parent / "etc" / "ssl"
    cert_path = ssl_dir / "server.cer"
    key_path = ssl_dir / "server_key.pem"
    nopass_key_path = ssl_dir / "server_key_nopass.pem"
    ssl_kwargs = {}
    if cert_path.is_file() and key_path.is_file():
        # Prefer the unencrypted key to avoid "Enter PEM pass phrase:" prompts
        actual_key = nopass_key_path if nopass_key_path.is_file() else key_path
        ssl_kwargs = {"ssl_certfile": str(cert_path), "ssl_keyfile": str(actual_key)}
        if not nopass_key_path.is_file():
            pwd_path = ssl_dir / "cert_pwd"
            if pwd_path.is_file():
                ssl_kwargs["ssl_keyfile_password"] = pwd_path.read_text(encoding="utf-8").strip()
        logger.info(f"Agent {agent_name!r} starting with HTTPS (cert={cert_path.name})")
    else:
        logger.warning(f"Agent {agent_name!r} SSL certs not found at {ssl_dir}, starting HTTP")
    config = uvicorn.Config(app, host=host, port=port, timeout_graceful_shutdown=2, **ssl_kwargs)
    uvicorn_server = uvicorn.Server(config)
    try:
        await uvicorn_server.serve()
    except (SystemExit, asyncio.CancelledError):
        pass


async def main() -> None:
    try:
        pre_insert_psop()
    except Exception as e:
        logger.error(f"pre_insert_psop failed (agents will still start): {e}")

    try:
        agent_lib = AgentCardLoader(Path(__file__).parent / "agentcard")
        agent_cards = agent_lib.get_all_agent_cards()
    except Exception as e:
        logger.error(f"Failed to load agent cards: {e}")
        return

    # SAMPLE_AGENTS_HOST lets these cards advertise a Docker service name (or
    # any reachable host) instead of the hardcoded 127.0.0.1 from the JSON,
    # so a container other than this one can reach and register them. Unset,
    # behavior is unchanged (loopback, same-host processes).
    advertise_host = os.environ.get("SAMPLE_AGENTS_HOST", "").strip()
    if advertise_host:
        for agent_card in agent_cards:
            _override_advertised_host(agent_card, advertise_host)
        logger.info(f"Advertising sample agent cards on host '{advertise_host}'")

    factory = None
    try:
        factory = AgentRegistryClientFactory().create_from_env()
    except Exception as e:
        logger.warning(f"Failed to create registry client (agents will start without registration): {e}")

    tasks: List[asyncio.Task] = []
    for agent_card in agent_cards:
        if factory:
            try:
                result = await register_or_update_agent(factory, agent_card)
                logger.info(f"register/update agentcard for {agent_card.name}, result is {result}")
            except Exception as e:
                logger.error(f"register/update agent card failed: {e}")
        agent_name = agent_card.name
        if not agent_card.supported_interfaces:
            logger.warning(f"Skipping agent '{agent_name}': no supported interfaces")
            continue
        parsed = urlparse(agent_card.supported_interfaces[0].url)
        # Bind to all interfaces when advertising a different host (a Docker
        # service name isn't a local bind address); otherwise bind exactly
        # what the card says, matching prior same-host behavior.
        bind_host = "0.0.0.0" if advertise_host else parsed.hostname
        task = asyncio.create_task(
            start_server(agent_card, port=parsed.port, host=bind_host),
            name=f"server_{agent_name}"
        )
        tasks.append(task)
        logger.info(f"Starting server for '{agent_name}' on {agent_card.supported_interfaces[0].url}")
    
    # Set up signal handlers for graceful shutdown
    loop = asyncio.get_running_loop()
    shutdown_event = asyncio.Event()
    
    def signal_handler():
        logger.info("Shutdown signal received, stopping all servers...")
        shutdown_event.set()
    
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, signal_handler)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            pass
    
    try:
        # Wait for either all tasks to complete or shutdown signal
        done, pending = await asyncio.wait(
            tasks,
            return_when=asyncio.FIRST_EXCEPTION,
            timeout=None
        )
        
        # If we get here due to shutdown signal or exception, cancel pending tasks
        if shutdown_event.is_set() or pending:
            logger.info("Shutting down all servers...")
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            logger.info("All servers stopped")
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received, shutting down...")
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("All servers stopped")
    finally:
        # Shutdown all agent executors
        logger.info(f"Shutting down {len(_agent_executors)} agent executors...")
        for executor in _agent_executors:
            if hasattr(executor, 'shutdown'):
                try:
                    executor.shutdown()
                except Exception as e:
                    logger.warning(f"Error shutting down executor {executor.__class__.__name__}: {e}")
        # Give executors a moment to clean up
        await asyncio.sleep(0.5)
        logger.info("All agent executors shut down")


if __name__ == "__main__":
    asyncio.run(main())
