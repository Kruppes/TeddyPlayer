<script lang="ts">
  import type { TonieTag } from '../types';
  import CoverCard from './CoverCard.svelte';

  // Svelte 5 props with callback handlers
  let {
    tags = [],
    loading = false,
    onplay,
    ondetails
  }: {
    tags: TonieTag[];
    loading: boolean;
    onplay?: (tag: TonieTag) => void;
    ondetails?: (tag: TonieTag) => void;
  } = $props();
</script>

<div class="cover-grid">
  {#if loading}
    <div class="empty-state">
      <div class="empty-icon">...</div>
      <div class="empty-text">Loading library...</div>
    </div>
  {:else if tags.length === 0}
    <div class="empty-state">
      <div class="empty-icon"></div>
      <div class="empty-text">No Tonies found</div>
    </div>
  {:else}
    {#each tags as tag (tag.uid)}
      <CoverCard
        {tag}
        onplay={onplay}
        ondetails={ondetails}
      />
    {/each}
  {/if}
</div>

<style>
  .cover-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
    gap: 20px;
  }

  @media (min-width: 1300px) {
    .cover-grid {
      grid-template-columns: repeat(5, 1fr);
    }
  }

  @media (max-width: 900px) {
    .cover-grid {
      grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
      gap: 16px;
    }
  }

  @media (max-width: 380px) {
    .cover-grid {
      grid-template-columns: repeat(2, 1fr);
      gap: 10px;
    }
  }
</style>
