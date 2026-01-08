<script lang="ts">
  import { activeStreams } from '../stores/playback';
  import StreamCard from './StreamCard.svelte';

  let hasStreams = $derived($activeStreams.length > 0);
</script>

{#if hasStreams}
  <div class="now-playing-section">
    <div class="section-header">
      <span class="section-title">Now Playing</span>
      <span class="stream-count">{$activeStreams.length} active</span>
    </div>

    <div class="streams-list">
      {#each $activeStreams as stream (stream.reader_ip + (stream.tag?.uid ?? ''))}
        <StreamCard {stream} />
      {/each}
    </div>
  </div>
{/if}

<style>
  .now-playing-section {
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

  .stream-count {
    font-size: 12px;
    color: var(--color-text-muted);
    background: var(--color-surface);
    padding: 4px 8px;
    border-radius: var(--radius-sm);
  }

  .streams-list {
    display: flex;
    flex-direction: column;
    gap: 12px;
  }
</style>
