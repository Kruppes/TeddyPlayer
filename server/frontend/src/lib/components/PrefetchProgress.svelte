<script lang="ts">
  import { activePrefetches } from '../stores/playback';

  function formatProgress(op: typeof $activePrefetches[0]): string {
    if (op.status === 'complete') return 'Cached';
    if (op.status === 'error') return 'Failed';
    
    const { currentTrack, totalTracks, progress } = op;
    if (totalTracks > 1 && currentTrack > 0) {
      return `Encoding track ${currentTrack}/${totalTracks} (${Math.round(progress)}%)`;
    }
    if (progress > 0) {
      return `Encoding... ${Math.round(progress)}%`;
    }
    return 'Starting...';
  }
</script>

{#if $activePrefetches.length > 0}
  <section class="prefetch-section">
    <h3 class="section-title">Caching</h3>
    <div class="prefetch-list">
      {#each $activePrefetches as op (op.id)}
        <div class="prefetch-card" class:complete={op.status === 'complete'} class:error={op.status === 'error'}>
          <div class="prefetch-icon">
            {#if op.status === 'encoding'}
              <svg class="spinner" width="20" height="20" viewBox="0 0 24 24">
                <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="3" fill="none" opacity="0.3"/>
                <path d="M12 2a10 10 0 0 1 10 10" stroke="currentColor" stroke-width="3" fill="none" stroke-linecap="round"/>
              </svg>
            {:else if op.status === 'complete'}
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round">
                <polyline points="20 6 9 17 4 12"/>
              </svg>
            {:else}
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <circle cx="12" cy="12" r="10"/>
                <line x1="15" y1="9" x2="9" y2="15"/>
                <line x1="9" y1="9" x2="15" y2="15"/>
              </svg>
            {/if}
          </div>
          <div class="prefetch-info">
            <div class="prefetch-title">{op.title}</div>
            <div class="prefetch-status">{formatProgress(op)}</div>
            {#if op.status === 'encoding'}
              <div class="prefetch-progress">
                <div class="prefetch-progress-bar" style="width: {op.progress}%"></div>
              </div>
            {/if}
          </div>
        </div>
      {/each}
    </div>
  </section>
{/if}

<style>
  .prefetch-section {
    margin-bottom: 32px;
  }

  .section-title {
    font-size: 14px;
    font-weight: 600;
    color: var(--color-text-muted);
    text-transform: uppercase;
    letter-spacing: 0.05em;
    margin-bottom: 16px;
  }

  .prefetch-list {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .prefetch-card {
    display: flex;
    align-items: center;
    gap: 12px;
    background: var(--color-card);
    border-radius: var(--radius-md);
    padding: 12px 16px;
    border-left: 3px solid var(--color-primary);
  }

  .prefetch-card.complete {
    border-left-color: var(--color-success);
  }

  .prefetch-card.error {
    border-left-color: var(--color-error);
  }

  .prefetch-icon {
    color: var(--color-primary);
    display: flex;
    align-items: center;
    justify-content: center;
  }

  .prefetch-card.complete .prefetch-icon {
    color: var(--color-success);
  }

  .prefetch-card.error .prefetch-icon {
    color: var(--color-error);
  }

  .prefetch-info {
    flex: 1;
    min-width: 0;
  }

  .prefetch-title {
    font-weight: 500;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .prefetch-status {
    font-size: 12px;
    color: var(--color-text-muted);
  }

  .prefetch-progress {
    background: var(--color-surface);
    height: 6px;
    border-radius: 3px;
    margin-top: 6px;
    overflow: hidden;
  }

  .prefetch-progress-bar {
    height: 100%;
    background: linear-gradient(90deg, var(--color-warning) 0%, var(--color-primary) 100%);
    transition: width 0.5s ease;
    border-radius: 3px;
  }

  .spinner {
    animation: spin 1s linear infinite;
  }

  @keyframes spin {
    from { transform: rotate(0deg); }
    to { transform: rotate(360deg); }
  }
</style>
