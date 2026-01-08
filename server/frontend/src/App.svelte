<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import type { TonieTag } from './lib/types';

  // Components
  import Header from './lib/components/Header.svelte';
  import Footer from './lib/components/Footer.svelte';
  import AlertContainer from './lib/components/AlertContainer.svelte';
  import NowPlayingSection from './lib/components/NowPlayingSection.svelte';
  import PrefetchProgress from './lib/components/PrefetchProgress.svelte';
  import UploadsSection from './lib/components/UploadsSection.svelte';
  import RecentlyPlayed from './lib/components/RecentlyPlayed.svelte';
  import LibraryControls from './lib/components/LibraryControls.svelte';
  import CoverGrid from './lib/components/CoverGrid.svelte';

  // Modals
  import SettingsModal from './lib/components/SettingsModal.svelte';
  import ReadersModal from './lib/components/ReadersModal.svelte';
  import DetailsModal from './lib/components/DetailsModal.svelte';
  import PendingUploadsModal from './lib/components/PendingUploadsModal.svelte';
  import ConnectionsModal from './lib/components/ConnectionsModal.svelte';

  // Stores
  import {
    loadDevices,
    selectedDevice,
    starredDevices,
    selectFirstStarredDevice,
  } from './lib/stores/devices';
  import {
    loadLibrary,
    filteredLibrary,
    tagsCache,
    recentlyPlayed,
    hiddenItems,
    addToRecentlyPlayed,
  } from './lib/stores/library';
  import {
    startStreamPolling,
    stopStreamPolling,
    startPlayback,
  } from './lib/stores/playback';
  import {
    loadSettings,
    loadFeatureFlags,
    loadVersionInfo,
    startHealthPolling,
    stopHealthPolling,
  } from './lib/stores/settings';
  import { showError, showSuccess } from './lib/stores/alerts';

  // API
  import { getPreferences, savePreference } from './lib/api/client';

  // Modal state (Svelte 5 runes)
  let settingsOpen = $state(false);
  let readersOpen = $state(false);
  let detailsOpen = $state(false);
  let pendingUploadsOpen = $state(false);
  let connectionsOpen = $state(false);

  let selectedTag: TonieTag | null = $state(null);
  let libraryLoading = $state(true);

  onMount(async () => {
    // Load preferences from server
    try {
      const prefs = await getPreferences();
      if (prefs.starredDevices) starredDevices.set(prefs.starredDevices);
      if (prefs.recentlyPlayed) recentlyPlayed.set(prefs.recentlyPlayed);
      if (prefs.hiddenItems) hiddenItems.set(prefs.hiddenItems);
    } catch (e) {
      console.warn('Failed to load preferences, using defaults');
    }

    // Load initial data
    await Promise.all([
      loadSettings(),
      loadDevices(),
      loadFeatureFlags(),
      loadVersionInfo(),
    ]);

    // Auto-select first starred device after devices are loaded
    selectFirstStarredDevice();

    // Load library
    try {
      await loadLibrary();
    } finally {
      libraryLoading = false;
    }

    // Start polling
    startStreamPolling(1000);
    startHealthPolling(10000);
  });

  onDestroy(() => {
    stopStreamPolling();
    stopHealthPolling();
  });

  async function handlePlay(tag: TonieTag) {
    const displayName = tag.series || tag.title || 'audio';

    try {
      const result = await startPlayback(tag);
      if (result.success) {
        showSuccess(`Playing ${displayName}`);
        // Save to recently played in background (don't block/fail on this)
        addToRecentlyPlayed(tag.uid).catch(e => {
          console.warn('[App] Failed to save recently played:', e);
        });
      } else {
        showError(result.error || `Failed to play ${displayName}`);
      }
    } catch (e) {
      showError(`Failed to play ${displayName}`);
      console.error('[App] Play error:', e);
    }
  }

  // Open details modal
  function handleShowDetails(tag: TonieTag) {
    selectedTag = tag;
    detailsOpen = true;
  }

  // Handle play from details modal (Svelte 5 callback prop style)
  function handlePlayFromDetails(tag: TonieTag) {
    detailsOpen = false;
    handlePlay(tag);
  }

  // Modal open handlers
  function openSettings() {
    settingsOpen = true;
  }

  function openReaders() {
    readersOpen = true;
  }

  function openPendingUploads() {
    pendingUploadsOpen = true;
  }

  function openConnections() {
    connectionsOpen = true;
  }
</script>

<AlertContainer />

<Header
  onOpenSettings={openSettings}
  onOpenReaders={openReaders}
  onOpenPendingUploads={openPendingUploads}
  onOpenConnections={openConnections}
/>

<main class="main-content">
  <NowPlayingSection />

  <PrefetchProgress />

  <UploadsSection />

  <RecentlyPlayed onplay={handlePlay} />

  <LibraryControls />

  <CoverGrid
    tags={$filteredLibrary}
    loading={libraryLoading}
    onplay={handlePlay}
    ondetails={handleShowDetails}
  />
</main>

<Footer />

<!-- Modals -->
<SettingsModal bind:open={settingsOpen} />
<ReadersModal bind:open={readersOpen} />
<DetailsModal
  bind:open={detailsOpen}
  tag={selectedTag}
  onplay={handlePlayFromDetails}
/>
<PendingUploadsModal bind:open={pendingUploadsOpen} />
<ConnectionsModal bind:open={connectionsOpen} />

<style>
  .main-content {
    max-width: 1400px;
    margin: 0 auto;
    padding: 24px;
    flex: 1;
  }

  @media (max-width: 900px) {
    .main-content {
      padding: 16px;
    }
  }
</style>
