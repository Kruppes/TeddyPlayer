<script lang="ts">
  import Modal from './Modal.svelte';
  import LogsModal from './LogsModal.svelte';
  import Icon from './Icon.svelte';
  import {
    settings,
    saveSettings,
    cacheInfo,
    loadCacheInfo,
    clearCache,
  } from '../stores/settings';
  
  let logsModalOpen = $state(false);
  import {
    devicesCache,
    starredDevices,
    toggleDeviceStar,
    removeDevice,
    discoverDevices,
    getDeviceKey,
    isDeviceStarred,
  } from '../stores/devices';
  import { showSuccess, showError } from '../stores/alerts';
  import type { DeviceType, Device } from '../types';

  // Svelte 5: use $bindable for two-way binding
  let { open = $bindable(false) } = $props();

  let teddycloudUrl = $state('');
  let serverUrl = $state('');
  let cacheMaxMb = $state(500);
  let isSaving = $state(false);
  let isDiscovering = $state(false);

  // Load current settings when modal opens (Svelte 5 effect)
  $effect(() => {
    if (open) {
      teddycloudUrl = $settings.teddycloud_url;
      serverUrl = $settings.server_url;
      cacheMaxMb = $settings.audio_cache_max_mb;
      loadCacheInfo();
    }
  });

  // Derived values (Svelte 5) - with safe fallbacks
  let cachePercent = $derived.by(() => {
    const info = $cacheInfo;
    if (!info || !info.max_mb) return 0;
    return (info.size_mb / info.max_mb) * 100;
  });

  let cacheBarColor = $derived(cachePercent > 90
    ? 'var(--color-error)'
    : cachePercent > 70
    ? 'var(--color-warning)'
    : 'var(--color-primary)');

  // Safe cache info accessors
  let cacheUsedMb = $derived($cacheInfo?.size_mb ?? 0);
  let cacheMaxMbDisplay = $derived($cacheInfo?.max_mb ?? 500);
  let cacheFolders = $derived($cacheInfo?.folders ?? 0);
  let cacheFiles = $derived($cacheInfo?.files ?? 0);

  async function handleSave() {
    isSaving = true;
    try {
      await saveSettings({
        teddycloud_url: teddycloudUrl,
        server_url: serverUrl,
        audio_cache_max_mb: cacheMaxMb,
      });
      // Refresh cache info to show updated max value
      await loadCacheInfo();
      showSuccess('Settings saved');
    } catch (e) {
      showError('Failed to save settings');
    } finally {
      isSaving = false;
    }
  }

  async function handleDiscover() {
    isDiscovering = true;
    try {
      const discovered = await discoverDevices();
      showSuccess(`Discovered ${discovered} new devices`);
    } catch (e) {
      showError('Failed to discover devices');
    } finally {
      isDiscovering = false;
    }
  }

  async function handleClearCache() {
    if (!confirm('Clear all cached audio files?')) return;
    try {
      const result = await clearCache();
      showSuccess(result.message);
    } catch (e) {
      showError('Failed to clear cache');
    }
  }

  function handleToggleStar(type: DeviceType, id: string) {
    toggleDeviceStar(type, id);
  }

  async function handleRemoveDevice(type: DeviceType, id: string, name: string) {
    if (!confirm(`Remove ${name}?`)) return;
    try {
      await removeDevice(type, id);
      showSuccess(`Removed ${name}`);
    } catch (e) {
      showError('Failed to remove device');
    }
  }

  // Build device list grouped by type (Svelte 5: access store inside $derived)
  let deviceGroups = $derived.by(() => {
    const cache = $devicesCache;
    if (!cache) return [];
    const groups: { type: DeviceType; label: string; devices: Device[] }[] = [];

    if (cache.sonos && cache.sonos.length > 0) {
      groups.push({
        type: 'sonos',
        label: 'Sonos',
        devices: cache.sonos.map(d => ({ type: 'sonos' as DeviceType, id: d.uid, name: d.name, ip: d.ip }))
      });
    }
    if (cache.airplay && cache.airplay.length > 0) {
      groups.push({
        type: 'airplay',
        label: 'AirPlay',
        devices: cache.airplay.map(d => ({ type: 'airplay' as DeviceType, id: d.id, name: d.name, ip: d.ip }))
      });
    }
    if (cache.chromecast && cache.chromecast.length > 0) {
      groups.push({
        type: 'chromecast',
        label: 'Chromecast',
        devices: cache.chromecast.map(d => ({ type: 'chromecast' as DeviceType, id: d.id, name: d.name, ip: d.ip }))
      });
    }
    if (cache.espuino && cache.espuino.length > 0) {
      groups.push({
        type: 'espuino',
        label: 'ESPuino',
        devices: cache.espuino.map(d => ({ type: 'espuino' as DeviceType, id: d.id, name: d.name, ip: d.ip }))
      });
    }

    return groups;
  });
</script>

