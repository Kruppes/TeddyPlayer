import { writable } from 'svelte/store';
import type { Settings, CacheInfo, FeatureFlags, VersionInfo, Reader } from '../types';
import * as api from '../api/client';
import { refreshLibrary } from './library';

// Settings
export const settings = writable<Settings>({
  teddycloud_url: '',
  server_url: '',
  audio_cache_max_mb: 500,
});

export const cacheInfo = writable<CacheInfo>({
  size_mb: 0,
  max_mb: 500,
  files: 0,
  folders: 0,
});

// Feature flags
export const featureFlags = writable<FeatureFlags>({
  espuino_enabled: false,
});

// Health status
export const healthStatus = writable<'online' | 'offline' | 'loading'>('loading');

// Version info
export const versionInfo = writable<VersionInfo | null>(null);

// Readers
export const readers = writable<Reader[]>([]);

// Load settings from server
export async function loadSettings(): Promise<void> {
  try {
    const data = await api.getSettings();
    settings.set(data);
  } catch (e) {
    console.error('Failed to load settings:', e);
  }
}

// Save settings to server
export async function saveSettings(newSettings: Partial<Settings>): Promise<void> {
  const result = await api.saveSettings(newSettings);
  settings.set(result);
}

// Load cache info
export async function loadCacheInfo(): Promise<void> {
  try {
    const data = await api.getCacheInfo();
    cacheInfo.set(data);
  } catch (e) {
    console.error('Failed to load cache info:', e);
  }
}

// Clear cache
export async function clearCache(): Promise<{ success: boolean; message: string }> {
  const result = await api.clearCache();
  // API returns { status: string, files_deleted: number }
  const success = result.status === 'ok';
  if (success) {
    await loadCacheInfo();
    await refreshLibrary();
  }
  return {
    success,
    message: success ? `Cleared ${result.files_deleted} cached files` : 'Failed to clear cache',
  };
}

// Load feature flags
export async function loadFeatureFlags(): Promise<void> {
  try {
    const data = await api.getFeatures();
    featureFlags.set(data);
  } catch (e) {
    console.error('Failed to load feature flags:', e);
  }
}

// Check health status
export async function checkHealth(): Promise<void> {
  try {
    healthStatus.set('loading');
    await api.checkHealth();
    healthStatus.set('online');
  } catch (e) {
    healthStatus.set('offline');
  }
}

// Load version info
export async function loadVersionInfo(): Promise<void> {
  try {
    const data = await api.getVersion();
    versionInfo.set(data);
  } catch (e) {
    console.error('Failed to load version info:', e);
  }
}

// Load readers
export async function loadReaders(): Promise<void> {
  try {
    const data = await api.getReaders();
    readers.set(data);
  } catch (e) {
    console.error('Failed to load readers:', e);
  }
}

// Health polling
let healthPollInterval: ReturnType<typeof setInterval> | null = null;

export function startHealthPolling(intervalMs: number = 10000): void {
  if (healthPollInterval) {
    clearInterval(healthPollInterval);
  }
  checkHealth();
  healthPollInterval = setInterval(checkHealth, intervalMs);
}

export function stopHealthPolling(): void {
  if (healthPollInterval) {
    clearInterval(healthPollInterval);
    healthPollInterval = null;
  }
}
