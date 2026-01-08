<script lang="ts">
  import { activeUploads, retryUpload, dismissUploadError, clearAllErrors, wipeAllUploads } from '../stores/playback';
  import { featureFlags } from '../stores/settings';
  import { showError, showSuccess } from '../stores/alerts';
  import type { Upload } from '../types';

  let uploads = $derived($activeUploads);
  let hasUploads = $derived(uploads.length > 0);
  let hasErrors = $derived(uploads.some(u => u.status === 'error'));
  let showSection = $derived($featureFlags.espuino_enabled && hasUploads);

  function formatBytes(bytes: number): string {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }

  function formatEta(seconds: number | undefined): string {
    if (!seconds || seconds <= 0) return '';
    if (seconds < 60) return `${Math.round(seconds)}s remaining`;
    const mins = Math.floor(seconds / 60);
    const secs = Math.round(seconds % 60);
    return `${mins}m ${secs}s remaining`;
  }

  async function handleRetry(upload: Upload) {
    try {
      await retryUpload(upload.uid, upload.device_id);
      showSuccess('Retrying upload...');
    } catch (e) {
      showError('Failed to retry upload');
    }
  }

  async function handleDismiss(upload: Upload) {
    try {
      await dismissUploadError(upload.uid, upload.device_id);
    } catch (e) {
      showError('Failed to dismiss error');
    }
  }

  async function handleClearAllErrors() {
    try {
      await clearAllErrors();
      showSuccess('All errors cleared');
    } catch (e) {
      showError('Failed to clear errors');
    }
  }

  async function handleWipeAll() {
    if (!confirm('Are you sure you want to cancel all uploads?')) return;
    try {
      await wipeAllUploads();
      showSuccess('All uploads cancelled');
    } catch (e) {
      showError('Failed to cancel uploads');
    }
  }
</script>

{#if showSection}
  <div class="uploads-section">
    <div class="section-header">
      <span class="section-title">Uploading to ESPuino SD</span>
    </div>

    {#if hasErrors || hasUploads}
      <div class="upload-global-actions">
        {#if hasErrors}
          <button class="clear-all-errors-btn" onclick={handleClearAllErrors}>
            Clear All Errors
          </button>
        {/if}
        <button class="wipe-all-btn" onclick={handleWipeAll}>
          Cancel All
        </button>
      </div>
    {/if}

    <div class="uploads-grid">
      {#each uploads as upload (upload.uid + upload.device_id)}
        <div
          class="upload-card"
          class:complete={upload.status === 'complete'}
          class:error={upload.status === 'error'}
        >
          <div class="upload-header">
            <div>
              <div class="upload-title">{upload.series}</div>
              <div class="upload-device">{upload.device_name}</div>
            </div>
          </div>

          {#if upload.status === 'error'}
            <div class="upload-error">{upload.error || 'Upload failed'}</div>
            <div class="upload-error-actions">
              <button class="retry-btn" onclick={() => handleRetry(upload)}>
                Retry
              </button>
              <button class="dismiss-btn" onclick={() => handleDismiss(upload)}>
                Dismiss
              </button>
            </div>
          {:else}
            <div class="upload-stats">
              <span>Track {upload.current_track}/{upload.total_tracks}</span>
              <span>{formatBytes(upload.bytes_sent)} / {formatBytes(upload.total_bytes)}</span>
              {#if upload.rate_kbps}
                <span class="upload-rate">{upload.rate_kbps} KB/s</span>
              {/if}
            </div>
            <div class="upload-progress">
              <div class="upload-progress-bar" style="width: {upload.progress}%"></div>
            </div>
            {#if upload.eta_seconds}
              <div class="upload-eta">{formatEta(upload.eta_seconds)}</div>
            {/if}
          {/if}
        </div>
      {/each}
    </div>
  </div>
{/if}

<style>
  .uploads-section {
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

  .uploads-grid {
    display: flex;
    flex-direction: column;
    gap: 12px;
  }

  .upload-card {
    background: var(--color-card);
    border-radius: var(--radius-md);
    padding: 16px;
    border: 2px solid var(--color-primary);
  }

  .upload-card.complete {
    border-color: var(--color-success);
  }

  .upload-card.error {
    border-color: var(--color-error);
  }

  .upload-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 12px;
  }

  .upload-title {
    font-weight: 600;
    color: var(--color-text);
    font-size: 14px;
  }

  .upload-device {
    font-size: 12px;
    color: var(--color-text-muted);
  }

  .upload-stats {
    display: flex;
    justify-content: space-between;
    font-size: 12px;
    color: var(--color-text-muted);
    margin-bottom: 8px;
  }

  .upload-rate {
    color: var(--color-primary);
    font-weight: 500;
  }

  .upload-progress {
    background: var(--color-surface);
    height: 8px;
    border-radius: 4px;
    overflow: hidden;
  }

  .upload-progress-bar {
    height: 100%;
    background: linear-gradient(90deg, var(--color-primary) 0%, var(--color-success) 100%);
    transition: width 0.3s ease;
  }

  .upload-eta {
    margin-top: 6px;
    font-size: 11px;
    color: var(--color-text-dim);
    text-align: right;
  }

  .upload-error {
    color: var(--color-error);
    font-size: 12px;
    margin-bottom: 8px;
  }

  .upload-error-actions {
    display: flex;
    gap: 8px;
    margin-top: 8px;
  }

  .retry-btn, .dismiss-btn {
    border: none;
    padding: 8px 16px;
    border-radius: 6px;
    cursor: pointer;
    font-size: 12px;
    font-weight: 500;
    transition: background 0.2s ease;
  }

  .retry-btn {
    background: var(--color-primary);
    color: white;
  }

  .retry-btn:hover {
    background: var(--color-primary-hover);
  }

  .dismiss-btn {
    background: var(--color-surface);
    color: var(--color-text-muted);
    border: 1px solid var(--color-border);
  }

  .dismiss-btn:hover {
    background: var(--color-error);
    color: white;
    border-color: var(--color-error);
  }

  .upload-global-actions {
    display: flex;
    gap: 8px;
    margin-bottom: 12px;
  }

  .clear-all-errors-btn {
    background: transparent;
    color: var(--color-text-muted);
    border: 1px solid var(--color-border);
    padding: 6px 12px;
    border-radius: 6px;
    cursor: pointer;
    font-size: 11px;
    transition: all 0.2s ease;
  }

  .clear-all-errors-btn:hover {
    background: var(--color-error);
    color: white;
    border-color: var(--color-error);
  }

  .wipe-all-btn {
    background: var(--color-error);
    color: white;
    border: 1px solid var(--color-error);
    padding: 6px 12px;
    border-radius: 6px;
    cursor: pointer;
    font-size: 11px;
    transition: all 0.2s ease;
  }

  .wipe-all-btn:hover {
    opacity: 0.9;
  }
</style>
