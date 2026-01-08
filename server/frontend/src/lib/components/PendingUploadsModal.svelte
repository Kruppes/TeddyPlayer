<script lang="ts">
  import Modal from './Modal.svelte';
  import { pendingUploads, cancelPendingUpload } from '../stores/playback';
  import { showError } from '../stores/alerts';

  // Svelte 5: use $bindable for two-way binding
  let { open = $bindable(false) } = $props();

  async function handleCancel(uid: string, deviceId: string) {
    try {
      await cancelPendingUpload(uid, deviceId);
    } catch (e) {
      showError('Failed to cancel upload');
    }
  }

  function formatQueuedAt(isoDate: string): string {
    if (!isoDate) return '';
    const date = new Date(isoDate);
    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }

  function handleClose() {
    open = false;
  }
</script>

<Modal {open} title="Pending SD Card Uploads" maxWidth="500px" onclose={handleClose}>
  <p class="modal-description">
    Queued uploads will resume automatically when ESPuino reconnects.
  </p>

  {#if $pendingUploads.length === 0}
    <div class="pending-empty">No pending uploads</div>
  {:else}
    <div class="pending-list">
      {#each $pendingUploads as upload (upload.uid + (upload.espuino_ip || upload.device_id))}
        <div class="pending-item">
          <div class="pending-item-header">
            <div>
              <div class="pending-item-title">{upload.series}</div>
              <div class="pending-item-device">{upload.espuino_ip || upload.device_id}</div>
            </div>
          </div>
          <div class="pending-item-meta">
            {upload.episode}
            {#if upload.queued_at}
              <span class="pending-queued-at">Queued at {formatQueuedAt(upload.queued_at)}</span>
            {/if}
          </div>
          <div class="pending-item-actions">
            <button
              class="pending-cancel-btn"
              onclick={() => handleCancel(upload.uid, upload.espuino_ip || upload.device_id || '')}
            >
              Cancel
            </button>
          </div>
        </div>
      {/each}
    </div>
  {/if}
</Modal>

<style>
  .modal-description {
    color: var(--color-text-muted);
    font-size: 13px;
    margin-bottom: 16px;
  }

  .pending-list {
    display: flex;
    flex-direction: column;
    gap: 12px;
    max-height: 400px;
    overflow-y: auto;
  }

  .pending-item {
    background: var(--color-card);
    border-radius: var(--radius-md);
    padding: 14px;
    border-left: 3px solid var(--color-primary);
  }

  .pending-item-header {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    margin-bottom: 8px;
  }

  .pending-item-title {
    font-weight: 600;
    font-size: 14px;
  }

  .pending-item-device {
    font-size: 12px;
    color: var(--color-text-muted);
  }

  .pending-item-meta {
    font-size: 12px;
    color: var(--color-text-dim);
    margin-top: 4px;
  }

  .pending-queued-at {
    margin-left: 12px;
    color: var(--color-text-dim);
  }

  .pending-item-actions {
    display: flex;
    gap: 8px;
    margin-top: 10px;
  }

  .pending-cancel-btn {
    background: transparent;
    color: var(--color-text-muted);
    border: 1px solid var(--color-border);
    padding: 6px 12px;
    border-radius: 6px;
    cursor: pointer;
    font-size: 11px;
    transition: all 0.2s ease;
  }

  .pending-cancel-btn:hover {
    background: var(--color-error);
    color: white;
    border-color: var(--color-error);
  }

  .pending-empty {
    text-align: center;
    padding: 30px;
    color: var(--color-text-muted);
  }
</style>
