// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// All Rights Reserved.
//
// SPDX-License-Identifier: Apache-2.0
//
//    Licensed under the Apache License, Version 2.0 (the "License"); you may
//    not use this file except in compliance with the License. You may obtain
//    a copy of the License at
//
//         http://www.apache.org/licenses/LICENSE-2.0
//
//    Unless required by applicable law or agreed to in writing, software
//    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
//    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
//    License for the specific language governing permissions and limitations
//    under the License.
import axios from "axios";

const STORAGE_KEY = 'server_config';
export const defaultIp = '127.0.0.1';
export const defaultPort = '5001';
export const defaultGateway = '/api/orchestrate';
 
 const TOKEN_KEY = 'access_token';
 
 export const getAuthToken = () => localStorage.getItem(TOKEN_KEY);
 export const setAuthToken = (token) => {
     if (token) {
         localStorage.setItem(TOKEN_KEY, token);
     } else {
         localStorage.removeItem(TOKEN_KEY);
    }
};

 // Protocol follows the current page: HTTPS page -> https://, otherwise http://
export const defaultProtocol = window.location.protocol === 'https:' ? 'https://' : 'http://';

const trimTrailingSlash = (url) => url.replace(/\/$/, '');

const isStandardPort = () => {
    const p = window.location.port;
    return !p || p === '80' || p === '443';
};

const isLocalHost = () => ['localhost', '127.0.0.1', '::1'].includes(window.location.hostname);

// A remote client can never reach a backend at 127.0.0.1 (the browser's own
// loopback), so direct-IP mode is only a safe unconfigured default when the
// page itself was loaded from localhost (the local `npm run dev` workflow).
// Anywhere else (including this project's own docker-compose deployment,
// which serves the nginx-fronted UI on the non-standard port 3003) must
// default to the nginx gateway path instead.
export const shouldDefaultToGateway = () => isStandardPort() || !isLocalHost();

export const getBaseUrl = () => {
    try {
        const saved = localStorage.getItem(STORAGE_KEY);
        if (saved) {
            const config = JSON.parse(saved);
          if (config.mode === 'ip') {
              const ip = config.ip || defaultIp;
              const port = config.port || defaultPort;
                return `${defaultProtocol}${ip}:${port}`;
           }
            return trimTrailingSlash(config.nginxUrl || config.gatewayUrl || defaultGateway);
        }
       if (shouldDefaultToGateway()) {
           return trimTrailingSlash(defaultGateway);
       }
        return `${defaultProtocol}${defaultIp}:${defaultPort}`;
   } catch (e) {
        return `${defaultProtocol}${defaultIp}:${defaultPort}`;
   }
}

const ORCHESTRATE_BASE = () => `${getBaseUrl()}/rest/v1/orchestrate`;

const api = axios.create({ timeout: 120000 });

 // Inject auth token into every request
 api.interceptors.request.use((config) => {
     const token = getAuthToken();
     if (token) {
         config.headers.Authorization = `Bearer ${token}`;
     }
     return config;
 });
 
api.interceptors.response.use(
    (response) => response.data,
    (error) => {
        if (error.response && error.response.status === 401) {
            setAuthToken(null);
            window.dispatchEvent(new Event('auth-expired'));
        }
        return Promise.reject(error);
    }
);

// ──── Agent Cards ────

export async function getAgentCards() {
    return api.get(`${ORCHESTRATE_BASE()}/agent-cards`);
}

// ──── Workflow CRUD ────

export async function getWorkflow() {
    return api.get(`${ORCHESTRATE_BASE()}/workflows`);
}

export async function getWorkflowById(id) {
    return api.get(`${ORCHESTRATE_BASE()}/workflows/${id}`);
}

export async function delWorkflowById(id) {
    return api.delete(`${ORCHESTRATE_BASE()}/workflows/${id}`);
}

export async function createWorkflow(data) {
    return api.post(`${ORCHESTRATE_BASE()}/workflows`, { psop: data });
}

// ──── Workflow Templates ────

export async function getTemplates() {
    return api.get(`${ORCHESTRATE_BASE()}/templates`);
}

export async function importTemplate(templateId) {
    return api.post(`${ORCHESTRATE_BASE()}/templates/${templateId}/import`);
}

// ──── PDF Parsing ────

export async function parsePdf(file) {
    const formData = new FormData();
    formData.append('file', file);
    const body = await api.post(`${ORCHESTRATE_BASE()}/parse-pdf`, formData);
    return body.data;
}

// ──── BPMN Parsing ────

