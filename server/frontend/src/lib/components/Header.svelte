<script lang="ts">
  import {
    allDevices,
    selectedDevice,
    starredDevicesList,
  } from '../stores/devices';
  import { pendingUploads } from '../stores/playback';
  import { healthStatus, featureFlags } from '../stores/settings';
  import type { Device } from '../types';
  import Icon from './Icon.svelte';

  // Svelte 5 props with callback handlers
  let {
    onOpenSettings = () => {},
    onOpenReaders = () => {},
    onOpenPendingUploads = () => {},
    onOpenConnections = () => {}
  }: {
    onOpenSettings?: () => void;
    onOpenReaders?: () => void;
    onOpenPendingUploads?: () => void;
    onOpenConnections?: () => void;
  } = $props();

  // Get icon name for device type
  function getDeviceIconName(type: string): string {
    switch (type) {
      case 'browser': return 'browser';
      case 'sonos': return 'sonos';
      case 'airplay': return 'airplay';
      case 'chromecast': return 'chromecast';
      case 'espuino': return 'espuino';
      default: return 'sonos';
    }
  }

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

  // Svelte 5 derived values
  let pendingCount = $derived($pendingUploads?.length ?? 0);
  let showPendingBadge = $derived($featureFlags?.espuino_enabled && pendingCount > 0);

  function handleDeviceChange(e: Event) {
    const select = e.target as HTMLSelectElement;
    const [type, ...idParts] = select.value.split('|');
    const id = idParts.join('|');
    const device = $allDevices.find(d => d.type === type && d.id === id);
    if (device) {
      selectedDevice.set(device);
    }
  }
</script>

<header class="app-header">
  <div class="header-content">
    <div class="logo">
      <div class="logo-icon">T</div>
      <span>TeddyPlayer</span>
    </div>

    <div class="header-spacer"></div>

    <div class="header-device">
      <label for="device-select">Play on:</label>
      <select
        id="device-select"
        value="{$selectedDevice.type}|{$selectedDevice.id}"
        onchange={handleDeviceChange}
      >
        {#each $starredDevicesList as device}
          <option value="{device.type}|{device.id}">
            {getDeviceTypeLabel(device.type)}: {device.name}
          </option>
        {/each}
      </select>
    </div>

    <div class="header-actions">
      {#if $featureFlags.espuino_enabled}
        <button
          class="icon-btn"
          title="Pending Uploads"
          onclick={onOpenPendingUploads}
          style="position: relative;"
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
            <polyline points="17 8 12 3 7 8"/>
            <line x1="12" y1="3" x2="12" y2="15"/>
          </svg>
          {#if showPendingBadge}
            <span class="pending-badge">{pendingCount}</span>
          {/if}
        </button>
      {/if}

      <button
        class="icon-btn"
        title="Connected Readers"
        onclick={onOpenReaders}
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <rect x="2" y="6" width="20" height="12" rx="2"/>
          <line x1="6" y1="10" x2="6" y2="14"/>
          <line x1="18" y1="10" x2="18" y2="14"/>
        </svg>
      </button>

      <button
        class="icon-btn"
        title="Settings"
        onclick={onOpenSettings}
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <circle cx="12" cy="12" r="3"></circle>
          <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"></path>
        </svg>
      </button>

      <button
        class="icon-btn"
        title="Connections"
        onclick={onOpenConnections}
      >
        <div
          class="status-dot"
          class:loading={$healthStatus === 'loading'}
          class:offline={$healthStatus === 'offline'}
        ></div>
      </button>
    </div>
  </div>
</header>

<style>
  .app-header {
    background: var(--color-surface);
    border-bottom: 1px solid var(--color-border);
    padding: 12px 24px;
    position: sticky;
    top: 0;
    z-index: 100;
  }

  .header-content {
    max-width: 1400px;
    margin: 0 auto;
    display: flex;
    align-items: center;
    gap: 20px;
  }

  .logo {
    font-size: 20px;
    font-weight: 700;
    color: var(--color-primary);
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .logo-icon {
    width: 28px;
    height: 28px;
    background: var(--color-primary);
    border-radius: 6px;
    display: flex;
    align-items: center;
    justify-content: center;
    color: #000;
    font-size: 16px;
  }

  .header-spacer {
    flex: 1;
  }

  .header-device {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .header-device label {
    font-size: 12px;
    color: var(--color-text-muted);
  }

  .header-device select {
    background: var(--color-input);
    border: 1px solid var(--color-border);
    color: var(--color-text);
    padding: 8px 12px;
    border-radius: var(--radius-md);
    font-size: 13px;
    min-width: 200px;
    cursor: pointer;
  }

  .header-device select:hover {
    border-color: var(--color-primary);
  }

  .header-actions {
    display: flex;
    gap: 8px;
  }

  .pending-badge {
    position: absolute;
    top: -4px;
    right: -4px;
    background: var(--color-primary);
    color: #000;
    font-size: 10px;
    font-weight: 700;
    min-width: 16px;
    height: 16px;
    padding: 0 4px;
    border-radius: 8px;
    display: flex;
    align-items: center;
    justify-content: center;
  }

  /* Responsive - Mobile */
  @media (max-width: 600px) {
    .app-header {
      padding: 10px 12px;
    }

    .header-content {
      gap: 8px;
    }

    .logo span {
      display: none;
    }

    .logo-icon {
      width: 32px;
      height: 32px;
      font-size: 14px;
    }

    .header-spacer {
      flex: 0 0 auto;
      width: 8px;
    }

    .header-device {
      flex: 1;
      min-width: 0;
    }

    .header-device label {
      display: none;
    }

    .header-device select {
      min-width: 0;
      width: 100%;
    }
  }
</style>
