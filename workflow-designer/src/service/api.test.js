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
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import axios from 'axios';
import {
  getBaseUrl,
  shouldDefaultToGateway,
  defaultIp,
  defaultPort,
  defaultGateway,
  getAgentCards,
  getWorkflow,
  getWorkflowById,
  createWorkflow,
  delWorkflowById,
  getTemplates,
  importTemplate,
  parsePdf,
  parseBpmn,
  handlePlan,
  generateWorkflowFromIntent,
  getStartProcessStreamUrl,
  matchWorkflows,
  getExecutionRecords,
  getExecutionRecord,
  deleteExecutionRecord
} from './api';

// Mock axios
vi.mock('axios', () => {
  const mockApi = {
    get: vi.fn(),
    post: vi.fn(),
    delete: vi.fn(),
    interceptors: {
      request: { use: vi.fn() },
      response: { use: vi.fn() }
    }
  };
  return {
    default: {
      create: vi.fn(() => mockApi),
      get: vi.fn(),
      post: vi.fn(),
      delete: vi.fn(),
      interceptors: {
        request: { use: vi.fn() },
        response: { use: vi.fn() }
      }
    }
  };
});

describe('api service', () => {
  const mockLocalStorage = (() => {
    let store = {};
    return {
      getItem: vi.fn((key) => store[key] || null),
      setItem: vi.fn((key, value) => { store[key] = value.toString(); }),
      removeItem: vi.fn((key) => { delete store[key]; }),
      clear: vi.fn(() => { store = {}; }),
    };
  })();

  beforeEach(() => {
    vi.stubGlobal('localStorage', mockLocalStorage);
    vi.clearAllMocks();
    mockLocalStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  describe('getBaseUrl', () => {
    it('should return default URL when localStorage is empty', () => {
      const url = getBaseUrl();
      expect(url).toBe(`http://${defaultIp}:${defaultPort}`);
    });

    it('should return custom URL when localStorage has config', () => {
      mockLocalStorage.setItem('server_config', JSON.stringify({ mode: 'ip', ip: '192.168.1.1', port: '8080' }));
      const url = getBaseUrl();
      expect(url).toBe('http://192.168.1.1:8080');
    });

    it('should return default IP if port is missing in config', () => {
      mockLocalStorage.setItem('server_config', JSON.stringify({ mode: 'ip', ip: '192.168.1.1' }));
      const url = getBaseUrl();
      expect(url).toBe(`http://192.168.1.1:${defaultPort}`);
    });

    it('should use independent nginx URL when mode is nginx', () => {
      mockLocalStorage.setItem('server_config', JSON.stringify({
        mode: 'nginx',
        ip: '192.168.1.1',
        port: '8080',
        nginxUrl: 'http://gateway.example.com/orchestration/'
      }));
      const url = getBaseUrl();
      expect(url).toBe('http://gateway.example.com/orchestration');
    });

    it('should use default gateway when nginx URL is missing', () => {
      mockLocalStorage.setItem('server_config', JSON.stringify({
        mode: 'nginx',
        ip: '192.168.1.1',
        port: '8080'
      }));
      const url = getBaseUrl();
      expect(url).toBe(defaultGateway);
    });

    it('should handle malformed JSON in localStorage', () => {
      mockLocalStorage.setItem('server_config', 'invalid json');
      const url = getBaseUrl();
      expect(url).toBe(`http://${defaultIp}:${defaultPort}`);
    });

    describe('with no saved config, on a non-standard port', () => {
      const originalLocation = window.location;

      const setHostname = (hostname) => {
        Object.defineProperty(window, 'location', {
          value: { ...originalLocation, hostname, port: '3003', protocol: 'http:' },
          configurable: true,
        });
      };

      afterEach(() => {
        Object.defineProperty(window, 'location', { value: originalLocation, configurable: true });
      });

      it('falls back to the nginx gateway for a remote hostname (regression: was defaulting to 127.0.0.1, unreachable from a real client)', () => {
        setHostname('10.220.239.88');
        expect(shouldDefaultToGateway()).toBe(true);
        expect(getBaseUrl()).toBe(defaultGateway);
      });

      it('still uses direct-IP mode when loaded from localhost (local `npm run dev` workflow)', () => {
        setHostname('localhost');
        expect(shouldDefaultToGateway()).toBe(false);
        expect(getBaseUrl()).toBe(`http://${defaultIp}:${defaultPort}`);
      });

      it('still uses direct-IP mode when loaded from 127.0.0.1', () => {
        setHostname('127.0.0.1');
        expect(shouldDefaultToGateway()).toBe(false);
        expect(getBaseUrl()).toBe(`http://${defaultIp}:${defaultPort}`);
      });
    });

    describe('with no saved config, on a standard port', () => {
      const originalLocation = window.location;

      afterEach(() => {
        Object.defineProperty(window, 'location', { value: originalLocation, configurable: true });
      });

      it('uses the nginx gateway for a non-localhost hostname served on port 443, regardless of host', () => {
        Object.defineProperty(window, 'location', {
          value: { ...originalLocation, hostname: 'orchestration.example.com', port: '443', protocol: 'https:' },
          configurable: true,
        });
        expect(shouldDefaultToGateway()).toBe(true);
        expect(getBaseUrl()).toBe(defaultGateway);
      });
    });
  });

  describe('API requests using the api instance', () => {
    // Note: The 'api' instance is created inside api.js. 
    // Since vi.mock('axios') is hoisted, axios.create will return our mockApi.
    // However, the interceptors are applied at module load time.

    it('getAgentCards should call api.get with correct URL', async () => {
      const mockApi = axios.create();
      mockApi.get.mockResolvedValue({ data: 'cards' });

      await getAgentCards();
      expect(mockApi.get).toHaveBeenCalledWith(expect.stringContaining('/agent-cards'));
    });

    it('getWorkflow should call api.get with correct URL', async () => {
      const mockApi = axios.create();
      mockApi.get.mockResolvedValue({ data: 'workflows' });

      await getWorkflow();
      expect(mockApi.get).toHaveBeenCalledWith(expect.stringContaining('/rest/v1/orchestrate/workflows'));
    });

    it('getWorkflowById should call api.get with correct URL', async () => {
      const mockApi = axios.create();
      mockApi.get.mockResolvedValue({ data: 'workflow' });
      const testId = '123';

      await getWorkflowById(testId);
      expect(mockApi.get).toHaveBeenCalledWith(expect.stringContaining(`/rest/v1/orchestrate/workflows/${testId}`));
    });

    it('createWorkflow should call api.post with correct URL and data', async () => {
      const mockApi = axios.create();
      mockApi.post.mockResolvedValue({ data: 'created' });
      const testData = { name: 'New Workflow' };

      await createWorkflow(testData);
      expect(mockApi.post).toHaveBeenCalledWith(expect.stringContaining('/rest/v1/orchestrate/workflows'), { psop: testData });
    });

    it('delWorkflowById should call api.delete with correct URL', async () => {
      const mockApi = axios.create();
      mockApi.delete.mockResolvedValue({ data: 'ok' });
      const testId = 'abc-123';

      await delWorkflowById(testId);
      expect(mockApi.delete).toHaveBeenCalledWith(expect.stringContaining(`/rest/v1/orchestrate/workflows/${testId}`));
    });
  });

  describe('More api instance requests', () => {
    it('parsePdf should handle successful response', async () => {
      const mockApi = axios.create();
      const mockFile = new File([''], 'test.pdf', { type: 'application/pdf' });
      const mockContent = { key: 'value' };
      mockApi.post.mockResolvedValue({ status: 'success', data: mockContent });

      const result = await parsePdf(mockFile);
      expect(result).toEqual({ key: 'value' });
      // No explicit Content-Type: passing a FormData body lets axios generate
      // the multipart boundary itself. A manually-set header without one
      // would break multipart parsing server-side.
      expect(mockApi.post).toHaveBeenCalledWith(
        expect.stringContaining('/rest/v1/orchestrate/parse-pdf'),
        expect.any(FormData)
      );
    });

    it('parsePdf should throw error when status is not success', async () => {
      const mockApi = axios.create();
      const mockFile = new File([''], 'test.pdf', { type: 'application/pdf' });
      mockApi.post.mockResolvedValue({ status: 'error', message: 'Parse failed' });

      await expect(parsePdf(mockFile)).rejects.toThrow('Parse failed');
    });

    it('parseBpmn should handle successful response', async () => {
      const mockApi = axios.create();
      const mockFile = new File([''], 'test.bpmn', { type: 'application/xml' });
      const mockContent = { key: 'value' };
      mockApi.post.mockResolvedValue({ status: 'success', data: mockContent });

      const result = await parseBpmn(mockFile);
      expect(result).toEqual({ key: 'value' });
      expect(mockApi.post).toHaveBeenCalledWith(
        expect.stringContaining('/rest/v1/orchestrate/parse-bpmn'),
        expect.any(FormData)
      );
    });

    it('parseBpmn should append process_id as a query param when provided', async () => {
      const mockApi = axios.create();
      const mockFile = new File([''], 'test.bpmn', { type: 'application/xml' });
      mockApi.post.mockResolvedValue({ status: 'success', data: {} });

      await parseBpmn(mockFile, 'process-1');
      expect(mockApi.post).toHaveBeenCalledWith(
        expect.stringContaining('/rest/v1/orchestrate/parse-bpmn?process_id=process-1'),
        expect.any(FormData)
      );
    });

    it('parseBpmn should throw error when status is not success', async () => {
      const mockApi = axios.create();
      const mockFile = new File([''], 'test.bpmn', { type: 'application/xml' });
      mockApi.post.mockResolvedValue({ status: 'error', message: 'Parse failed' });

      await expect(parseBpmn(mockFile)).rejects.toThrow('Parse failed');
    });

    it('handlePlan should handle successful response', async () => {
      const mockApi = axios.create();
      const preflow = {};
      const agentCards = [];
      const mockData = { plan: 'test' };
      mockApi.post.mockResolvedValue({ status: 'success', data: mockData });

      const result = await handlePlan(preflow, agentCards);
      expect(result).toEqual({ plan: 'test' });
      expect(mockApi.post).toHaveBeenCalledWith(
        expect.stringContaining('/rest/v1/orchestrate/generate-from-preflow'),
        { preflow, agent_cards: agentCards }
      );
    });

    it('handlePlan should throw error when status is not success', async () => {
      const mockApi = axios.create();
      mockApi.post.mockResolvedValue({ status: 'error', message: 'Plan failed' });

      await expect(handlePlan({}, [])).rejects.toThrow('Plan failed');
    });

    it('generateWorkflowFromIntent should handle successful response', async () => {
      const mockApi = axios.create();
      const intent = 'test intent';
      const mockWorkflow = { id: 1 };
      mockApi.post.mockResolvedValue({ status: 'success', data: mockWorkflow });

      const result = await generateWorkflowFromIntent(intent);
      expect(result).toEqual(mockWorkflow);
      expect(mockApi.post).toHaveBeenCalledWith(
        expect.stringContaining('/rest/v1/orchestrate/generate-from-intent'),
        { user_intent: intent, workflow_name: "Generated Workflow" }
      );
    });

    it('generateWorkflowFromIntent should throw error when response indicates failure', async () => {
      const mockApi = axios.create();
      mockApi.post.mockResolvedValue({ status: 'error', message: 'Generation failed' });

      await expect(generateWorkflowFromIntent('intent')).rejects.toThrow('Generation failed');
    });

    it('getTemplates should call api.get with correct URL', async () => {
      const mockApi = axios.create();
      mockApi.get.mockResolvedValue({ data: [] });

      await getTemplates();
      expect(mockApi.get).toHaveBeenCalledWith(expect.stringContaining('/rest/v1/orchestrate/templates'));
    });

    it('importTemplate should call api.post with correct URL and template id', async () => {
      const mockApi = axios.create();
      mockApi.post.mockResolvedValue({ data: {} });
      const tplId = 'template_ran_energy_saving';

      await importTemplate(tplId);
      expect(mockApi.post).toHaveBeenCalledWith(
        expect.stringContaining(`/rest/v1/orchestrate/templates/${tplId}/import`)
      );
    });

    it('matchWorkflows should call api.post and return parsed results', async () => {
      const mockApi = axios.create();
      const intent = 'energy saving';
      mockApi.post.mockResolvedValue({
        status: 'success',
        data: [{ id: 'wf1', name: 'ES Workflow', description: 'desc', tags: ['RAN'] }]
      });

      const result = await matchWorkflows(intent);
      expect(result).toEqual([{ workflow_id: 'wf1', name: 'ES Workflow', description: 'desc', tags: ['RAN'] }]);
      expect(mockApi.post).toHaveBeenCalledWith(
        expect.stringContaining('/rest/v1/orchestrate/retrieve-by-intent'),
        { user_intent: intent }
      );
    });

    it('getExecutionRecords should call api.get with correct URL', async () => {
      const mockApi = axios.create();
      mockApi.get.mockResolvedValue({ data: [] });

      await getExecutionRecords();
      expect(mockApi.get).toHaveBeenCalledWith(expect.stringContaining('/rest/v1/orchestrate/execution-records'));
    });

    it('getExecutionRecord should call api.get with execution id', async () => {
      const mockApi = axios.create();
      mockApi.get.mockResolvedValue({ data: {} });
      const execId = 'exec-001';

      await getExecutionRecord(execId);
      expect(mockApi.get).toHaveBeenCalledWith(expect.stringContaining(`/rest/v1/orchestrate/execution-records/${execId}`));
    });

    it('deleteExecutionRecord should call api.delete with execution id', async () => {
      const mockApi = axios.create();
      mockApi.delete.mockResolvedValue({ data: 'ok' });
      const execId = 'exec-002';

      await deleteExecutionRecord(execId);
      expect(mockApi.delete).toHaveBeenCalledWith(expect.stringContaining(`/rest/v1/orchestrate/execution-records/${execId}`));
    });
    it('getStartProcessStreamUrl should build correct SSE URL', () => {
      const url1 = getStartProcessStreamUrl('psop-123');
      expect(url1).toContain('/rest/v1/orchestrate/execute?psop_id=psop-123');
      expect(url1).not.toContain('user_intent');

      const url2 = getStartProcessStreamUrl('psop-456', 'test intent with spaces');
      expect(url2).toContain('/rest/v1/orchestrate/execute?psop_id=psop-456');
      expect(url2).toContain('user_intent=');
    });
  });
});
