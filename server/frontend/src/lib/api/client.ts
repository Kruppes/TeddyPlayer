import type {
  DevicesCache,
  TonieTag,
  LibraryResponse,
  StreamsResponse,
  Settings,
  Preferences,
  Reader,
  FeatureFlags,
  CacheInfo,
  VersionInfo,
} from '../types';

const BASE_URL = '';  // Relative to same origin

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = 'ApiError';
  }
}

async function fetchJson<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  if (!res.ok) {
    let errorDetail = `${res.status} ${res.statusText}`;
    try {
      const errorBody = await res.json();
      errorDetail = errorBody.detail || errorBody.message || errorDetail;
    } catch {
    }
    console.error(`[API] ${options?.method || 'GET'} ${path} failed:`, errorDetail);
    throw new ApiError(res.status, errorDetail);
  }
  return res.json();
}

// Health & Version
export const checkHealth = () => fetchJson<{ status: string }>('/health');
export const getVersion = () => fetchJson<VersionInfo>('/version');

// Features
export const getFeatures = () => fetchJson<FeatureFlags>('/api/features');

// Library
export const getLibrary = async (): Promise<TonieTag[]> => {
  const response = await fetchJson<LibraryResponse>('/library');
  return response.files;
};

// Streams
export const getStreams = () =>
  fetchJson<StreamsResponse>(`/streams?_t=${Date.now()}`);

// Devices
export const getDevices = () => fetchJson<DevicesCache>('/devices');

export const discoverDevices = () =>
  fetchJson<{ discovered: number }>('/devices/discover', { method: 'POST' });

export const removeDevice = (type: string, id: string) =>
  fetchJson<{ success: boolean }>(`/devices/${type}/${encodeURIComponent(id)}`, {
    method: 'DELETE'
  });

// Playback
export interface ToniePlayResponse {
  uid: string;
  found: boolean;
  playback_started: boolean;
  encoding?: boolean;
  playback_url?: string;
  series?: string;
  episode?: string;
  title?: string;
  picture?: string;
}

export interface PlayMetadata {
  title?: string;
  series?: string;
  episode?: string;
  picture?: string;
  tracks?: { name: string; duration: number; start: number }[];
  audio_url?: string;
}

export const playTonie = (uid: string, deviceType: string, deviceId: string, metadata?: PlayMetadata): Promise<ToniePlayResponse> => {
  // Use device-specific reader ID to allow multiple concurrent streams from browser
  // e.g., "web-sonos-RINCON_xxx" or "web-browser-web"
  const readerId = `web-${deviceType}-${deviceId || 'web'}`;
  const payload = {
    uid,
    mode: 'stream',
    target_device: { type: deviceType, id: deviceId || 'web' },
    espuino_ip: readerId,
    ...metadata
  };
  console.log('[API] playTonie payload:', payload);
  return fetchJson<ToniePlayResponse>('/tonie', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
};

export const playUrl = (audioUrl: string, title: string, deviceType: string, deviceId: string) =>
  fetchJson<{ status?: string; error?: string }>('/playback/url', {
    method: 'POST',
    body: JSON.stringify({ audio_url: audioUrl, title, device_type: deviceType, device_id: deviceId }),
  });

export const stopStream = (readerIp: string) =>
  fetchJson<{ success: boolean }>(`/readers/${encodeURIComponent(readerIp)}/playback/stop`, {
    method: 'POST'
  });

export const pauseStream = (readerIp: string) =>
  fetchJson<{ success: boolean }>(`/readers/${encodeURIComponent(readerIp)}/playback/pause`, {
    method: 'POST'
  });

export const resumeStream = (readerIp: string) =>
  fetchJson<{ success: boolean }>(`/readers/${encodeURIComponent(readerIp)}/playback/play`, {
    method: 'POST'
  });

export const seekStream = (readerIp: string, position: number) =>
  fetchJson<{ success: boolean }>(`/readers/${encodeURIComponent(readerIp)}/playback/seek`, {
    method: 'POST',
    body: JSON.stringify({ position: position })
  });

export const nextTrack = (readerIp: string) =>
  fetchJson<{ status: string }>(`/readers/${encodeURIComponent(readerIp)}/playback/next`, {
    method: 'POST'
  });

export const prevTrack = (readerIp: string) =>
  fetchJson<{ status: string }>(`/readers/${encodeURIComponent(readerIp)}/playback/prev`, {
    method: 'POST'
  });

export const reportPosition = (readerIp: string, uid: string, position: number) =>
  fetch(`/readers/${encodeURIComponent(readerIp)}/position`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ uid, position }),
  });

