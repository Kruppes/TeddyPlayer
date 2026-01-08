<script lang="ts">
  import type { TonieTag } from '../types';
  import { hiddenItems, isItemHidden, toggleHideItem, refreshLibrary } from '../stores/library';
  import { settings } from '../stores/settings';
  import { getImageUrl, prefetchCache } from '../api/client';
  import { addPrefetch, updatePrefetchProgress, completePrefetch, errorPrefetch } from '../stores/playback';
  import Icon from './Icon.svelte';

  let {
    tag,
    onplay,
    ondetails
  }: {
    tag: TonieTag;
    onplay?: (tag: TonieTag) => void;
    ondetails?: (tag: TonieTag) => void;
  } = $props();

  let longPressTimer: ReturnType<typeof setTimeout> | null = $state(null);
  let longPressTriggered = $state(false);
  let imageLoadFailed = $state(false);
  let isPrefetching = $state(false);
  let localCachedOverride = $state<boolean | null>(null);
  
  let isCached = $derived(localCachedOverride ?? tag.cached ?? false);
  
  // Reset local override when tag.cached changes from library refresh
  $effect(() => {
    const _ = tag.cached;
    localCachedOverride = null;
  });

  let isHidden = $derived(isItemHidden($hiddenItems, tag.uid));
  let imageUrl = $derived(getImageUrl(tag.picture, $settings.teddycloud_url));
  let displayTitle = $derived(tag.series || tag.title || tag.name);
  let displaySubtitle = $derived(tag.series ? tag.episode : '');
  let showImage = $derived(imageUrl && !imageLoadFailed);

  function handleImageError() {
    imageLoadFailed = true;
  }

  function handleClick() {
    if (!longPressTriggered) {
      onplay?.(tag);
    }
    longPressTriggered = false;
  }

  function handleLongPressStart() {
    longPressTimer = setTimeout(() => {
      longPressTriggered = true;
      ondetails?.(tag);
    }, 500);
  }

  function handleLongPressEnd() {
    if (longPressTimer) {
      clearTimeout(longPressTimer);
      longPressTimer = null;
    }
  }

  function handleContextMenu(e: MouseEvent) {
    e.preventDefault();
    ondetails?.(tag);
  }

  function handleInfoClick(e: MouseEvent) {
    e.stopPropagation();
    ondetails?.(tag);
  }

  function handleHideClick(e: MouseEvent) {
    e.stopPropagation();
    toggleHideItem(tag.uid);
  }

  async function handlePrefetchClick(e: MouseEvent) {
    e.stopPropagation();
    if (isPrefetching || isCached) return;
    
    isPrefetching = true;
    const prefetchId = addPrefetch(tag.title || tag.series || 'Unknown', tag.audio_url);
    
    try {
      const tracksWithStart = tag.tracks?.map(t => ({ name: t.name, duration: t.duration, start: t.start ?? 0 }));
      const result = await prefetchCache(tag.audio_url, tag.title || tag.series, tracksWithStart);
      
      if (result.status === 'already_cached') {
        localCachedOverride = true;
        completePrefetch(prefetchId);
      } else {
        pollForCompletion(prefetchId, tag.audio_url);
      }
    } catch (err) {
      errorPrefetch(prefetchId);
    } finally {
      isPrefetching = false;
    }
  }

  function pollForCompletion(prefetchId: string, audioUrl: string) {
    const checkInterval = setInterval(async () => {
      try {
        const response = await fetch(`/cache/prefetch?audio_url=${encodeURIComponent(audioUrl)}`);
        if (!response.ok) return;
        const data = await response.json();
        
        // Update progress in store
        updatePrefetchProgress(
          prefetchId,
          data.progress ?? 0,
          data.current_track ?? 0,
          data.total_tracks ?? 0
        );
        
        if (data.cached || data.status === 'cached') {
          clearInterval(checkInterval);
          localCachedOverride = true;  // Update this card's badge immediately
          completePrefetch(prefetchId);
          refreshLibrary();  // Also refresh library for other views
        }
      } catch {
        // Keep polling
      }
    }, 1000);  // Poll every 1s for smoother progress updates
    
    // Stop polling after 5 minutes
    setTimeout(() => clearInterval(checkInterval), 5 * 60 * 1000);
  }

  function handleKeydown(e: KeyboardEvent) {
    if (e.key === 'Enter') {
      handleClick();
    }
  }
</script>

<div
  class="cover-card"
  class:hidden-item={isHidden}
  role="button"
  tabindex="0"
  onclick={handleClick}
  onmousedown={handleLongPressStart}
  onmouseup={handleLongPressEnd}
  onmouseleave={handleLongPressEnd}
  ontouchstart={handleLongPressStart}
  ontouchend={handleLongPressEnd}
  oncontextmenu={handleContextMenu}
  onkeydown={handleKeydown}