<Modal {open} title="Settings" maxWidth="500px" onclose={() => open = false}>
  <div class="form-group">
    <label class="form-label" for="teddycloud-url">TeddyCloud URL</label>
    <input
      type="text"
      id="teddycloud-url"
      class="form-input"
      bind:value={teddycloudUrl}
      placeholder="http://your-teddycloud-ip:80"
    />
  </div>

  <div class="form-group">
    <label class="form-label" for="server-url">Server URL (for external devices)</label>
    <input
      type="text"
      id="server-url"
      class="form-input"
      bind:value={serverUrl}
      placeholder="http://your-server-ip:8754"
    />
    <div class="form-hint">Leave empty to auto-detect. Set explicitly if devices can't reach the server.</div>
  </div>

  <!-- Cache Settings -->
  <div class="settings-section">
    <div class="settings-section-title">Audio Cache</div>

    <div class="cache-usage-container">
      <div class="cache-usage-header">
        <span class="cache-usage-label">
          {cacheUsedMb.toFixed(0)} MB / {cacheMaxMbDisplay} MB used
        </span>
        <span class="cache-files-label">
          {cacheFolders} Tonies ({cacheFiles} tracks)
        </span>
      </div>
      <div class="cache-usage-bar">
        <div
          class="cache-usage-fill"
          style="width: {cachePercent}%; background: {cacheBarColor}"
        ></div>
      </div>
    </div>

    <div class="form-group">
      <label class="form-label" for="cache-max">Maximum Cache Size (MB)</label>
      <div class="cache-input-row">
        <input
          type="number"
          id="cache-max"
          class="form-input cache-input"
          bind:value={cacheMaxMb}
          min="100"
          max="10000"
          step="100"
        />
        <button class="btn btn-secondary" onclick={handleClearCache}>
          Clear Cache
        </button>
      </div>
      <div class="form-hint">Cached Tonies are evicted (oldest first) when limit is reached.</div>
    </div>
  </div>

  <div class="btn-group" style="margin-top: 20px;">
    <button class="btn btn-primary" onclick={handleSave} disabled={isSaving}>
      {isSaving ? 'Saving...' : 'Save'}
    </button>
    <button class="btn btn-secondary" onclick={handleDiscover} disabled={isDiscovering}>
      {isDiscovering ? 'Discovering...' : 'Discover Devices'}
    </button>
  </div>

  <!-- Device List -->
  {#if deviceGroups.length > 0}
    <div class="device-list">
      {#each deviceGroups as group}
        <div class="device-section">
          <div class="device-section-title">{group.label}</div>
          {#each group.devices as device}
            <div class="device-item">
              <button
                class="device-star"
                class:starred={isDeviceStarred($starredDevices, group.type, device.id)}
                onclick={() => handleToggleStar(group.type, device.id)}
                title="Star device"
              >
                <Icon name={isDeviceStarred($starredDevices, group.type, device.id) ? 'star' : 'star-outline'} size={18} />
              </button>
              <span class="device-name">{device.name}</span>
              {#if device.ip}
                <span class="device-ip">{device.ip}</span>
              {/if}
              <button
                class="device-remove"
                onclick={() => handleRemoveDevice(group.type, device.id, device.name)}
                title="Remove device"
              >
                ×
              </button>
            </div>
          {/each}
        </div>
      {/each}
    </div>
  {/if}

  <div class="settings-section">
    <div class="settings-section-title">Debugging</div>
    <button class="btn btn-secondary" onclick={() => logsModalOpen = true}>
      <Icon name="document-text" size={16} />
      View Server Logs
    </button>
  </div>
</Modal>

<LogsModal bind:open={logsModalOpen} />

<style>
  .settings-section {
    margin-top: 24px;
    padding-top: 20px;
    border-top: 1px solid var(--color-border);
  }

  .settings-section-title {
    font-weight: 600;
    margin-bottom: 16px;
    color: var(--color-text);
  }

  .cache-usage-container {
    margin-bottom: 16px;
  }

  .cache-usage-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 8px;
  }

  .cache-usage-label {
    font-size: 13px;
    color: var(--color-text-muted);
  }

  .cache-files-label {
    font-size: 12px;
    color: var(--color-text-muted);
  }

  .cache-usage-bar {
    height: 8px;
    background: var(--color-surface);
    border-radius: 4px;
    overflow: hidden;
  }

  .cache-usage-fill {
    height: 100%;
    transition: width 0.3s ease, background 0.3s ease;
  }

  .cache-input-row {
    display: flex;
    gap: 12px;
    align-items: center;
  }

  .cache-input {
    width: 120px;
    flex-shrink: 0;
  }

  .device-list {
    margin-top: 20px;
  }

  .device-section {
    margin-bottom: 16px;
  }

  .device-section-title {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 1px;
    color: var(--color-text-dim);
    margin-bottom: 8px;
  }

  .device-item {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 10px 12px;
    background: var(--color-card);
    border-radius: var(--radius-md);
    margin-bottom: 6px;
  }

  .device-item:hover {
    background: var(--color-card-hover);
  }

  .device-star {
    color: var(--color-text-dim);
    cursor: pointer;
    transition: color var(--transition-fast);
    background: none;
    border: none;
    padding: 0;
    display: flex;
    align-items: center;
  }

  .device-star:hover { color: var(--color-warning); }
  .device-star.starred { color: var(--color-warning); }

  .device-name {
    flex: 1;
    font-size: 13px;
  }

  .device-ip {
    font-size: 11px;
    color: var(--color-text-dim);
    font-family: monospace;
  }

  .device-remove {
    color: var(--color-text-dim);
    cursor: pointer;
    opacity: 0;
    transition: opacity var(--transition-fast);
    font-size: 18px;
    background: none;
    border: none;
    padding: 0;
  }

  .device-item:hover .device-remove {
    opacity: 1;
  }

  .device-remove:hover {
    color: var(--color-error);
  }
</style>
