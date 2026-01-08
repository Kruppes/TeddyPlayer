// Device types
export type DeviceType = 'browser' | 'sonos' | 'airplay' | 'chromecast' | 'espuino';

export interface Device {
  type: DeviceType;
  id: string;
  name: string;
  ip?: string;
}

// Raw device types from API (different field names per type)
export interface SonosDevice {
  name: string;
  ip: string;
  uid: string;  // Sonos uses uid as identifier
  model?: string;
  online?: boolean;
}

export interface AirPlayDevice {
  name: string;
  id: string;
  ip: string;
  address?: string;
  model?: string;
  online?: boolean;
}

export interface ChromecastDevice {
  name: string;
  id: string;
  ip: string;
  port?: number;
  model?: string;
  online?: boolean;
}

export interface ESPuinoDevice {
  name: string;
  id: string;
  ip: string;
}

export interface DevicesCache {
  sonos: SonosDevice[];
  airplay: AirPlayDevice[];
  chromecast: ChromecastDevice[];
  espuino: ESPuinoDevice[];
}

// Library/Tonie types
export interface Track {
  name: string;
  duration: number;
  start?: number;
}

export interface TonieTag {
  uid: string;
  name: string;
  path: string;
  folder: string;
  size: number;
  size_mb: number;
  date: number;
  series: string;
  episode: string;
  title: string;
  picture: string;
  model: string;
  language: string;
  valid: boolean;
  audio_id: number;
  duration: number;
  num_tracks: number;
  tracks: Track[];
  audio_url: string;
  cached?: boolean;
}

export interface LibraryResponse {
  count: number;
  files: TonieTag[];
}

// Stream/Playback types
export interface AudioInfo {
  source_url?: string;
  playback_url?: string;
  track_urls?: string[];
  is_multi_track?: boolean;
  track_count?: number;
  track_metadata?: TrackMetadata[];
}

export interface TrackMetadata {
  title: string;
  duration: number;
}

export interface EncodingInfo {
  status: 'unknown' | 'encoding' | 'complete' | 'cached' | 'ready' | 'error' | 'partial';
  cached?: boolean;
  progress?: number;
  total_tracks?: number;
  tracks_completed?: number;
  current_track?: number;
  elapsed_seconds?: number;
  file_size_mb?: number;
  error?: string;
}

export interface TransportState {
  state: 'playing' | 'paused' | 'stopped';
  position: number;
  duration: number;
  uri?: string;
}

export interface StreamTag {
  uid: string;
  title?: string;
  series?: string;
  episode?: string;
  picture?: string;
  placed_at?: string;
  start_position?: number;
  duration?: number;
  tracks?: Track[];
}

export interface StreamDevice {
  type: DeviceType;
  id: string;
  name?: string;
}

export interface ActiveStream {
  reader_ip: string;
  reader_name?: string;
  tag: StreamTag;
  audio?: AudioInfo;
  device?: StreamDevice;
  encoding?: EncodingInfo;
  transport?: TransportState;
  error?: string;
}

// Upload types (ESPuino)
export interface Upload {
  uid: string;
  device_id: string;
  device_name: string;
  series: string;
  episode: string;
  progress: number;
  current_track: number;
  total_tracks: number;
  bytes_sent: number;
  total_bytes: number;
  rate_kbps?: number;
  eta_seconds?: number;
  status: 'uploading' | 'complete' | 'error' | 'pending';
  error?: string;
}

export interface PendingUpload {
  uid: string;
  espuino_ip?: string;
  device_id?: string;
  device_name?: string;
  series: string;
  episode: string;
  queued_at: string;
  status?: string;
  tracks_total?: number;
  folder_path?: string;
}

// Reader types
export interface Reader {
  ip: string;
  name: string;
  first_seen: string;
  last_seen: string;
  scan_count: number;
  online: boolean;
  current_tag: string | null;
  device?: {
    type: DeviceType;
    id: string;
  };
  default_device?: {
    type: DeviceType;
    id: string;
  };
  device_override?: boolean;
}

// Settings
export interface Settings {
  teddycloud_url: string;
  server_url: string;
  audio_cache_max_mb: number;
}

// Cache info
export interface CacheInfo {
  size_mb: number;  // API returns size_mb
  max_mb: number;
  files: number;
  folders: number;
}

// Preferences (persisted)
export interface Preferences {
  starredDevices: string[];      // "type|id" format
  recentlyPlayed: string[];      // UIDs
  hiddenItems: string[];         // UIDs
}

// Feature flags
export interface FeatureFlags {
  espuino_enabled: boolean;
}

// Version info
export interface VersionInfo {
  version: string;
  git_commit: string;
  build_time: string;
}

// Streams response
export interface StreamsResponse {
  streams: ActiveStream[];
  uploads: Upload[];
  pending_uploads: PendingUpload[];
}

// Alert types
export type AlertType = 'success' | 'error' | 'warning';

export interface Alert {
  id: string;
  message: string;
  type: AlertType;
  timeout?: number;
}
