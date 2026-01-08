<script lang="ts">
  import { searchQuery, showHidden, hiddenCount, refreshLibrary } from '../stores/library';
  import { showSuccess } from '../stores/alerts';
  import Icon from './Icon.svelte';

  let isRefreshing = $state(false);

  async function handleRefresh() {
    isRefreshing = true;
    try {
      await refreshLibrary();
      showSuccess('Library refreshed');
    } finally {
      isRefreshing = false;
    }
  }
</script>

<div class="library-controls">
  <input
    type="text"
    class="search-input"
    placeholder="Search your Tonie library..."
    bind:value={$searchQuery}
  />
  <button
    class="control-btn refresh-btn"
    onclick={handleRefresh}
    disabled={isRefreshing}
    title="Refresh library from TeddyCloud"
  >
    <Icon name="refresh" size={16} />
  </button>
  <button
    class="control-btn toggle-btn"
    class:active={$showHidden}
    onclick={() => showHidden.update(v => !v)}
  >
    Show Hidden{#if $hiddenCount > 0} ({$hiddenCount}){/if}
  </button>
</div>

<style>
  .library-controls {
    display: flex;
    gap: 12px;
    margin-bottom: 24px;
    align-items: center;
  }

  .search-input {
    flex: 1;
    background: var(--color-surface);
    border: 1px solid var(--color-border);
    color: var(--color-text);
    padding: 14px 18px;
    border-radius: var(--radius-lg);
    font-size: 15px;
    transition: border-color var(--transition-fast);
  }

  .search-input:focus {
    outline: none;
    border-color: var(--color-primary);
  }

  .search-input::placeholder {
    color: var(--color-text-dim);
  }

  .control-btn {
    background: var(--color-surface);
    border: 1px solid var(--color-border);
    color: var(--color-text-muted);
    padding: 12px 16px;
    border-radius: var(--radius-lg);
    font-size: 13px;
    cursor: pointer;
    transition: all var(--transition-fast);
    display: flex;
    align-items: center;
    gap: 6px;
  }

  .control-btn:hover:not(:disabled) {
    border-color: var(--color-primary);
    color: var(--color-text);
  }

  .control-btn:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }

  .toggle-btn.active {
    background: var(--color-primary);
    border-color: var(--color-primary);
    color: #000;
  }

  .refresh-btn {
    padding: 12px;
  }

  @media (max-width: 600px) {
    .library-controls {
      flex-wrap: wrap;
    }

    .search-input {
      flex: 1 1 100%;
    }
  }
</style>
