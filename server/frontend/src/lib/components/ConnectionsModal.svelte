<script lang="ts">
  import Modal from './Modal.svelte';
  import { healthStatus, versionInfo } from '../stores/settings';

  let { open = $bindable(false) } = $props();

  interface Connection {
    name: string;
    status: 'online' | 'offline' | 'loading';
    details?: string;
  }

  let connections = $derived.by((): Connection[] => {
    return [
      {
        name: 'TeddyCloud',
        status: $healthStatus,
        details: $healthStatus === 'online' 
          ? `Connected${$versionInfo?.git_commit ? ` (${$versionInfo.git_commit})` : ''}`
          : $healthStatus === 'loading' 
            ? 'Checking...'
            : 'Unable to connect'
      }
    ];
  });

  let allOnline = $derived(connections.every(c => c.status === 'online'));
  let anyLoading = $derived(connections.some(c => c.status === 'loading'));

  function getStatusColor(status: 'online' | 'offline' | 'loading'): string {
    switch (status) {
      case 'online': return 'var(--color-success)';
      case 'offline': return 'var(--color-error)';
      case 'loading': return 'var(--color-warning)';
    }
  }

  function getStatusText(status: 'online' | 'offline' | 'loading'): string {
    switch (status) {
      case 'online': return 'Connected';
      case 'offline': return 'Disconnected';
      case 'loading': return 'Checking...';
    }
  }
</script>

<Modal {open} title="Connections" maxWidth="400px" onclose={() => open = false}>
  <div class="connections-list">
    {#each connections as conn}
      <div class="connection-item">
        <div class="connection-info">
          <div class="connection-name">{conn.name}</div>
          {#if conn.details}
            <div class="connection-details">{conn.details}</div>
          {/if}
        </div>
        <div class="connection-status">
          <span 
            class="status-indicator"
            style="background: {getStatusColor(conn.status)}"
          ></span>
          <span class="status-text">{getStatusText(conn.status)}</span>
        </div>
      </div>
    {/each}
  </div>

  <div class="connections-summary">
    {#if anyLoading}
      <span class="summary-loading">Checking connections...</span>
    {:else if allOnline}
      <span class="summary-ok">All systems operational</span>
    {:else}
      <span class="summary-error">Some connections failed</span>
    {/if}
  </div>
</Modal>

<style>
  .connections-list {
    display: flex;
    flex-direction: column;
    gap: 12px;
  }

  .connection-item {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 12px 16px;
    background: var(--color-card);
    border-radius: var(--radius-md);
    border: 1px solid var(--color-border);
  }

  .connection-info {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .connection-name {
    font-weight: 600;
    color: var(--color-text);
  }

  .connection-details {
    font-size: 12px;
    color: var(--color-text-muted);
  }

  .connection-status {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .status-indicator {
    width: 10px;
    height: 10px;
    border-radius: 50%;
  }

  .status-text {
    font-size: 13px;
    color: var(--color-text-muted);
  }

  .connections-summary {
    margin-top: 16px;
    padding-top: 16px;
    border-top: 1px solid var(--color-border);
    text-align: center;
    font-size: 13px;
  }

  .summary-ok {
    color: var(--color-success);
  }

  .summary-error {
    color: var(--color-error);
  }

  .summary-loading {
    color: var(--color-warning);
  }
</style>
