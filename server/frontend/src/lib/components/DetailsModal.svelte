<script lang="ts">
  import Modal from './Modal.svelte';
  import Icon from './Icon.svelte';
  import type { TonieTag } from '../types';
  import { settings } from '../stores/settings';
  import { getImageUrl, getTracksInfo } from '../api/client';

  // Svelte 5 props with callbacks instead of createEventDispatcher
  let {
    open = $bindable(false),
    tag = null,
    onplay
  }: {
    open: boolean;
    tag: TonieTag | null;
    onplay?: (tag: TonieTag) => void;
  } = $props();

  let tracks: { title: string; duration: number }[] = $state([]);
  let loadingTracks = $state(false);

  $effect(() => {
    if (open && tag) {
      loadTracks(tag.uid);
    }
  });

  async function loadTracks(uid: string) {
    loadingTracks = true;
    try {
      const result = await getTracksInfo(uid);
      tracks = result.tracks || [];
    } catch (e) {
      console.error('Failed to load tracks:', e);
      tracks = (tag?.tracks || []).map(t => ({ title: t.name, duration: t.duration }));
    } finally {
      loadingTracks = false;
    }
  }

  function formatDuration(seconds: number): string {
    if (!seconds || isNaN(seconds)) return '-';
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${mins}:${secs.toString().padStart(2, '0')}`;
  }

  function getTotalDuration(): string {
    if (tracks.length === 0) return '-';
    const total = tracks.reduce((sum, t) => sum + (t.duration || 0), 0);
    if (total === 0) return '-';
    const mins = Math.floor(total / 60);
    const secs = Math.floor(total % 60);
    return `${mins}:${secs.toString().padStart(2, '0')}`;
  }

  function handlePlay() {
    if (tag) {
      onplay?.(tag);
    }
  }

  function handleClose() {
    open = false;
  }

  // Svelte 5 derived values
  let imageUrl = $derived(tag ? getImageUrl(tag.picture, $settings.teddycloud_url) : '');
  let displayTitle = $derived(tag ? (tag.series || tag.title || tag.name) : '');
  let displaySubtitle = $derived(tag?.series ? tag.episode : '');
</script>

<Modal {open} title="Details" maxWidth="450px" onclose={handleClose}>
  {#if tag}
    <div class="details-content">
      <div class="details-header">
        {#if imageUrl}
          <img class="details-cover" src={imageUrl} alt={tag.series} />
        {:else}
          <div class="details-cover-placeholder">
            <Icon name="teddy" size={36} />
          </div>
        {/if}

        <div class="details-main">
          <div class="details-title">{displayTitle}</div>
          {#if displaySubtitle}
            <div class="details-subtitle">{displaySubtitle}</div>
          {/if}

          <div class="details-meta">
            <span class="details-meta-label">UID:</span>
            <span class="details-meta-value">{tag.uid}</span>

            <span class="details-meta-label">Tracks:</span>
            <span class="details-meta-value">{tag.num_tracks}</span>

            <span class="details-meta-label">Duration:</span>
            <span class="details-meta-value">{getTotalDuration()}</span>
          </div>
        </div>
      </div>

      {#if tracks.length > 0 || loadingTracks}
        <div class="details-tracks">
          <div class="details-tracks-title">
            Tracks ({loadingTracks ? '...' : tracks.length})
          </div>
          <div class="details-track-list">
            {#if loadingTracks}
              <div class="details-track">
                <span class="details-track-name">Loading...</span>
              </div>
            {:else}
              {#each tracks as track, i}
                <div class="details-track">
                  <span class="details-track-name">
                    {i + 1}. {track.title || `Track ${i + 1}`}
                  </span>
                  <span class="details-track-duration">
                    {formatDuration(track.duration)}
                  </span>
                </div>
              {/each}
            {/if}
          </div>
        </div>
      {/if}
    </div>

    <div class="btn-group" style="justify-content: flex-end; margin-top: 20px;">
      <button class="btn btn-secondary" onclick={handleClose}>
        Close
      </button>
      <button class="btn btn-primary" onclick={handlePlay}>
        Play
      </button>
    </div>
  {/if}
</Modal>

<style>
  .details-content {
    display: flex;
    flex-direction: column;
    gap: 16px;
  }

  .details-header {
    display: flex;
    gap: 16px;
    align-items: flex-start;
  }

  .details-cover {
    width: 100px;
    height: 100px;
    border-radius: var(--radius-md);
    object-fit: cover;
    background: var(--color-surface);
    flex-shrink: 0;
  }

  .details-cover-placeholder {
    width: 100px;
    height: 100px;
    border-radius: var(--radius-md);
    background: var(--color-surface);
    display: flex;
    align-items: center;
    justify-content: center;
    color: var(--color-text-dim);
    flex-shrink: 0;
  }

  .details-main {
    flex: 1;
    min-width: 0;
  }

  .details-title {
    font-size: 18px;
    font-weight: 600;
    margin-bottom: 4px;
    color: var(--color-primary);
  }

  .details-subtitle {
    font-size: 14px;
    color: var(--color-text-muted);
    margin-bottom: 8px;
  }

  .details-meta {
    display: grid;
    grid-template-columns: auto 1fr;
    gap: 8px 12px;
    font-size: 13px;
  }

  .details-meta-label {
    color: var(--color-text-dim);
  }

  .details-meta-value {
    color: var(--color-text);
    word-break: break-all;
  }

  .details-tracks {
    margin-top: 8px;
  }

  .details-tracks-title {
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    color: var(--color-text-dim);
    margin-bottom: 8px;
  }

  .details-track-list {
    background: var(--color-card);
    border-radius: var(--radius-md);
    max-height: 200px;
    overflow-y: auto;
  }

  .details-track {
    display: flex;
    justify-content: space-between;
    padding: 8px 12px;
    font-size: 13px;
    border-bottom: 1px solid var(--color-border);
  }

  .details-track:last-child {
    border-bottom: none;
  }

  .details-track-name {
    color: var(--color-text);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    margin-right: 12px;
  }

  .details-track-duration {
    color: var(--color-text-dim);
    font-family: monospace;
    flex-shrink: 0;
  }
</style>
