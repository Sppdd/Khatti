// Client for the Khatti API (khatti/api.py). Every call is timed and logged.
import { Platform } from 'react-native';

import { demoExtract } from './demo';
import type { PreparedImage } from './image';
import { getSettings } from './settings';
import { logApi } from './store';
import type { ExtractMode, ExtractResult, KycCaseResult } from './types';

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number | null,
  ) {
    super(message);
  }
}

export interface Timed<T> {
  data: T;
  latencyMs: number;
}

const TIMEOUT_MS = 90_000;
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

async function filePart(image: PreparedImage, name: string): Promise<Blob> {
  if (Platform.OS === 'web') return (await fetch(image.uri)).blob();
  // React Native's FormData streams the file from its URI.
  return { uri: image.uri, name, type: 'image/jpeg' } as unknown as Blob;
}

async function call<T>(endpoint: string, init: RequestInit, meta: Record<string, unknown> = {}): Promise<Timed<T>> {
  const { apiUrl, apiKey } = getSettings();
  if (!apiUrl) throw new ApiError('Set the Khatti API URL in the Lab tab, or turn on demo mode.', null);

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  const started = Date.now();
  let status: number | null = null;
  try {
    const res = await fetch(`${apiUrl.replace(/\/+$/, '')}${endpoint}`, {
      ...init,
      signal: controller.signal,
      headers: {
        Accept: 'application/json',
        ...(init.headers as Record<string, string> | undefined),
        ...(apiKey ? { Authorization: `Bearer ${apiKey}` } : {}),
      },
    });
    status = res.status;
    const body = await res.json().catch(() => null);
    if (!res.ok) {
      const detail = body?.detail;
      throw new ApiError(
        typeof detail === 'string' ? detail : detail ? JSON.stringify(detail) : `HTTP ${res.status}`,
        res.status,
      );
    }
    const latencyMs = Date.now() - started;
    logApi({ endpoint, status, ok: true, latency_ms: latencyMs, error: null, meta });
    return { data: body as T, latencyMs };
  } catch (e) {
    const err =
      e instanceof ApiError
        ? e
        : new ApiError(
            (e as Error)?.name === 'AbortError' ? 'The API took too long to answer' : `Network error: ${(e as Error)?.message}`,
            status,
          );
    logApi({ endpoint, status, ok: false, latency_ms: Date.now() - started, error: err.message, meta });
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

export async function extractPhoto(image: PreparedImage, mode: ExtractMode): Promise<Timed<ExtractResult>> {
  if (getSettings().demoMode) {
    const started = Date.now();
    await sleep(1400 + Math.random() * 900);
    const latencyMs = Date.now() - started;
    logApi({ endpoint: '/v1/extract (demo)', status: 200, ok: true, latency_ms: latencyMs, error: null, meta: { mode } });
    return { data: demoExtract(mode), latencyMs };
  }
  const { extractModel } = getSettings();
  const form = new FormData();
  form.append('file', await filePart(image, 'photo.jpg'));
  form.append('mode', mode);
  if (extractModel) form.append('model', extractModel);
  return call<ExtractResult>(
    '/v1/extract',
    { method: 'POST', body: form },
    { mode, model: extractModel || null, width: image.width, height: image.height },
  );
}

export interface Health {
  status: string;
  readers: string[];
  router: string | null;
  extractor: string | null;
  auth: boolean;
}

export const getHealth = () => call<Health>('/health', { method: 'GET' });

export type DocType = 'national_id' | 'passport' | 'residence_card';

export async function createKycCase(docs: { image: PreparedImage; docType: DocType }[]): Promise<Timed<KycCaseResult>> {
  const form = new FormData();
  for (const [i, d] of docs.entries()) {
    form.append('files', await filePart(d.image, `doc-${i}.jpg`));
    form.append('doc_types', d.docType);
  }
  return call<KycCaseResult>('/v1/kyc/cases', { method: 'POST', body: form }, { docs: docs.map((d) => d.docType) });
}

// ---------- Model bridge (khatti/bridge.py) ----------

export interface BridgeProvider {
  name: string;
  label: string;
  kind: 'openai' | 'hf-endpoints';
  features: string[];
  default: boolean;
}

export interface BridgeModel {
  id: string; // "<provider>/<model id>"
  provider: string;
  owned_by?: string | null;
  state?: string | null; // HF endpoints only
}

export interface HfEndpoint {
  name: string;
  model?: { repository?: string };
  compute?: { instanceType?: string; instanceSize?: string; accelerator?: string };
  status?: { state?: string; url?: string; message?: string };
}

const json = (body: unknown): RequestInit => ({
  method: 'POST',
  body: JSON.stringify(body),
  headers: { 'Content-Type': 'application/json' },
});

export const getProviders = () =>
  call<{ providers: BridgeProvider[]; default: string | null; hosting: boolean }>('/v1/bridge/providers', { method: 'GET' });

export const getModels = () =>
  call<{ data: BridgeModel[]; errors: Record<string, string> }>('/v1/bridge/models', { method: 'GET' });

export async function chatOnce(model: string, prompt: string): Promise<Timed<string>> {
  const { data, latencyMs } = await call<{ choices: { message: { content: string | null } }[] }>(
    '/v1/bridge/openai/chat/completions',
    json({ model, messages: [{ role: 'user', content: prompt }], max_tokens: 600 }),
    { model },
  );
  return { data: data.choices?.[0]?.message?.content ?? '', latencyMs };
}

// Returns an image URI (remote URL or data: URI) from any bridged image model.
export async function generateImage(model: string, prompt: string): Promise<Timed<string>> {
  const { data, latencyMs } = await call<{ data: { url?: string; b64_json?: string }[] }>(
    '/v1/bridge/openai/images/generations',
    json({ model, prompt, response_format: 'b64_json' }),
    { model },
  );
  const first = data.data?.[0];
  const uri = first?.b64_json ? `data:image/png;base64,${first.b64_json}` : first?.url;
  if (!uri) throw new ApiError('The model returned no image', 200);
  return { data: uri, latencyMs };
}

export const listEndpoints = () => call<{ items: HfEndpoint[] }>('/v1/bridge/hosting/endpoints', { method: 'GET' });

export const hostModel = (repository: string, instanceType: string) =>
  call<HfEndpoint>('/v1/bridge/hosting/endpoints', json({ repository, instance_type: instanceType }), { repository });

export const endpointAction = (name: string, action: 'pause' | 'resume' | 'scale-to-zero' | 'delete') =>
  action === 'delete'
    ? call<unknown>(`/v1/bridge/hosting/endpoints/${encodeURIComponent(name)}`, { method: 'DELETE' }, { name, action })
    : call<unknown>(`/v1/bridge/hosting/endpoints/${encodeURIComponent(name)}/${action}`, { method: 'POST' }, { name, action });
