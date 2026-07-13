const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const { spawn } = require('node:child_process');
const Database = require('better-sqlite3');
const test = require('node:test');

const ROOT = path.join(__dirname, '..');

function listen(server) {
  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      server.off('error', reject);
      resolve(server.address().port);
    });
  });
}

async function freePort() {
  const server = http.createServer();
  const port = await listen(server);
  await new Promise(resolve => server.close(resolve));
  return port;
}

function waitForServer(child) {
  return new Promise((resolve, reject) => {
    let output = '';
    const timeout = setTimeout(() => reject(new Error(`server did not start:\n${output}`)), 5000);
    const onData = (chunk) => {
      output += chunk;
      if (output.includes('http://localhost:')) {
        clearTimeout(timeout);
        child.stdout.off('data', onData);
        resolve();
      }
    };
    child.stdout.on('data', onData);
    child.stderr.on('data', chunk => { output += chunk; });
    child.once('exit', (code, signal) => {
      clearTimeout(timeout);
      reject(new Error(`server exited before starting (${code || signal}):\n${output}`));
    });
  });
}

function stopServer(child) {
  if (child.exitCode !== null) return Promise.resolve();
  return new Promise(resolve => {
    child.once('exit', resolve);
    child.kill();
  });
}

function seedDatabase(dbPath) {
  const db = new Database(dbPath);
  db.exec(fs.readFileSync(path.join(ROOT, 'db', 'schema.sql'), 'utf8'));
  db.prepare('INSERT INTO papers(id, source, title, title_zh) VALUES(?, ?, ?, ?)')
    .run('p1', 'manual', 'First Paper', null);
  db.prepare('INSERT INTO papers(id, source, title, title_zh) VALUES(?, ?, ?, ?)')
    .run('p2', 'manual', 'Second Paper', null);
  db.prepare('INSERT INTO papers(id, source, title, title_zh) VALUES(?, ?, ?, ?)')
    .run('p3', 'manual', 'Already Translated', '\u5df2\u7ffb\u8bd1\u8bba\u6587');
  db.close();
}

test('title translation routes serialize pending work and persist a completed NDJSON batch', async (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'study-app-title-api-'));
  const dbPath = path.join(root, 'app.db');
  const settingsPath = path.join(root, 'settings.json');
  seedDatabase(dbPath);

  const fakeRequests = [];
  const fakeOpenAi = http.createServer(async (req, res) => {
    assert.equal(req.method, 'POST');
    assert.equal(req.url, '/v1/chat/completions');
    let body = '';
    for await (const chunk of req) body += chunk;
    const request = JSON.parse(body);
    fakeRequests.push(request);
    const title = request.messages.at(-1).content;
    const translations = {
      'First Paper': '\u7b2c\u4e00\u7bc7\u8bba\u6587',
      'Second Paper': '\u7b2c\u4e8c\u7bc7\u8bba\u6587'
    };
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ choices: [{ message: { content: translations[title] } }] }));
  });
  const fakePort = await listen(fakeOpenAi);
  fs.writeFileSync(settingsPath, JSON.stringify({
    apiKey: 'test-key',
    baseUrl: `http://127.0.0.1:${fakePort}/v1`,
    model: 'test-model'
  }));

  const appPort = await freePort();
  const child = spawn(process.execPath, ['server.js'], {
    cwd: ROOT,
    env: { ...process.env, PORT: String(appPort), DB_PATH: dbPath, SETTINGS_PATH: settingsPath },
    stdio: ['ignore', 'pipe', 'pipe']
  });
  t.after(async () => {
    await stopServer(child);
    await new Promise(resolve => fakeOpenAi.close(resolve));
    fs.rmSync(root, { recursive: true, force: true });
  });
  await waitForServer(child);

  const pendingResponse = await fetch(`http://127.0.0.1:${appPort}/api/title-translations`);
  assert.equal(pendingResponse.status, 200);
  assert.deepEqual(await pendingResponse.json(), { ok: true, pending: 2 });

  const batchResponse = await fetch(`http://127.0.0.1:${appPort}/api/title-translations`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ limit: 2 })
  });
  assert.equal(batchResponse.status, 200);
  assert.match(batchResponse.headers.get('content-type'), /application\/x-ndjson/);
  const events = (await batchResponse.text()).trim().split('\n').map(JSON.parse);
  assert.deepEqual(events.at(-1), {
    type: 'result',
    ok: true,
    summary: { total: 2, done: 2, failed: [], cancelled: false }
  });
  assert.equal(events.filter(event => event.stage === 'item' && event.state === 'done').length, 2);
  assert.equal(fakeRequests.length, 2);

  const db = new Database(dbPath, { readonly: true });
  assert.deepEqual(db.prepare('SELECT id, title_zh FROM papers ORDER BY id').all(), [
    { id: 'p1', title_zh: '\u7b2c\u4e00\u7bc7\u8bba\u6587' },
    { id: 'p2', title_zh: '\u7b2c\u4e8c\u7bc7\u8bba\u6587' },
    { id: 'p3', title_zh: '\u5df2\u7ffb\u8bd1\u8bba\u6587' }
  ]);
  db.close();

  const emptyPendingResponse = await fetch(`http://127.0.0.1:${appPort}/api/title-translations`);
  assert.deepEqual(await emptyPendingResponse.json(), { ok: true, pending: 0 });
});
