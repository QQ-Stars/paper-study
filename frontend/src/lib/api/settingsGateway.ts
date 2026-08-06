import { api, jsonRequest, type ApiClient } from './client';
import {
  decodeLlmTestCommand,
  decodeOkCommand,
  decodeSettingsView,
} from './decoders';
import { signalOptions } from './gatewayTransport';
import type { SettingsUpdate } from './types';

export function createSettingsGateway(client: ApiClient = api) {
  return {
    getSettings(signal?: AbortSignal) {
      return client.json('/api/settings', decodeSettingsView, signalOptions(signal));
    },

    async saveSettings(update: SettingsUpdate, signal?: AbortSignal): Promise<void> {
      await client.json('/api/settings', decodeOkCommand, jsonRequest(
        update, { method: 'POST', ...signalOptions(signal) },
      ));
    },

    testLlm(signal?: AbortSignal) {
      return client.json('/api/test-llm', decodeLlmTestCommand, jsonRequest(
        {}, { method: 'POST', ...signalOptions(signal) },
      ));
    },
  };
}

export const settingsGateway = createSettingsGateway();
export type SettingsGateway = ReturnType<typeof createSettingsGateway>;
