import type { ChatPayload, Graph, Status } from './types';

async function readJSON<T>(response: Response): Promise<T> {
  const payload = (await response.json()) as T & { error?: string };
  if (!response.ok || payload.error) {
    throw new Error(payload.error || response.status + ' ' + response.statusText);
  }
  return payload;
}

export async function fetchStatus(): Promise<Status> {
  return readJSON<Status>(await fetch('/api/status'));
}

export async function fetchGraph(): Promise<Graph> {
  return readJSON<Graph>(await fetch('/api/graph'));
}

export async function sendChat(text: string, lang: string, endpoints?: Partial<Record<'origin' | 'destination', string>>): Promise<ChatPayload> {
  const response = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, lang, endpoints }),
  });
  // a server-side rejection still carries a message worth showing verbatim
  return (await response.json()) as ChatPayload;
}

export async function saveSettings(
  apiKey: string | null,
  model: string | null,
): Promise<{ status: Status; env: string }> {
  const response = await fetch('/api/settings', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ api_key: apiKey, model }),
  });
  const payload = await readJSON<{ status: Status; env: string; ok: boolean }>(response);
  return { status: payload.status, env: payload.env };
}

export async function resetSession(): Promise<void> {
  await fetch('/api/reset', { method: 'POST' });
}
