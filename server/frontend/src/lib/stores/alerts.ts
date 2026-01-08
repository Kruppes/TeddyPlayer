import { writable } from 'svelte/store';
import type { Alert, AlertType } from '../types';

// Active alerts
export const alerts = writable<Alert[]>([]);

// Counter for unique IDs
let alertIdCounter = 0;

// Show an alert
export function showAlert(
  message: string,
  type: AlertType = 'error',
  timeout: number = 5000
): string {
  const id = `alert-${++alertIdCounter}`;

  alerts.update(current => [
    ...current,
    { id, message, type, timeout }
  ]);

  // Auto-remove after timeout
  if (timeout > 0) {
    setTimeout(() => {
      dismissAlert(id);
    }, timeout);
  }

  return id;
}

// Dismiss an alert
export function dismissAlert(id: string): void {
  alerts.update(current =>
    current.filter(a => a.id !== id)
  );
}

// Dismiss all alerts
export function dismissAllAlerts(): void {
  alerts.set([]);
}

// Helper shortcuts
export const showSuccess = (message: string, timeout?: number) =>
  showAlert(message, 'success', timeout);

export const showError = (message: string, timeout?: number) =>
  showAlert(message, 'error', timeout);

export const showWarning = (message: string, timeout?: number) =>
  showAlert(message, 'warning', timeout);
