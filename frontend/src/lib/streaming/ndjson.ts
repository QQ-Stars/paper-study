import { ProtocolError } from '../api/errors';
import type { StreamContract, StreamFrame } from './contracts';

export type StreamObserver<E, T> = (value: E | T) => void;

function parseLine<E, T>(
  text: string,
  line: number,
  contract: StreamContract<E, T>,
): StreamFrame<E, T> | null {
  if (!text.trim()) return null;
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch (error) {
    throw new ProtocolError('invalid-json', 'Invalid JSON', line, error);
  }
  try {
    return contract.decode(value, `$line[${line}]`);
  } catch (error) {
    if (error instanceof ProtocolError) throw error;
    throw new ProtocolError('invalid-event', 'Invalid NDJSON event', line, error);
  }
}

export async function readNdjsonResponse<E, T>(
  response: Response,
  contract: StreamContract<E, T>,
  observer?: StreamObserver<E, T>,
): Promise<T> {
  let terminal: T | undefined;

  const consume = (frame: StreamFrame<E, T> | null, line: number) => {
    if (!frame) return;
    if (terminal !== undefined) {
      const code = frame.kind === 'terminal' ? 'duplicate-terminal' : 'post-terminal-event';
      const message = frame.kind === 'terminal' ? 'Duplicate terminal event' : 'Event after terminal event';
      throw new ProtocolError(code, message, line);
    }
    if (frame.kind === 'terminal') terminal = frame.value;
    else observer?.(frame.value);
  };

  if (!response.body) {
    let value: unknown;
    try {
      value = await response.json();
    } catch (error) {
      throw new ProtocolError('invalid-json', 'Response is not one JSON event', 1, error);
    }
    let frame: StreamFrame<E, T>;
    try {
      frame = contract.decode(value, '$line[1]');
    } catch (error) {
      throw new ProtocolError('invalid-event', 'Invalid NDJSON event', 1, error);
    }
    consume(frame, 1);
  } else {
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let line = 1;
    try {
      for (;;) {
        const next = await reader.read();
        if (next.done) break;
        buffer += decoder.decode(next.value, { stream: true });
        let newline = buffer.indexOf('\n');
        while (newline >= 0) {
          const physicalLine = buffer.slice(0, newline).replace(/\r$/, '');
          consume(parseLine(physicalLine, line, contract), line);
          line += 1;
          buffer = buffer.slice(newline + 1);
          newline = buffer.indexOf('\n');
        }
      }
      buffer += decoder.decode();
      if (buffer.length > 0) consume(parseLine(buffer.replace(/\r$/, ''), line, contract), line);
    } finally {
      reader.releaseLock();
    }
  }

  if (terminal === undefined) {
    throw new ProtocolError('missing-terminal', `Missing ${contract.terminalType} terminal event`);
  }
  observer?.(terminal);
  return terminal;
}
