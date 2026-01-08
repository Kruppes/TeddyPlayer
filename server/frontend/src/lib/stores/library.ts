import { writable, derived } from 'svelte/store';
import type { TonieTag } from '../types';
import * as api from '../api/client';

// Raw tags cache from server
export const tagsCache = writable<TonieTag[]>([]);

// Search query
export const searchQuery = writable('');

// Show hidden items toggle
export const showHidden = writable(false);

// Hidden items (persisted)
export const hiddenItems = writable<string[]>([]);

// Recently played UIDs (persisted)
export const recentlyPlayed = writable<string[]>([]);

// Filtered library based on search and hidden state
export const filteredLibrary = derived(
  [tagsCache, searchQuery, showHidden, hiddenItems],
  ([$tags, $query, $showHidden, $hidden]) => {
    return $tags.filter(tag => {
      // Search filter
      const queryLower = $query.toLowerCase();
      const matchesSearch = !$query ||
        tag.series.toLowerCase().includes(queryLower) ||
        tag.episode.toLowerCase().includes(queryLower);

      // Hidden filter
      const isHidden = $hidden.includes(tag.uid);

      return matchesSearch && ($showHidden || !isHidden);
    });
  }
);

// Recently played with full tag info
export const recentlyPlayedTags = derived(
  [tagsCache, recentlyPlayed, hiddenItems],
  ([$tags, $recent, $hidden]) => {
    return $recent
      .map(uid => $tags.find(t => t.uid === uid))
      .filter((tag): tag is TonieTag => tag !== undefined && !$hidden.includes(tag.uid));
  }
);

// Hidden items count
export const hiddenCount = derived(hiddenItems, $hidden => $hidden.length);

// Sort tags alphabetically by series/title, then by episode
function sortTags(tags: TonieTag[]): TonieTag[] {
  return [...tags].sort((a, b) => {
    const titleA = (a.series || a.title || '').toLowerCase();
    const titleB = (b.series || b.title || '').toLowerCase();
    if (titleA !== titleB) return titleA.localeCompare(titleB);
    const epA = (a.episode || '').toLowerCase();
    const epB = (b.episode || '').toLowerCase();
    return epA.localeCompare(epB, undefined, { numeric: true });
  });
}

// Load library from server
export async function loadLibrary(): Promise<void> {
  try {
    const data = await api.getLibrary();
    tagsCache.set(sortTags(data));
  } catch (e) {
    console.error('Failed to load library:', e);
  }
}

// Refresh library from server
export async function refreshLibrary(): Promise<void> {
  await loadLibrary();
}

// Add to recently played
export async function addToRecentlyPlayed(uid: string): Promise<void> {
  recentlyPlayed.update(recent => {
    // Remove if already in list
    const filtered = recent.filter(u => u !== uid);
    // Add to front, max 10 items
    return [uid, ...filtered].slice(0, 10);
  });

  // Persist to server
  let currentRecent: string[] = [];
  recentlyPlayed.subscribe(r => currentRecent = r)();
  await api.savePreference('recentlyPlayed', currentRecent);
}

// Toggle hidden state
export async function toggleHideItem(uid: string): Promise<void> {
  hiddenItems.update(hidden => {
    const index = hidden.indexOf(uid);
    if (index >= 0) {
      return [...hidden.slice(0, index), ...hidden.slice(index + 1)];
    } else {
      return [...hidden, uid];
    }
  });

  // Persist to server
  let currentHidden: string[] = [];
  hiddenItems.subscribe(h => currentHidden = h)();
  await api.savePreference('hiddenItems', currentHidden);
}

// Check if item is hidden
export function isItemHidden(hiddenList: string[], uid: string): boolean {
  return hiddenList.includes(uid);
}

// Get tag by UID
export function getTagByUid(tags: TonieTag[], uid: string): TonieTag | undefined {
  return tags.find(t => t.uid === uid);
}
