import { writable, derived } from 'svelte/store';
import type { DevicesCache, Device, DeviceType } from '../types';
import * as api from '../api/client';

// Raw devices cache from server
export const devicesCache = writable<DevicesCache>({
  sonos: [],
  airplay: [],
  chromecast: [],
  espuino: [],
});

// Selected device for playback
export const selectedDevice = writable<Device>({
  type: 'browser',
  id: 'web',
  name: 'This Browser',
});

// Starred devices (persisted)
export const starredDevices = writable<string[]>(['browser|web']);

// All devices as flat list (normalized to have `id` field)
export const allDevices = derived(devicesCache, ($cache) => {
  const devices: Device[] = [
    { type: 'browser', id: 'web', name: 'This Browser' },
  ];

  // Sonos uses `uid` as identifier
  for (const device of $cache.sonos) {
    devices.push({ type: 'sonos', id: device.uid, name: device.name, ip: device.ip });
  }
  // AirPlay, Chromecast, ESPuino use `id`
  for (const device of $cache.airplay) {
    devices.push({ type: 'airplay', id: device.id, name: device.name, ip: device.ip });
  }
  for (const device of $cache.chromecast) {
    devices.push({ type: 'chromecast', id: device.id, name: device.name, ip: device.ip });
  }
  for (const device of $cache.espuino) {
    devices.push({ type: 'espuino', id: device.id, name: device.name, ip: device.ip });
  }

  return devices;
});

// Get device key for starring
export function getDeviceKey(type: DeviceType, id: string): string {
  return `${type}|${id}`;
}

// Check if device is starred (with safe null check)
export function isDeviceStarred(starredList: string[] | undefined | null, type: DeviceType, id: string): boolean {
  if (!starredList || !Array.isArray(starredList)) return false;
  return starredList.includes(getDeviceKey(type, id));
}

// Starred devices with full info
export const starredDevicesList = derived(
  [allDevices, starredDevices],
  ([$devices, $starred]) => {
    return $devices.filter(d => $starred.includes(getDeviceKey(d.type, d.id)));
  }
);

// Load devices from server
export async function loadDevices(): Promise<void> {
  try {
    const data = await api.getDevices();
    devicesCache.set(data);
  } catch (e) {
    console.error('Failed to load devices:', e);
  }
}

// Discover new devices
export async function discoverDevices(): Promise<number> {
  const result = await api.discoverDevices();
  await loadDevices();
  return result.discovered;
}

// Toggle star on a device
export async function toggleDeviceStar(type: DeviceType, id: string): Promise<void> {
  const key = getDeviceKey(type, id);

  starredDevices.update(starred => {
    const index = starred.indexOf(key);
    if (index >= 0) {
      // Don't allow unstarring the last device
      if (starred.length <= 1) return starred;
      return [...starred.slice(0, index), ...starred.slice(index + 1)];
    } else {
      return [...starred, key];
    }
  });

  // Persist to server
  let currentStarred: string[] = [];
  starredDevices.subscribe(s => currentStarred = s)();
  await api.savePreference('starredDevices', currentStarred);
}

// Remove a device
export async function removeDevice(type: DeviceType, id: string): Promise<void> {
  await api.removeDevice(type, id);
  await loadDevices();

  // Also remove from starred if present
  const key = getDeviceKey(type, id);
  starredDevices.update(starred => starred.filter(k => k !== key));
}

// Set selected device to first starred device (called after loading preferences and devices)
export function selectFirstStarredDevice(): void {
  let starred: string[] = [];
  let devices: Device[] = [];

  starredDevices.subscribe(s => starred = s)();
  allDevices.subscribe(d => devices = d)();

  if (starred.length > 0) {
    const [type, id] = starred[0].split('|');
    const device = devices.find(d => d.type === type && d.id === id);
    if (device) {
      selectedDevice.set(device);
      return;
    }
  }

  // Fallback to browser if no starred device found
  const browser = devices.find(d => d.type === 'browser');
  if (browser) {
    selectedDevice.set(browser);
  }
}
