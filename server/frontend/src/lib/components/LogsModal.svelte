<script lang="ts">
  import Modal from './Modal.svelte';
  import Icon from './Icon.svelte';

  let { open = $bindable(false) } = $props();

  interface LogEntry {
    time: string;
    level: string;
    logger: string;
    message: string;
  }

  let logs = $state<LogEntry[]>([]);
  let isLoading = $state(false);
  let filterLevel = $state('');
  let autoRefresh = $state(false);
  let newestFirst = $state(true);
  let refreshInterval: ReturnType<typeof setInterval> | null = null;
  
  let sortedLogs = $derived(newestFirst ? logs : [...logs].reverse());

  // Load logs when modal opens
  $effect(() => {
    if (open) {
      loadLogs();
    } else {
      // Stop auto-refresh when closed
      if (refreshInterval) {
        clearInterval(refreshInterval);
        refreshInterval = null;
      }
    }
  });

  // Handle auto-refresh toggle
  $effect(() => {
    if (autoRefresh && open) {
      refreshInterval = setInterval(loadLogs, 2000);
    } else if (refreshInterval) {
      clearInterval(refreshInterval);
      refreshInterval = null;
    }
  });

  async function loadLogs() {
    isLoading = true;
    try {
      const params = new URLSearchParams({ limit: '200' });
      if (filterLevel) params.set('level', filterLevel);
      const res = await fetch(`/api/logs?${params}`);
      if (res.ok) {
        const data = await res.json();
        logs = data.logs;
      }
    } catch (e) {
      console.error('Failed to load logs:', e);
    } finally {
      isLoading = false;
    }
  }

  function getLevelColor(level: string): string {
    switch (level) {
      case 'ERROR': return 'var(--color-error)';
      case 'WARNING': return 'var(--color-warning)';
      case 'INFO': return 'var(--color-primary)';
      case 'DEBUG': return 'var(--color-text-dim)';
      default: return 'var(--color-text-muted)';
    }
  }

  function formatTime(isoTime: string): string {
    const date = new Date(isoTime);
    return date.toLocaleTimeString('en-US', { hour12: false });
  }
</script>

<Modal {open} title="Server Logs" onclose={() => open = false} maxWidth="800px">
  <div class="logs-toolbar">
    <div class="filter-group">
      <label for="level-filter">Level:</label>
      <select id="level-filter" bind:value={filterLevel} onchange={loadLogs}>
        <option value="">All</option>
        <option value="ERROR">Error</option>
        <option value="WARNING">Warning</option>
        <option value="INFO">Info</option>
        <option value="DEBUG">Debug</option>
      </select>
    </div>
    
    <label class="auto-refresh">
      <input type="checkbox" bind:checked={autoRefresh} />
      Auto-refresh
    </label>
    
    <button class="btn btn-secondary btn-sm" onclick={() => newestFirst = !newestFirst}>
      {newestFirst ? '↓ Newest first' : '↑ Oldest first'}
    </button>
    
    <button class="btn btn-secondary btn-sm" onclick={loadLogs} disabled={isLoading}>
      <Icon name="refresh" size={14} />
      Refresh
    </button>
  </div>

  <div class="logs-container">
    {#if isLoading && sortedLogs.length === 0}
      <div class="logs-loading">Loading...</div>
    {:else if sortedLogs.length === 0}
      <div class="logs-empty">No logs found</div>
    {:else}
      {#each sortedLogs as log}
        <div class="log-entry">
          <span class="log-time">{formatTime(log.time)}</span>
          <span class="log-level" style="color: {getLevelColor(log.level)}">{log.level}</span>
          <span class="log-message">{log.message}</span>
        </div>
      {/each}
    {/if}
  </div>
</Modal>

<style>
  .logs-toolbar {
    display: flex;
    align-items: center;
    gap: 16px;
    margin-bottom: 12px;
    padding-bottom: 12px;
    border-bottom: 1px solid var(--color-border);
  }

  .filter-group {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .filter-group label {
    font-size: 13px;
    color: var(--color-text-muted);
  }

  .filter-group select {
    background: var(--color-surface);
    border: 1px solid var(--color-border);
    border-radius: var(--radius-sm);
    padding: 4px 8px;
    color: var(--color-text);
    font-size: 13px;
  }

  .auto-refresh {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 13px;
    color: var(--color-text-muted);
    cursor: pointer;
  }

  .auto-refresh input {
    cursor: pointer;
  }

  .btn-sm {
    padding: 4px 10px;
    font-size: 12px;
    display: flex;
    align-items: center;
    gap: 4px;
  }

  .logs-container {
    background: var(--color-bg);
    border-radius: var(--radius-md);
    padding: 12px;
    max-height: 400px;
    overflow-y: auto;
    font-family: 'SF Mono', 'Monaco', 'Inconsolata', monospace;
    font-size: 12px;
    line-height: 1.5;
  }

  .logs-loading,
  .logs-empty {
    text-align: center;
    padding: 40px;
    color: var(--color-text-muted);
  }

  .log-entry {
    display: flex;
    gap: 12px;
    padding: 4px 0;
    border-bottom: 1px solid var(--color-border);
  }

  .log-entry:last-child {
    border-bottom: none;
  }

  .log-time {
    color: var(--color-text-dim);
    flex-shrink: 0;
    width: 70px;
  }

  .log-level {
    flex-shrink: 0;
    width: 60px;
    font-weight: 600;
  }

  .log-message {
    color: var(--color-text);
    word-break: break-word;
    flex: 1;
  }
</style>
