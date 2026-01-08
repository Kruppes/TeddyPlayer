<script lang="ts">
  import { alerts, dismissAlert } from '../stores/alerts';
</script>

<div class="alert-container">
  {#each $alerts as alert (alert.id)}
    <div class="alert {alert.type}" role="alert">
      <span class="alert-message">{alert.message}</span>
      <button class="alert-close" onclick={() => dismissAlert(alert.id)} aria-label="Dismiss">
        &times;
      </button>
    </div>
  {/each}
</div>

<style>
  .alert-container {
    position: fixed;
    top: 70px;
    right: 20px;
    z-index: 1000;
    max-width: 400px;
    display: flex;
    flex-direction: column;
    gap: 10px;
  }

  .alert {
    padding: 12px 16px;
    border-radius: var(--radius-md);
    animation: slideIn 0.3s ease;
    box-shadow: var(--shadow-md);
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
  }

  .alert.success {
    background: var(--color-success-bg);
    border: 1px solid var(--color-success);
    color: var(--color-success);
  }

  .alert.error {
    background: var(--color-error-bg);
    border: 1px solid var(--color-error);
    color: var(--color-error);
  }

  .alert.warning {
    background: var(--color-warning-bg);
    border: 1px solid var(--color-warning);
    color: var(--color-warning);
  }

  .alert-message {
    flex: 1;
    font-size: 14px;
  }

  .alert-close {
    background: none;
    border: none;
    color: inherit;
    font-size: 20px;
    cursor: pointer;
    padding: 0;
    line-height: 1;
    opacity: 0.7;
  }

  .alert-close:hover {
    opacity: 1;
  }

  @keyframes slideIn {
    from {
      transform: translateX(100%);
      opacity: 0;
    }
    to {
      transform: translateX(0);
      opacity: 1;
    }
  }
</style>