>
  <div class="cover-image-container">
    {#if showImage}
      <img
        class="cover-image"
        src={imageUrl}
        alt={tag.series}
        loading="lazy"
        onerror={handleImageError}
      />
    {:else}
      <div class="cover-image-placeholder">
        <Icon name="teddy" size={48} />
      </div>
    {/if}

    <div class="cover-action-left">
      <button
        class="cover-info-btn"
        onclick={handleInfoClick}
        title="Details"
        aria-label="Show details"
      >
        <Icon name="info" size={16} />
      </button>
    </div>

    <div class="cover-action-right">
      <button
        class="cover-hide-btn"
        onclick={handleHideClick}
        title={isHidden ? 'Unhide' : 'Hide'}
        aria-label={isHidden ? 'Unhide' : 'Hide'}
      >
        <Icon name={isHidden ? 'eye' : 'eye-off'} size={16} />
      </button>
    </div>

    {#if isHidden}
      <div class="hidden-badge" title="Hidden">
        <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round">
          <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94"/>
          <path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19"/>
          <line x1="1" y1="1" x2="23" y2="23"/>
        </svg>
      </div>
    {/if}

    {#if isCached}
      <div class="cache-badge" title="Cached">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor">
          <polyline points="20 6 9 17 4 12" stroke="currentColor" stroke-width="3" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
      </div>
    {:else}
      <button
        class="cache-btn"
        onclick={handlePrefetchClick}
        disabled={isPrefetching}
        title="Cache for offline"
        aria-label="Cache for offline"
      >
        {#if isPrefetching}
          <svg class="spinner" width="14" height="14" viewBox="0 0 24 24">
            <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="3" fill="none" opacity="0.3"/>
            <path d="M12 2a10 10 0 0 1 10 10" stroke="currentColor" stroke-width="3" fill="none" stroke-linecap="round"/>
          </svg>
        {:else}
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
            <polyline points="7 10 12 15 17 10"/>
            <line x1="12" y1="15" x2="12" y2="3"/>
          </svg>
        {/if}
      </button>
    {/if}
  </div>

  <div class="cover-info">
    <div class="cover-title">{displayTitle}</div>
    {#if displaySubtitle}
      <div class="cover-subtitle">{displaySubtitle}</div>
    {/if}
  </div>
</div>

<style>
  .cover-card {
    background: var(--color-card);
    border-radius: var(--radius-lg);
    overflow: hidden;
    cursor: pointer;
    transition: all var(--transition-normal);
    border: 2px solid transparent;
    position: relative;
  }

  .cover-card:hover {
    transform: translateY(-6px);
    box-shadow: var(--shadow-lg);
    border-color: var(--color-primary);
  }

  .cover-card.hidden-item {
    opacity: 0.5;
  }

  .cover-card.hidden-item:hover {
    opacity: 0.8;
  }

  .cover-image-container {
    position: relative;
    aspect-ratio: 1;
  }

  .cover-image {
    width: 100%;
    height: 100%;
    object-fit: cover;
    background: var(--color-surface);
  }

  .cover-image-placeholder {
    width: 100%;
    height: 100%;
    background: linear-gradient(135deg, var(--color-surface) 0%, var(--color-card) 100%);
    display: flex;
    align-items: center;
    justify-content: center;
    color: var(--color-text-dim);
  }

  .cover-action-left {
    position: absolute;
    top: 8px;
    left: 8px;
    opacity: 0;
    transition: opacity var(--transition-fast);
  }

  .cover-action-right {
    position: absolute;
    top: 8px;
    right: 8px;
    opacity: 0;
    transition: opacity var(--transition-fast);
  }

  .cover-card:hover .cover-action-left,
  .cover-card:hover .cover-action-right {
    opacity: 1;
  }

  .cover-info-btn, .cover-hide-btn {
    width: 32px;
    height: 32px;
    border-radius: 50%;
    background: rgba(0, 0, 0, 0.7);
    border: none;
    color: var(--color-text-muted);
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 14px;
    transition: all var(--transition-fast);
  }

  .cover-info-btn:hover {
    background: rgba(0, 0, 0, 0.9);
    color: var(--color-primary);
  }

  .cover-hide-btn:hover {
    background: rgba(0, 0, 0, 0.9);
  }

  .cover-card.hidden-item .cover-hide-btn {
    color: var(--color-success);
  }

  .cover-info {
    padding: 12px 14px;
  }

  .cover-title {
    font-size: 14px;
    font-weight: 600;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    margin-bottom: 4px;
  }

  .cover-subtitle {
    font-size: 12px;
    color: var(--color-text-muted);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .cover-card:hover .cover-title {
    color: var(--color-primary);
  }

  .hidden-badge {
    position: absolute;
    top: 0;
    right: 0;
    width: 0;
    height: 0;
    border-style: solid;
    border-width: 0 32px 32px 0;
    border-color: transparent var(--color-text-muted) transparent transparent;
  }

  .hidden-badge svg {
    position: absolute;
    top: 4px;
    right: -28px;
    color: var(--color-bg);
  }

  .cache-badge {
    position: absolute;
    bottom: 0;
    right: 0;
    width: 0;
    height: 0;
    border-style: solid;
    border-width: 0 0 32px 32px;
    border-color: transparent transparent var(--color-primary) transparent;
  }

  .cache-badge svg {
    position: absolute;
    bottom: -28px;
    right: 4px;
    color: #000;
  }

  .cache-btn {
    position: absolute;
    bottom: 8px;
    right: 8px;
    width: 32px;
    height: 32px;
    border-radius: 50%;
    background: rgba(0, 0, 0, 0.7);
    border: none;
    color: var(--color-text-muted);
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    opacity: 0;
    transition: all var(--transition-fast);
  }

  .cover-card:hover .cache-btn {
    opacity: 1;
  }

  .cache-btn:hover {
    background: var(--color-primary);
    color: #000;
  }

  .cache-btn:disabled {
    cursor: wait;
  }

  .cache-btn .spinner {
    animation: spin 1s linear infinite;
  }

  @keyframes spin {
    from { transform: rotate(0deg); }
    to { transform: rotate(360deg); }
  }
</style>
