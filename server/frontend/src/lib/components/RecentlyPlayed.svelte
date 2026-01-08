<script lang="ts">
  import type { TonieTag } from '../types';
  import Icon from './Icon.svelte';
  import { recentlyPlayedTags } from '../stores/library';
  import { settings } from '../stores/settings';
  import { getImageUrl } from '../api/client';

  // Svelte 5 props with callback handler
  let {
    onplay
  }: {
    onplay?: (tag: TonieTag) => void;
  } = $props();

  // Svelte 5 derived
  let hasRecent = $derived($recentlyPlayedTags.length > 0);
</script>

{#if hasRecent}
  <div class="recently-played-section">
    <div class="section-header">
      <span class="section-title">Recently Played</span>
      <span class="section-count">{$recentlyPlayedTags.length}</span>
    </div>

    <div class="recently-played-grid">
      {#each $recentlyPlayedTags as tag (tag.uid)}
        <button
          class="recent-card"
          onclick={() => onplay?.(tag)}
          title="{tag.series} - {tag.episode}"
        >
          {#if tag.picture}
            <img
              src={getImageUrl(tag.picture, $settings.teddycloud_url)}
              alt={tag.series}
              loading="lazy"
              onerror={(e) => { (e.target as HTMLImageElement).style.display = 'none'; (e.target as HTMLImageElement).nextElementSibling?.classList.remove('hidden'); }}
            />
            <div class="recent-placeholder hidden">
              <Icon name="teddy" size={32} />
            </div>
          {:else}
            <div class="recent-placeholder">
              <Icon name="teddy" size={32} />
            </div>
          {/if}
          <div class="recent-title">{tag.series}</div>
        </button>
      {/each}
    </div>
  </div>
{/if}

<style>
  .recently-played-section {
    margin-bottom: 24px;
  }

  .section-header {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 12px;
  }

  .section-title {
    font-size: 18px;
    font-weight: 600;
    color: var(--color-primary);
  }

  .section-count {
    font-size: 12px;
    color: var(--color-text-muted);
    background: var(--color-surface);
    padding: 4px 8px;
    border-radius: var(--radius-sm);
  }

  .recently-played-grid {
    display: flex;
    gap: 12px;
    overflow-x: auto;
    padding-bottom: 8px;
    scrollbar-width: thin;
    scrollbar-color: var(--color-border) transparent;
  }

  .recently-played-grid::-webkit-scrollbar {
    height: 6px;
  }

  .recently-played-grid::-webkit-scrollbar-track {
    background: transparent;
  }

  .recently-played-grid::-webkit-scrollbar-thumb {
    background: var(--color-border);
    border-radius: 3px;
  }

  .recent-card {
    flex-shrink: 0;
    width: 100px;
    background: var(--color-card);
    border-radius: var(--radius-md);
    overflow: hidden;
    cursor: pointer;
    transition: all var(--transition-fast);
    border: none;
    padding: 0;
    text-align: center;
  }

  .recent-card:hover {
    transform: translateY(-4px);
    box-shadow: var(--shadow-md);
  }

  .recent-card img,
  .recent-placeholder {
    width: 100px;
    height: 100px;
    object-fit: cover;
    background: var(--color-surface);
  }

  .recent-placeholder {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 100%;
    height: 100%;
    background: var(--color-surface);
    color: var(--color-text-muted);
  }

  .recent-placeholder.hidden {
    display: none;
  }

  .recent-title {
    padding: 8px 6px;
    font-size: 11px;
    color: var(--color-text-muted);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .recent-card:hover .recent-title {
    color: var(--color-primary);
  }

  @media (max-width: 600px) {
    .recent-card {
      width: 80px;
    }

    .recent-card img,
    .recent-placeholder {
      width: 80px;
      height: 80px;
    }

    .recent-title {
      font-size: 10px;
      padding: 6px 4px;
    }
  }
</style>
