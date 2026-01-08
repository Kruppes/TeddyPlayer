<script lang="ts">
  import type { ActiveStream } from "../types";
  import BrowserPlayer from "./BrowserPlayer.svelte";
  import Icon from "./Icon.svelte";
  import { stopStream, pauseStream, resumeStream, nextTrackStream, prevTrackStream } from "../stores/playback";
  import { settings } from "../stores/settings";
  import { getImageUrl } from "../api/client";
  import { showError } from "../stores/alerts";

  let { stream }: { stream: ActiveStream } = $props();

  let isBrowser = $derived(stream.device?.type === "browser");
  let isEncoding = $derived(
    stream.encoding?.status === "encoding" ||
      (stream.encoding?.progress !== undefined &&
        stream.encoding.progress < 100 &&
        stream.encoding.progress > 0),
  );
  let encodingProgress = $derived(stream.encoding?.progress ?? 0);
  let encodingText = $derived((() => {
    const enc = stream.encoding;
    if (!enc) return "Encoding...";
    
    const current = enc.current_track ?? 0;
    const total = enc.total_tracks ?? 0;
    const progress = enc.progress ?? 0;
    
    if (total > 1 && current > 0) {
      return `Encoding track ${current}/${total} (${Math.round(progress)}%)`;
    }
    return `Encoding... ${Math.round(progress)}%`;
  })());
  let coverUrl = $derived(
    getImageUrl(stream.tag?.picture, $settings.teddycloud_url),
  );
  let coverLoadFailed = $state(false);
  let showCover = $derived(coverUrl && !coverLoadFailed);
  let sourceName = $derived(stream.reader_name || stream.reader_ip);
  
  let trackCount = $derived(stream.audio?.track_count ?? stream.tag?.tracks?.length ?? 1);
  let isMultiTrack = $derived(trackCount > 1);
  
  let currentTrack = $derived((() => {
    const uri = stream.transport?.uri ?? '';
    const match = uri.match(/\/(\d+)\.mp3/);
    return match ? parseInt(match[1], 10) : 1;
  })());

  function handleCoverError() {
    coverLoadFailed = true;
  }
  let targetLabel = $derived((() => {
    const type = stream.device?.type || 'browser';
    const name = stream.device?.name;
    // Use friendly name if available
    if (name) {
      if (type === 'sonos') return `Sonos ${name}`;
      if (type === 'airplay') return `AirPlay ${name}`;
      if (type === 'chromecast') return `Chromecast ${name}`;
      if (type === 'espuino') return `ESPuino ${name}`;
      return name;
    }
    // Fallback to type
    if (type === 'browser') return 'Browser';
    return type.charAt(0).toUpperCase() + type.slice(1);
  })());

  async function handleStop() {
    try {
      await stopStream(stream.reader_ip);
    } catch (e) {
      showError("Failed to stop stream");
    }
  }

  function formatTransportState(): string {
    if (!stream.transport) return "";
    const state = stream.transport.state;
    const pos = formatTime(stream.transport.position);
    const dur = formatTime(stream.transport.duration);
    return `${state} - ${pos} / ${dur}`;
  }

  function formatTime(seconds: number): string {
    if (!seconds || isNaN(seconds)) return "0:00";
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${mins}:${secs.toString().padStart(2, "0")}`;
  }
</script>

<div class="stream-card" class:encoding={isEncoding}>
  <button class="stream-close-btn" onclick={handleStop} aria-label="Stop">
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      stroke-width="2"
    >
      <line x1="18" y1="6" x2="6" y2="18" />
      <line x1="6" y1="6" x2="18" y2="18" />
    </svg>
  </button>

  <div class="stream-content">
    {#if showCover}
      <img class="stream-cover" src={coverUrl} alt="" onerror={handleCoverError} />
    {:else}
      <div class="stream-cover-placeholder">
        <Icon name="teddy" size={32} />
      </div>
    {/if}

    <div class="stream-info">
      <div class="stream-title">
        {stream.tag?.series ?? stream.tag?.title ?? "Unknown"}
      </div>
      <div class="stream-meta">{stream.tag?.episode ?? ""}</div>
      <div class="stream-device">
        <span class="device-source">{sourceName}</span>
        <span class="device-arrow">→</span>
        <span class="device-target">{targetLabel}</span>
      </div>

      {#if stream.error}
        <div class="stream-error">{stream.error}</div>
      {:else if isEncoding}
        <div class="stream-encoding">
          <span>{encodingText}</span>
          <div class="stream-progress">
            <div
              class="stream-progress-bar"
              style="width: {encodingProgress}%"
            ></div>
          </div>
        </div>
      {:else if isBrowser}
        <BrowserPlayer {stream} />
      {:else if stream.transport}
        {#if isMultiTrack}
          <div class="stream-track-info">
            Track {currentTrack} / {trackCount}
          </div>
        {/if}
        <div class="stream-transport">
          <div class="transport-controls">
            {#if isMultiTrack}
              <button
                class="transport-btn"
                onclick={() => prevTrackStream(stream.reader_ip)}
                aria-label="Previous Track"
              >
                <Icon name="skip-previous" size={16} />
              </button>
            {/if}
            {#if stream.transport.state === "playing"}
              <button
                class="transport-btn"
                onclick={() => pauseStream(stream.reader_ip)}
                aria-label="Pause"
              >
                <Icon name="pause" size={20} />
              </button>
            {:else}
              <button
                class="transport-btn"
                onclick={() => resumeStream(stream.reader_ip)}
                aria-label="Play"
              >
                <Icon name="play" size={20} />
              </button>
            {/if}
            {#if isMultiTrack}
              <button
                class="transport-btn"
                onclick={() => nextTrackStream(stream.reader_ip)}
                aria-label="Next Track"
              >
                <Icon name="skip-next" size={16} />
              </button>
            {/if}
            <div class="transport-info">
              <span class="transport-time"
                >{formatTime(stream.transport.position)}</span
              >
              <div class="transport-progress">
                <div
                  class="transport-progress-bar"
                  style="width: {stream.transport.duration
                    ? (stream.transport.position / stream.transport.duration) *
                      100
                    : 0}%"
                ></div>
              </div>
              <span class="transport-time"
                >{formatTime(stream.transport.duration)}</span
              >
            </div>
          </div>
        </div>
      {/if}
    </div>
  </div>
</div>

<style>
  .stream-card {
    background: var(--color-card);
    border-radius: var(--radius-lg);
    padding: 16px;
    border: 2px solid var(--color-primary);
    position: relative;
    transition: all var(--transition-fast);
  }

  .stream-card.encoding {
    border-color: var(--color-warning);
  }

  .stream-close-btn {
    position: absolute;
    top: 10px;
    right: 10px;
    width: 28px;
    height: 28px;
    border-radius: 50%;
    background: rgba(0, 0, 0, 0.6);
    border: none;
    color: var(--color-text-muted);
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: all var(--transition-fast);
  }

  .stream-close-btn:hover {
    background: var(--color-error);
    color: #fff;
  }

  .stream-content {
    display: flex;
    gap: 16px;
  }

  .stream-cover {
    width: 120px;
    height: 120px;
    border-radius: var(--radius-md);
    object-fit: cover;
    flex-shrink: 0;
  }

  .stream-cover-placeholder {
    width: 120px;
    height: 120px;
    border-radius: var(--radius-md);
    background: var(--color-surface);
    display: flex;
    align-items: center;
    justify-content: center;
    color: var(--color-text-dim);
    flex-shrink: 0;
  }

  .stream-info {
    flex: 1;
    min-width: 0;
  }

  .stream-title {
    font-size: 16px;
    font-weight: 600;
    color: var(--color-primary);
    margin-bottom: 4px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .stream-meta {
    font-size: 13px;
    color: var(--color-text-muted);
    margin-bottom: 4px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .stream-device {
    font-size: 12px;
    color: var(--color-text-dim);
    display: flex;
    align-items: center;
    gap: 6px;
  }

  .device-source {
    color: var(--color-text-muted);
  }

  .device-arrow {
    color: var(--color-text-dim);
    font-size: 10px;
  }

  .device-target {
    color: var(--color-primary);
    font-weight: 500;
  }

  .stream-error {
    margin-top: 8px;
    font-size: 11px;
    color: var(--color-error);
  }

  .stream-track-info {
    font-size: 12px;
    color: var(--color-primary);
    margin-top: 8px;
    margin-bottom: 4px;
  }

  .stream-encoding {
    margin-top: 12px;
  }

  .stream-encoding span {
    font-size: 12px;
    color: var(--color-warning);
  }

  .stream-progress {
    background: var(--color-surface);
    height: 6px;
    border-radius: 3px;
    margin-top: 6px;
    overflow: hidden;
  }

  .stream-progress-bar {
    height: 100%;
    background: linear-gradient(
      90deg,
      var(--color-warning) 0%,
      var(--color-primary) 100%
    );
    transition: width 0.5s ease;
    border-radius: 3px;
  }

  .stream-transport {
    margin-top: 12px;
  }

  .transport-controls {
    display: flex;
    align-items: center;
    gap: 12px;
  }

  .transport-btn {
    width: 32px;
    height: 32px;
    border-radius: 50%;
    background: var(--color-surface);
    border: 1px solid var(--color-border);
    color: var(--color-primary);
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    transition: all var(--transition-fast);
  }

  .transport-btn:hover {
    background: var(--color-primary);
    color: #fff;
    border-color: var(--color-primary);
  }

  .transport-info {
    flex: 1;
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 12px;
    color: var(--color-text-muted);
  }

  .transport-progress {
    flex: 1;
    height: 4px;
    background: var(--color-surface);
    border-radius: 2px;
    overflow: hidden;
  }

  .transport-progress-bar {
    height: 100%;
    background: var(--color-primary);
    border-radius: 2px;
    transition: width 1s linear;
  }

  .transport-time {
    min-width: 35px;
    text-align: center;
  }
</style>
