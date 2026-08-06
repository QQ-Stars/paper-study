import { api, jsonRequest, type ApiClient } from './client';
import {
  decodeCommandId,
  decodeOkCommand,
  decodeScheduleList,
} from './decoders';
import { DecodeError } from './errors';
import { signalOptions } from './gatewayTransport';
import type { CreateJobInput } from './jobsGateway';

export interface CreateScheduleInput extends CreateJobInput {
  everyDays?: number;
}

export function createSchedulesGateway(client: ApiClient = api) {
  return {
    listSchedules(signal?: AbortSignal) {
      return client.json('/api/schedules', decodeScheduleList, signalOptions(signal));
    },

    async createSchedule(input: CreateScheduleInput, signal?: AbortSignal): Promise<number> {
      const value = await client.json('/api/schedules', decodeCommandId, jsonRequest(
        input, { method: 'POST', ...signalOptions(signal) },
      ));
      if (typeof value !== 'number') throw new DecodeError('$.id', 'schedule id number', value);
      return value;
    },

    async toggleSchedule(id: number, enabled: boolean, signal?: AbortSignal): Promise<void> {
      const scheduleId = Number(id);
      await client.json('/api/schedules/toggle', decodeOkCommand, jsonRequest(
        { id: scheduleId, enabled }, { method: 'POST', ...signalOptions(signal) },
      ));
    },

    async deleteSchedule(id: number, signal?: AbortSignal): Promise<void> {
      const scheduleId = Number(id);
      await client.json('/api/schedules/delete', decodeOkCommand, jsonRequest(
        { id: scheduleId }, { method: 'POST', ...signalOptions(signal) },
      ));
    },
  };
}

export const schedulesGateway = createSchedulesGateway();
export type SchedulesGateway = ReturnType<typeof createSchedulesGateway>;
