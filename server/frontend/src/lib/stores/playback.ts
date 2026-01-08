import { writable, get } from 'svelte/store';
import type { ActiveStream, Upload, PendingUpload, TonieTag, Device } from '../types';
import * as api from '../api/client';
import { selectedDevice } from './devices';
import { refreshLibrary } from './library';

export const activeStreams = writable<ActiveStream[]>([]);
export const activeUploads = writable<Upload[]>([]);
export const pendingUploads = writable<PendingUpload[]>([]);

let encodingUidsFromLastPoll = new Set<string>();

export interface PrefetchOperation {
  id: string;
  title: string;
  audioUrl: string;
  status: 'encoding' | 'complete' | 'error';
  progress: number;
  currentTrack: number;
  totalTracks: number;
  startedAt: number;
}

export const activePrefetches = writable<PrefetchOperation[]>([]);

export function addPrefetch(title: string, audioUrl: string): string {
  const id = `prefetch-${Date.now()}`;
  activePrefetches.update(ops => [...ops, {
    id,
    title,
    audioUrl,
    status: 'encoding',
    progress: 0,
    currentTrack: 0,
    totalTracks: 0,
    startedAt: Date.now()
  }]);
  return id;
}

export function updatePrefetchProgress(id: string, progress: number, currentTrack: number, totalTracks: number) {
  activePrefetches.update(ops => 
    ops.map(op => op.id === id ? { ...op, progress, currentTrack, totalTracks } : op)
  );
}

export function completePrefetch(id: string) {
  activePrefetches.update(ops => 
    ops.map(op => op.id === id ? { ...op, status: 'complete' as const, progress: 100 } : op)
  );
  setTimeout(() => {
    activePrefetches.update(ops => ops.filter(op => op.id !== id));
  }, 2000);
}

export function errorPrefetch(id: string) {
  activePrefetches.update(ops => 
    ops.map(op => op.id === id ? { ...op, status: 'error' as const } : op)
  );
  setTimeout(() => {
    activePrefetches.update(ops => ops.filter(op => op.id !== id));
  }, 3000);
}

let pollInterval: ReturnType<typeof setInterval> | null = null;

export async function loadStreams(): Promise<void> {
  try {
    const data = await api.getStreams();
    
    const currentlyEncodingUids = new Set<string>();
    for (const stream of data.streams) {
      const isEncoding = stream.encoding?.status === 'encoding' || 
        (stream.encoding?.progress !== undefined && stream.encoding.progress < 100 && stream.encoding.progress > 0);
      if (isEncoding && stream.tag?.uid) {
        currentlyEncodingUids.add(stream.tag.uid);
      }
    }
    
    for (const uid of encodingUidsFromLastPoll) {
      if (!currentlyEncodingUids.has(uid)) {
        refreshLibrary();
        break;
      }
    }
    
    encodingUidsFromLastPoll = currentlyEncodingUids;
    
    activeStreams.set(data.streams);
    activeUploads.set(data.uploads);
    pendingUploads.set(data.pending_uploads);
  } catch (e) {
    console.error('[Playback] Failed to load streams:', e);
  }
}

export function startStreamPolling(intervalMs: number = 1000): void {
  if (pollInterval) {
    clearInterval(pollInterval);
  }
  loadStreams();
  pollInterval = setInterval(loadStreams, intervalMs);
}

export function stopStreamPolling(): void {
  if (pollInterval) {
    clearInterval(pollInterval);
    pollInterval = null;
  }
}

export async function startPlayback(
  tag: TonieTag,
  device?: Device
): Promise<{ success: boolean; error?: string }> {
  const targetDevice = device ?? get(selectedDevice);
  
  console.log('[Playback] Starting:', { uid: tag.uid, device: targetDevice, tracks: tag.tracks?.length });
  
  try {
    const response = await api.playTonie(
      tag.uid,
      targetDevice.type,
      targetDevice.id,
      {
        title: tag.title,
        series: tag.series,
        episode: tag.episode,
        picture: tag.picture,
        tracks: tag.tracks,
        audio_url: tag.audio_url,
      }
    );
    
    console.log('[Playback] Response:', response);
    
    await loadStreams();
    return { success: true };
  } catch (e) {
    const errorMsg = e instanceof Error ? e.message : 'Playback failed';
    console.error('[Playback] Start error:', e);
    return { success: false, error: errorMsg };
  }
}

export async function stopStream(readerIp: string): Promise<void> {
  try {
    await api.stopStream(readerIp);
    activeStreams.update(streams =>
      streams.filter(s => s.reader_ip !== readerIp)
    );
  } catch (e) {
    console.error('[Playback] Stop error:', e);
    throw e;
  }
}

export async function pauseStream(readerIp: string): Promise<void> {
  try {
    await api.pauseStream(readerIp);
  } catch (e) {
    console.error('[Playback] Pause error:', e);
    throw e;
  }
}

export async function resumeStream(readerIp: string): Promise<void> {
  try {
    await api.resumeStream(readerIp);
  } catch (e) {
    console.error('[Playback] Resume error:', e);
    throw e;
  }
}

export async function seekStream(readerIp: string, position: number): Promise<void> {
  try {
    await api.seekStream(readerIp, position);
  } catch (e) {
    console.error('[Playback] Seek error:', e);
    throw e;
  }
}

export async function nextTrackStream(readerIp: string): Promise<void> {
  try {
    await api.nextTrack(readerIp);
  } catch (e) {
    console.error('[Playback] Next track error:', e);
    throw e;
  }
}

export async function prevTrackStream(readerIp: string): Promise<void> {
  try {
    await api.prevTrack(readerIp);
  } catch (e) {
    console.error('[Playback] Prev track error:', e);
    throw e;
  }
}

export async function cancelUpload(uid: string, deviceId: string): Promise<void> {
  await api.cancelUpload(uid, deviceId);
  await loadStreams();
}

export async function cancelPendingUpload(uid: string, deviceId: string): Promise<void> {
  await api.cancelPendingUpload(uid, deviceId);
  await loadStreams();
}

export async function retryUpload(uid: string, deviceId: string): Promise<void> {
  await api.retryUpload(uid, deviceId);
  await loadStreams();
}

export async function dismissUploadError(uid: string, deviceId: string): Promise<void> {
  await api.dismissUploadError(uid, deviceId);
  await loadStreams();
}

export async function clearAllErrors(): Promise<void> {
  await api.clearAllErrors();
  await loadStreams();
}

export async function wipeAllUploads(): Promise<void> {
  await api.wipeAllUploads();
  await loadStreams();
}
