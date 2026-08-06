/* global AbortSignal, fetch */

import process from 'node:process';
import { setTimeout } from 'node:timers';
import { pathToFileURL } from 'node:url';

import { preview } from 'vite';

const host = '127.0.0.1';
const port = 5174;
const shutdownPath = '/__paper_study_e2e_shutdown__';
const shutdownHeader = 'x-paper-study-e2e-shutdown';

function shutdownPlugin() {
  const configurePreviewServer = (server) => {
    server.middlewares.use(shutdownPath, (request, response, next) => {
      if (request.method !== 'POST' || request.headers[shutdownHeader] !== 'true') {
        next();
        return;
      }

      response.statusCode = 204;
      response.end();
      setTimeout(() => {
        void server.close().finally(() => process.exit(0));
      }, 10);
    });
  };

  return {
    name: 'paper-study-e2e-shutdown',
    configurePreviewServer,
  };
}

async function startVite() {
  const server = await preview({
    plugins: [shutdownPlugin()],
    preview: { host, port, strictPort: true },
  });
  server.printUrls();
}

export default async function stopVite() {
  try {
    await fetch(`http://${host}:${port}${shutdownPath}`, {
      method: 'POST',
      headers: { [shutdownHeader]: 'true' },
      signal: AbortSignal.timeout(2_000),
    });
  } catch {
    // The server may already have stopped after a startup or test failure.
  }
}

const isEntryPoint = process.argv[1]
  ? import.meta.url === pathToFileURL(process.argv[1]).href
  : false;

if (isEntryPoint) {
  await startVite();
}