export async function parseBpmn(file, processId) {
    const formData = new FormData();
    formData.append('file', file);
    const query = processId ? `?process_id=${encodeURIComponent(processId)}` : '';
    const body = await api.post(`${ORCHESTRATE_BASE()}/parse-bpmn${query}`, formData);
    return body.data;
}

// ──── Workflow Generation ────

export async function handlePlan(preflow, agentCards) {
    const body = await api.post(`${ORCHESTRATE_BASE()}/generate-from-preflow`, {
        preflow: preflow,
        agent_cards: agentCards
    });
    return body.data;
}

export async function generateWorkflowFromIntent(intent, name = "Generated Workflow") {
    const body = await api.post(`${ORCHESTRATE_BASE()}/generate-from-intent`, {
        user_intent: intent,
        workflow_name: name
    });
    return body.data || body;
}

export async function matchWorkflows(intent) {
    const body = await api.post(`${ORCHESTRATE_BASE()}/retrieve-by-intent`, {
        user_intent: intent,
    });
    const data = body.data;
    if (!data) return [];
    const list = Array.isArray(data) ? data : [data];
    return list.map(item => ({
        workflow_id: item.id || item.workflow_id,
        name: item.name || item.workflow_name,
        description: item.description,
        tags: item.tags || []
    }));
}

export async function matchWorkflowsTopN(intent, topN = 3) {
    const body = await api.post(`${ORCHESTRATE_BASE()}/retrieve-topn-by-intent`, {
        user_intent: intent,
        top_n: topN
    });
    const data = body.data;
    if (!data) return [];
    return (Array.isArray(data) ? data : [data]).map(item => ({
        workflow_id: item.workflow_id,
        name: item.name,
        description: item.description,
        tags: item.tags || [],
        score: item.score
    }));
}

// ──── Workflow Execution ────

export function getStartProcessStreamUrl(psopId, userIntent = '', lang = '') {
    const base = `${ORCHESTRATE_BASE()}/execute?psop_id=${psopId}`;
    const params = [];
    const token = getAuthToken();
    if (token) {
        params.push(`access_token=${encodeURIComponent(token)}`);
    }
    if (userIntent) {
        params.push(`user_intent=${encodeURIComponent(userIntent)}`);
    }
    if (lang) {
        params.push(`lang=${encodeURIComponent(lang)}`);
    }
    if (params.length > 0) {
        return `${base}&${params.join('&')}`;
    }
    return base;
}

// ──── Execution Records ────

export async function getExecutionRecords() {
    return api.get(`${ORCHESTRATE_BASE()}/execution-records`);
}

export async function getExecutionRecord(executionId) {
    return api.get(`${ORCHESTRATE_BASE()}/execution-records/${executionId}`);
}

export async function deleteExecutionRecord(executionId) {
    return api.delete(`${ORCHESTRATE_BASE()}/execution-records/${executionId}`);
}
 
 // ---- Access authentication ----
 
export async function authCheck() {
    const resp = await api.get(`${ORCHESTRATE_BASE()}/auth/check`);
    return resp.data;
}
 
async function sha256(text) {
    const data = new TextEncoder().encode(text);
    const hash = await crypto.subtle.digest('SHA-256', data);
    return Array.from(new Uint8Array(hash)).map(b => b.toString(16).padStart(2, '0')).join('');
}

export async function login(username, password) {
    const hashed = await sha256(password);
    const body = await api.post(`${ORCHESTRATE_BASE()}/auth/login`, { username, password: hashed });
    if (body.data && body.data.token) {
         setAuthToken(body.data.token);
     }
     return body.data;
 }
 
export async function logout() {
    try {
        await api.post(`${ORCHESTRATE_BASE()}/auth/logout`);
    } finally {
        setAuthToken(null);
    }
}
 
export async function register(username, password) {
    const hashed = await sha256(password);
    const body = await api.post(`${ORCHESTRATE_BASE()}/auth/register`, { username, password: hashed });
    return body.data;
}
 
export async function listUsers() {
    const resp = await api.get(`${ORCHESTRATE_BASE()}/auth/users`);
    return resp.data;
}
 
export async function deleteUser(username) {
    return api.delete(`${ORCHESTRATE_BASE()}/auth/users/${username}`);
}

export async function changePassword(oldPassword, newPassword) {
    const oldHash = await sha256(oldPassword);
    const newHash = await sha256(newPassword);
    return api.post(`${ORCHESTRATE_BASE()}/auth/change-password`, {
        old_password: oldHash,
        new_password: newHash,
    });
}
