<script lang="ts">
  import Modal from './Modal.svelte';
  import { readers, loadReaders } from '../stores/settings';
  import { starredDevicesList } from '../stores/devices';
  import * as api from '../api/client';
  import { showSuccess, showError } from '../stores/alerts';

  // Svelte 5: use $bindable for two-way binding
  let { open = $bindable(false) } = $props();

  // Refresh readers when modal opens (Svelte 5 effect)
  $effect(() => {
    if (open) {
      loadReaders();
    }
  });

  // Get device type label
  function getDeviceTypeLabel(type: string): string {
    switch (type) {
      case 'browser': return 'Browser';
      case 'sonos': return 'Sonos';
      case 'airplay': return 'AirPlay';
      case 'chromecast': return 'Chromecast';
      case 'espuino': return 'ESPuino';
      default: return type;
    }
  }

  // Build device options for dropdown - only starred devices
  let deviceOptions = $derived.by(() => {
    const options = [{ label: 'Default (global)', value: 'default|' }];

    for (const device of $starredDevicesList) {
      options.push({
        label: `${getDeviceTypeLabel(device.type)}: ${device.name}`,
        value: `${device.type}|${device.id}`
      });
    }

    return options;
  });

  function formatLastSeen(isoDate: string): string {
    if (!isoDate) return 'Never';
    const date = new Date(isoDate);
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffSec = Math.floor(diffMs / 1000);

    if (diffSec < 60) return 'Just now';
    if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
    if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h ago`;
    return date.toLocaleDateString();
  }

  function getStatusClass(reader: { online?: boolean; last_seen?: string }): string {
    const ago = reader.last_seen
      ? Math.floor((Date.now() - new Date(reader.last_seen).getTime()) / 1000)
      : Infinity;
    const isOnline = reader.online !== false && ago < 60;
    return isOnline ? 'online' : 'offline';
  }

  function getSelectedValue(reader: { device?: { type?: string; id?: string } }): string {
    if (!reader.device?.type) return 'default|';
    return `${reader.device.type}|${reader.device.id || ''}`;
  }

  async function handleDeviceChange(readerIp: string, value: string) {
    const [type, id] = value.split('|');
    try {
      if (type === 'default') {
        await api.clearReaderDevice(readerIp);
      } else {
        await api.setReaderDevice(readerIp, type, id);
      }
      showSuccess('Reader device saved');
      await loadReaders();
    } catch (e) {
      showError('Failed to save reader device');
    }
  }

  async function handleRename(readerIp: string, currentName: string) {
    const newName = prompt('Enter new name:', currentName);
    if (!newName || newName === currentName) return;

    try {
      await api.renameReader(readerIp, newName);
      showSuccess('Reader renamed');
      await loadReaders();
    } catch (e) {
      showError('Failed to rename reader');
    }
  }

  async function handleForget(readerIp: string, name: string) {
    if (!confirm(`Forget reader "${name}"?`)) return;

    try {
      await api.forgetReader(readerIp);
      showSuccess('Reader forgotten');
      await loadReaders();
    } catch (e) {
      showError('Failed to forget reader');
    }
  }

  function handleClose() {
    open = false;
  }
</script>

<Modal {open} title="Connected Readers" onclose={handleClose}>
  {#if $readers.length === 0}
    <div class="empty-state" style="padding: 30px;">
      <div class="empty-text">No readers connected</div>
    </div>
  {:else}
    <div class="readers-list">
      {#each $readers as reader (reader.ip)}
        <div class="reader-item">
          <div class="reader-status {getStatusClass(reader)}"></div>
          <div class="reader-info">
            <button
              class="reader-name"
              onclick={() => handleRename(reader.ip, reader.name || reader.ip)}
              title="Click to rename"
            >
              {reader.name || reader.ip}
            </button>
            <div class="reader-meta">
              <span class="reader-ip">{reader.ip}</span>
              <span class="reader-last-seen">{formatLastSeen(reader.last_seen)}</span>
            </div>
          </div>
          <select
            class="reader-device-select"
            value={getSelectedValue(reader)}
            onchange={(e) => handleDeviceChange(reader.ip, (e.target as HTMLSelectElement).value)}
          >
            {#each deviceOptions as opt}
              <option value={opt.value}>{opt.label}</option>
            {/each}
          </select>
          <button
            class="reader-forget-btn"
            onclick={() => handleForget(reader.ip, reader.name || reader.ip)}
            title="Forget reader"
          >
            ×
          </button>
        </div>
      {/each}
    </div>
  {/if}
</Modal>

<style>
  .readers-list {
    display: flex;
    flex-direction: column;
    gap: 12px;
  }

  .reader-item {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 14px;
    background: var(--color-card);
    border-radius: var(--radius-md);
  }

  .reader-status {
    width: 10px;
    height: 10px;
    border-radius: 50%;
    flex-shrink: 0;
  }

  .reader-status.online {
    background: var(--color-success);
  }

  .reader-status.offline {
    background: var(--color-text-dim);
  }

  .reader-info {
    flex: 1;
    min-width: 0;
  }

  .reader-name {
    font-weight: 600;
    font-size: 14px;
    margin-bottom: 4px;
    background: none;
    border: none;
    padding: 0;
    color: var(--color-text);
    cursor: pointer;
    text-align: left;
  }

  .reader-name:hover {
    color: var(--color-primary);
  }

  .reader-meta {
    display: flex;
    gap: 12px;
    font-size: 12px;
    color: var(--color-text-muted);
  }

  .reader-ip {
    font-family: monospace;
  }

  .reader-device-select {
    background: var(--color-input);
    border: 1px solid var(--color-border);
    color: var(--color-text);
    padding: 8px 12px;
    border-radius: var(--radius-md);
    font-size: 12px;
    min-width: 150px;
    cursor: pointer;
  }

  .reader-device-select:hover {
    border-color: var(--color-primary);
  }

  .reader-forget-btn {
    width: 28px;
    height: 28px;
    border-radius: 50%;
    background: transparent;
    border: 1px solid var(--color-border);
    color: var(--color-text-dim);
    font-size: 18px;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
    transition: all var(--transition-fast);
  }

  .reader-forget-btn:hover {
    background: var(--color-error);
    border-color: var(--color-error);
    color: #fff;
  }
</style>