// Settings
export const getSettings = () => fetchJson<Settings>('/settings');

export const saveSettings = (settings: Partial<Settings>) =>
  fetchJson<Settings>('/settings', {
    method: 'PUT',
    body: JSON.stringify(settings)
  });

// Preferences
export const getPreferences = () => fetchJson<Preferences>('/preferences');

export const savePreference = (key: string, value: unknown) =>
  fetchJson<Preferences>('/preferences', {
    method: 'PUT',
    body: JSON.stringify({ [key]: value })
  });

// Readers
export const getReaders = async (): Promise<Reader[]> => {
  const response = await fetchJson<{ count: number; readers: Reader[] }>('/readers');
  return response.readers;
};

export const setReaderDevice = (readerIp: string, type: string, id: string) =>
  fetchJson<{ success: boolean }>(`/readers/${encodeURIComponent(readerIp)}/device`, {
    method: 'POST',
    body: JSON.stringify({ type, id }),
  });

export const clearReaderDevice = (readerIp: string) =>
  fetchJson<{ success: boolean }>(`/readers/${encodeURIComponent(readerIp)}/device`, {
    method: 'DELETE',
  });

export const renameReader = (readerIp: string, name: string) =>
  fetchJson<{ success: boolean }>(`/readers/${encodeURIComponent(readerIp)}/name`, {
    method: 'PUT',
    body: JSON.stringify({ name }),
  });

export const forgetReader = (readerIp: string) =>
  fetchJson<{ success: boolean }>(`/readers/${encodeURIComponent(readerIp)}`, {
    method: 'DELETE',
  });

// Cache
export const getCacheInfo = () => fetchJson<CacheInfo>('/cache');

export const clearCache = () =>
  fetchJson<{ status: string; files_deleted: number }>('/cache', {
    method: 'DELETE'
  });

// Uploads (ESPuino)
export const cancelUpload = (uid: string, deviceId: string) =>
  fetchJson<{ success: boolean }>(`/uploads/${encodeURIComponent(uid)}/${encodeURIComponent(deviceId)}`, {
    method: 'DELETE',
  });

export const cancelPendingUpload = (uid: string, espuinoIp: string) =>
  fetchJson<{ status: string; cleared: string }>(`/uploads/pending?espuino_ip=${encodeURIComponent(espuinoIp)}`, {
    method: 'DELETE',
  });

export const retryUpload = (uid: string, deviceId: string) =>
  fetchJson<{ success: boolean }>(`/uploads/${encodeURIComponent(uid)}/${encodeURIComponent(deviceId)}/retry`, {
    method: 'POST',
  });

export const dismissUploadError = (uid: string, deviceId: string) =>
  fetchJson<{ success: boolean }>(`/uploads/${encodeURIComponent(uid)}/${encodeURIComponent(deviceId)}/dismiss`, {
    method: 'POST',
  });

export const clearAllErrors = () =>
  fetchJson<{ success: boolean }>('/uploads/errors/clear', { method: 'POST' });

export const wipeAllUploads = () =>
  fetchJson<{ success: boolean }>('/uploads/wipe', { method: 'POST' });

// Cache prefetch
export interface PrefetchResponse {
  status: 'encoding' | 'already_cached';
  audio_url: string;
  tracks?: number;
}

export const prefetchCache = (audioUrl: string, title: string, tracks?: { name: string; duration: number; start: number }[]) =>
  fetchJson<PrefetchResponse>('/cache/prefetch', {
    method: 'POST',
    body: JSON.stringify({ audio_url: audioUrl, title, tracks }),
  });

// Tracks info
export const getTracksInfo = (uid: string) =>
  fetchJson<{ tracks: { title: string; duration: number }[] }>(`/tracks/${encodeURIComponent(uid)}`);

// Proxy URLs for images
export function getImageUrl(picturePath: string | undefined | null, _teddycloudUrl: string): string {
  if (!picturePath || picturePath.trim() === '') return '';
  return `/proxy/image?path=${encodeURIComponent(picturePath)}`;
}
