const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const root = path.resolve(__dirname, '..');
const dockerfile = fs.readFileSync(path.join(root, 'Dockerfile'), 'utf8');
const dockerignore = fs.readFileSync(path.join(root, '.dockerignore'), 'utf8');
const dockerCompose = fs.readFileSync(path.join(root, 'docker-compose.yml'), 'utf8');

test('the runtime image receives a production React build from an isolated frontend stage', () => {
  const buildStage = dockerfile.indexOf('AS frontend-build');
  const runtimeStage = dockerfile.indexOf('AS runtime');

  assert.ok(buildStage >= 0, 'Dockerfile must declare a frontend-build stage');
  assert.ok(runtimeStage > buildStage, 'the runtime stage must follow the frontend build stage');
  assert.match(dockerfile, /COPY frontend\/package\.json frontend\/package-lock\.json \.[/\s]/u);
  assert.match(dockerfile, /RUN npm ci\s/u);
  assert.match(dockerfile, /RUN npm run build\s/u);
  assert.match(
    dockerfile,
    /COPY --from=frontend-build \/build\/frontend\/dist \/app\/frontend\/dist/u,
  );
  assert.match(dockerfile, /\bUI_ENTRY=react\b/u);
});

test('the Docker context cannot substitute host frontend artifacts for the image build', () => {
  assert.match(dockerignore, /^frontend\/node_modules\/?$/mu);
  assert.match(dockerignore, /^frontend\/dist\/?$/mu);
});

test('the production Docker context excludes tests, fixtures, and fake provider credentials', () => {
  const ignored = new Set(
    dockerignore.split(/\r?\n/u).map((line) => line.trim()).filter(Boolean),
  );
  for (const excluded of [
    'backend/tests',
    'backend/app/providers/ocr/fake.py',
    'test',
    'frontend/e2e',
    'frontend/src/**/*.test.*',
    'frontend/src/test',
    'frontend/build/**/*.test.*',
    'frontend/build/legacyGatewayGuard.ts',
    'test-results',
  ]) {
    assert.ok(
      ignored.has(excluded),
      `${excluded} must be absent from the production build context`,
    );
  }
});

test('Docker Compose exposes the startup-only UI entry rollback switch', () => {
  assert.match(dockerCompose, /^\s*- UI_ENTRY=\$\{UI_ENTRY:-react\}\s*$/mu);
});

test('containers default backend rollout to legacy and OCR off with pass-through overrides', () => {
  for (const [variable, fallback] of Object.entries({
    API_BACKEND_MODE: 'legacy',
    DOCUMENT_PIPELINE_MODE: 'legacy',
    GENERATION_PIPELINE_MODE: 'legacy',
    ARTIFACT_READ_MODE: 'legacy',
    ARTIFACT_WRITE_MODE: 'legacy',
    OCR_ENABLED: '0',
  })) {
    assert.match(
      dockerfile,
      new RegExp(`\\b${variable}=${fallback}\\b`, 'u'),
      `${variable} Dockerfile default`,
    );
    assert.match(
      dockerCompose,
      new RegExp(`^\\s*- ${variable}=\\$\\{${variable}:-${fallback}\\}\\s*$`, 'mu'),
      `${variable} Compose pass-through`,
    );
  }
  assert.doesNotMatch(dockerfile, /(?:API_KEY|AUTH_TOKEN|PASSWORD)=\S+/u);
  assert.doesNotMatch(dockerCompose, /^\s*- (?:API_KEY|AUTH_TOKEN|PASSWORD)=\S+/mu);
});
