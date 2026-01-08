<script lang="ts">
  import type { Snippet } from 'svelte';

  // Svelte 5 props with children snippet and onclose callback
  let {
    open = false,
    title = '',
    maxWidth = '500px',
    onclose,
    children
  }: {
    open: boolean;
    title: string;
    maxWidth: string;
    onclose?: () => void;
    children?: Snippet;
  } = $props();

  function close() {
    onclose?.();
  }

  function handleBackdropClick() {
    close();
  }

  function handleKeydown(e: KeyboardEvent) {
    if (e.key === 'Escape' && open) {
      close();
    }
  }
</script>

<svelte:window onkeydown={handleKeydown} />

{#if open}
  <div class="modal">
    <div class="modal-backdrop" onclick={handleBackdropClick} role="presentation"></div>
    <div class="modal-content" style="max-width: {maxWidth}">
      <div class="modal-header">
        <span class="modal-title">{title}</span>
        <button class="modal-close" onclick={close} aria-label="Close">&times;</button>
      </div>
      <div class="modal-body">
        {@render children?.()}
      </div>
    </div>
  </div>
{/if}

<style>
  .modal {
    position: fixed;
    top: 0;
    left: 0;
    width: 100%;
    height: 100%;
    z-index: 200;
    display: flex;
    align-items: center;
    justify-content: center;
  }

  .modal-backdrop {
    position: absolute;
    top: 0;
    left: 0;
    width: 100%;
    height: 100%;
    background: rgba(0, 0, 0, 0.8);
  }

  .modal-content {
    position: relative;
    background: var(--color-surface);
    border-radius: var(--radius-xl);
    padding: 24px;
    border: 1px solid var(--color-border);
    width: 90%;
    max-height: 85vh;
    overflow-y: auto;
    box-shadow: var(--shadow-lg);
  }

  .modal-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 20px;
    padding-bottom: 16px;
    border-bottom: 1px solid var(--color-border);
  }

  .modal-title {
    font-size: 18px;
    font-weight: 600;
    color: var(--color-primary);
  }

  .modal-close {
    background: none;
    border: none;
    color: var(--color-text-muted);
    font-size: 24px;
    cursor: pointer;
    padding: 0;
    line-height: 1;
  }

  .modal-close:hover {
    color: var(--color-text);
  }

  .modal-body {
    /* Content styles handled by children */
  }
</style>
