const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const root = path.resolve(__dirname, '..');
const dockerfile = fs.readFileSync(path.join(root, 'Dockerfile'), 'utf8');
const dockerignore = fs.readFileSync(path.join(root, '.dockerignore'), 'utf8');

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
