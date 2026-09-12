import type { ClockSample, EventHistoryData, Incident, IncidentSummary, Playback, PlaybackAction, Recording, Transcripts } from './types';

export class ApiError extends Error {
  constructor(message: string, public status: number) {
    super(message);
    this.name = 'ApiError';
  }
}

function errorText(body: unknown): string | undefined {
  if (!body || typeof body !== 'object' || !('detail' in body)) return undefined;
  const detail = body.detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail.map((issue: { loc?: unknown[]; msg?: string }) =>
      `${issue.loc?.slice(1).join(' / ') || 'Request'}: ${issue.msg || 'Invalid value'}`,
    ).join('; ');
  }
  return undefined;
}

async function request<T>(path: string, options?: RequestInit, timeoutMs = 15000): Promise<T> {
  let response: Response;
  try {
    const timeout = timeoutMs > 0 ? AbortSignal.timeout(timeoutMs) : undefined;
    const signal = timeout && options?.signal ? AbortSignal.any([timeout, options.signal]) : timeout ?? options?.signal;
    response = await fetch(`/api${path}`, { ...options, signal });
  } catch (error) {
    if (error instanceof Error && error.name === 'AbortError') throw error;
    if (error instanceof Error && error.name === 'TimeoutError') throw new ApiError('The API request timed out. Check your connection and try again.', 0);
    throw new ApiError('Cannot reach the API. Check your connection and that the API is running.', 0);
  }
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    throw new ApiError(errorText(body) ?? `The request failed (HTTP ${response.status}). Please try again.`, response.status);
  }
  if (body === null) throw new ApiError('The API returned an unreadable response. Please try again.', response.status);
  return body as T;
}

function jsonBody(body: unknown): RequestInit {
  return { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
}

function sample(playback: Playback): ClockSample {
  // Anchor to a monotonic browser clock, so wall-clock adjustments cannot jump replay.
  // Starting at response receipt is intentionally conservative about network transit.
  return { playback, receivedAt: performance.now() };
}

export const api = {
  getEvents: (id: string, signal?: AbortSignal) => request<EventHistoryData>(`/incidents/${id}/events`, { signal }),
  generateEvents: (id: string) => request<EventHistoryData>(`/incidents/${id}/events`, { method: 'POST' }, 120000),
  listIncidents: (signal?: AbortSignal) => request<IncidentSummary[]>('/incidents', { signal }),
  getIncident: (id: string, signal?: AbortSignal) => request<Incident>(`/incidents/${id}`, { signal }),
  createIncident: (title: string, context: string) => request<Incident>('/incidents', {
    method: 'POST', ...jsonBody({ title, context }),
  }),
  uploadRecording: (incidentId: string, form: FormData) => request<Recording>(`/incidents/${incidentId}/recordings`, {
    method: 'POST', body: form,
  }, 0),
  alignRecording: (incidentId: string, recordingId: string, cameraLabel: string, offset: number) => request<Recording>(
    `/incidents/${incidentId}/recordings/${recordingId}`, {
      method: 'PATCH', ...jsonBody({ camera_label: cameraLabel, start_offset_seconds: offset }),
    },
  ),
  getPlayback: async (id: string, signal?: AbortSignal) => sample(await request<Playback>(`/incidents/${id}/playback`, { signal }, 3500)),
  controlPlayback: async (id: string, action: PlaybackAction, expectedRevision: number) => sample(await request<Playback>(
    `/incidents/${id}/playback`, {
      method: 'POST', ...jsonBody({ action, expected_revision: expectedRevision }),
    },
  )),
  getTranscripts: (id: string, signal?: AbortSignal) => request<Transcripts>(`/incidents/${id}/transcripts`, { signal }, 5000),
};

export function messageFor(error: unknown): string {
  return error instanceof Error ? error.message : 'An unexpected error occurred. Please try again.';
}
