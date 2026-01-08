<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import type { ActiveStream } from '../types';
  import { reportPosition } from '../api/client';

  let { stream }: { stream: ActiveStream } = $props();

  let audio: HTMLAudioElement | undefined = $state(undefined);
  let currentTrackIndex = $state(0);
  let isPlaying = $state(false);
  let currentTime = $state(0);
  let duration = $state(0);
  let positionReporter: ReturnType<typeof setInterval> | null = null;
  let autoPlayAttempted = $state(false);
  let shouldPlayOnLoad = $state(false);  // Flag to trigger play after audio loads

  let tracks = $derived(stream.audio?.track_urls ?? []);
  let currentSrc = $derived(tracks[currentTrackIndex] ?? stream.audio?.playback_url);
  let totalTracks = $derived(stream.audio?.track_count ?? tracks.length);
  let isMultiTrack = $derived(totalTracks > 1);
  let isEncoding = $derived(
    stream.encoding?.status === 'encoding' ||
    (stream.encoding?.progress !== undefined && stream.encoding.progress < 100 && stream.encoding.progress > 0)
  );
  let encodingComplete = $derived(
    stream.encoding?.status === 'complete' ||
    stream.encoding?.status === 'cached' ||
    stream.encoding?.cached === true
  );

  // Auto-play when encoding completes and we have audio source
  $effect(() => {
    if (!autoPlayAttempted && currentSrc && (encodingComplete || !isEncoding) && audio) {
      autoPlayAttempted = true;
      shouldPlayOnLoad = true;
    }
  });

  // Called when browser has loaded enough audio data to play
  function handleCanPlay() {
    console.log('[BrowserPlayer] canplay event, shouldPlayOnLoad:', shouldPlayOnLoad);
    if (shouldPlayOnLoad) {
      shouldPlayOnLoad = false;
      audio?.play().catch(e => {
        console.log('[BrowserPlayer] Play blocked:', e.message);
      });
    }
  }

  onMount(() => {
    // Report position every 2 seconds
    positionReporter = setInterval(reportCurrentPosition, 2000);
  });

  onDestroy(() => {
    if (positionReporter) {
      clearInterval(positionReporter);
    }
    audio?.pause();
  });

  function reportCurrentPosition() {
    if (audio && isPlaying && stream.reader_ip && stream.tag?.uid) {
      reportPosition(stream.reader_ip, stream.tag.uid, audio.currentTime);
    }
  }

  function formatTime(seconds: number): string {
    if (isNaN(seconds) || !isFinite(seconds)) return '0:00';
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${mins}:${secs.toString().padStart(2, '0')}`;
  }

  function handlePlay() {
    if (!audio) return;
    if (audio.paused) {
      audio.play();
    } else {
      audio.pause();
    }
  }

  function handlePrev() {
    if (currentTrackIndex > 0) {
      shouldPlayOnLoad = true;  // Will auto-play when new track loads
      currentTrackIndex--;
    }
  }

  function handleNext() {
    if (currentTrackIndex < tracks.length - 1) {
      shouldPlayOnLoad = true;  // Will auto-play when new track loads
      currentTrackIndex++;
    }
  }

  function handleTrackEnded() {
    if (currentTrackIndex < tracks.length - 1) {
      handleNext();
    }
  }

  function handleSeek(e: MouseEvent) {
    const bar = e.currentTarget as HTMLElement;
    const rect = bar.getBoundingClientRect();
    const percent = (e.clientX - rect.left) / rect.width;
    if (audio && duration > 0) {
      audio.currentTime = percent * duration;
    }
  }

  function handleSeekKeydown(e: KeyboardEvent) {
    if (!audio || duration <= 0) return;
    const step = duration * 0.05; // 5% of duration
    if (e.key === 'ArrowRight') {
      audio.currentTime = Math.min(duration, audio.currentTime + step);
    } else if (e.key === 'ArrowLeft') {
      audio.currentTime = Math.max(0, audio.currentTime - step);
    }
  }
</script>

<div class="browser-player">
  <audio
    bind:this={audio}
    src={currentSrc}
    bind:currentTime
    bind:duration
    onloadstart={() => isPlaying = false}
    oncanplay={handleCanPlay}
    onended={handleTrackEnded}
    onplay={() => isPlaying = true}
    onpause={() => isPlaying = false}
  ></audio>

  {#if isMultiTrack}
    <div class="player-track-info">
      Track {currentTrackIndex + 1} / {totalTracks}
    </div>
  {/if}

  <div class="custom-player">
    {#if isMultiTrack}
      <button
        class="player-prev-btn"
        onclick={handlePrev}
        disabled={currentTrackIndex === 0}
        aria-label="Previous track"
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
          <polygon points="19,20 9,12 19,4"/>
          <rect x="5" y="4" width="2" height="16"/>
        </svg>
      </button>
    {/if}

    <button class="player-play-btn" onclick={handlePlay} aria-label={isPlaying ? 'Pause' : 'Play'}>
      {#if isPlaying}
        <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
          <rect x="6" y="4" width="4" height="16"/>
          <rect x="14" y="4" width="4" height="16"/>
        </svg>
      {:else}
        <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
          <polygon points="5,3 19,12 5,21"/>
        </svg>
      {/if}
    </button>

    {#if isMultiTrack}
      <button
        class="player-next-btn"
        onclick={handleNext}
        disabled={currentTrackIndex >= tracks.length - 1}
        aria-label="Next track"
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
          <polygon points="5,4 15,12 5,20"/>
          <rect x="17" y="4" width="2" height="16"/>
        </svg>
      </button>
    {/if}

    <div class="player-progress-container">
      <div
        class="player-progress-bar"
        onclick={handleSeek}
        onkeydown={handleSeekKeydown}
        role="slider"
        tabindex="0"
        aria-label="Seek"
        aria-valuemin="0"
        aria-valuemax={duration}
        aria-valuenow={currentTime}
      >
        <div
          class="player-progress-fill"
          style="width: {duration > 0 ? (currentTime / duration) * 100 : 0}%"
        ></div>
      </div>
      <div class="player-time">
        <span>{formatTime(currentTime)}</span>
        <span>{formatTime(duration)}</span>
      </div>
    </div>
  </div>
</div>

<style>
  .browser-player {
    margin-top: 12px;
  }

  .browser-player audio {
    display: none;
  }

  .custom-player {
    display: flex;
    align-items: center;
    gap: 12px;
  }

  .player-play-btn {
    width: 36px;
    height: 36px;
    border-radius: 50%;
    background: var(--color-primary);
    border: none;
    color: #000;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
    transition: all var(--transition-fast);
  }

  .player-play-btn:hover {
    background: var(--color-primary-hover);
    transform: scale(1.05);
  }

  .player-prev-btn, .player-next-btn {
    width: 28px;
    height: 28px;
    border-radius: 50%;
    background: var(--color-surface);
    border: 1px solid var(--color-border);
    color: var(--color-text);
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
    transition: all var(--transition-fast);
  }

  .player-prev-btn:hover, .player-next-btn:hover {
    border-color: var(--color-primary);
  }

  .player-prev-btn:disabled, .player-next-btn:disabled {
    opacity: 0.3;
    cursor: not-allowed;
  }

  .player-progress-container {
    flex: 1;
    display: flex;
    flex-direction: column;
    gap: 4px;
    min-width: 0;
  }

  .player-progress-bar {
    position: relative;
    background: var(--color-surface);
    height: 8px;
    border-radius: 4px;
    cursor: pointer;
    overflow: hidden;
  }

  .player-progress-bar:hover {
    height: 10px;
  }

  .player-progress-fill {
    position: absolute;
    left: 0;
    top: 0;
    height: 100%;
    background: linear-gradient(90deg, var(--color-primary) 0%, var(--color-warning) 100%);
    border-radius: 4px;
    pointer-events: none;
    transition: width 0.1s linear;
  }

  .player-time {
    display: flex;
    justify-content: space-between;
    font-size: 11px;
    color: var(--color-text-dim);
    font-family: monospace;
  }

  .player-track-info {
    font-size: 11px;
    color: var(--color-primary);
    margin-bottom: 4px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
</style>
