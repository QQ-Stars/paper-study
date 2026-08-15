"use strict";

const DEFAULTS = Object.freeze({
  apiBackendMode: 'legacy',
  documentPipelineMode: 'legacy',
  generationPipelineMode: 'legacy',
  artifactReadMode: 'legacy',
  artifactWriteMode: 'legacy',
  ocrEnabled: false,
});

const P0_1_ROLLOUT_VOCABULARY = 'p0.1';
const P5_ROLLOUT_VOCABULARY = 'p5';

const SPECIFICATION = Object.freeze([
  ['API_BACKEND_MODE', 'apiBackendMode', ['legacy', 'shadow', 'python'], 'legacy'],
  ['DOCUMENT_PIPELINE_MODE', 'documentPipelineMode', ['legacy', 'p1'], 'legacy'],
  ['GENERATION_PIPELINE_MODE', 'generationPipelineMode', ['legacy', 'p1'], 'legacy'],
  ['ARTIFACT_READ_MODE', 'artifactReadMode', ['legacy', 'prefer_new'], 'legacy'],
  ['ARTIFACT_WRITE_MODE', 'artifactWriteMode', ['legacy', 'dual'], 'legacy'],
  ['OCR_ENABLED', 'ocrEnabled', ['0', '1'], '0'],
]);

const P5_EXTENSION = Object.freeze([
  ['OBSIDIAN_ENABLED', 'obsidianEnabled', ['0', '1'], '0'],
]);

const BOOLEAN_VARIABLES = new Set(['OCR_ENABLED', 'OBSIDIAN_ENABLED']);

class BackendRolloutConfigurationError extends Error {
  constructor(code, variable, value, message) {
    super(`${code}: ${message}`);
    this.name = 'BackendRolloutConfigurationError';
    this.code = code;
    this.variable = variable;
    this.value = value;
  }
}

function specificationFor(vocabulary) {
  if (vocabulary === P0_1_ROLLOUT_VOCABULARY) return SPECIFICATION;
  if (vocabulary === P5_ROLLOUT_VOCABULARY) {
    return [...SPECIFICATION, ...P5_EXTENSION];
  }
  throw new BackendRolloutConfigurationError(
    'INVALID_ROLLOUT_VOCABULARY',
    'ROLLOUT_VOCABULARY',
    vocabulary,
    `ROLLOUT_VOCABULARY must be exactly one of ${P0_1_ROLLOUT_VOCABULARY}, ${P5_ROLLOUT_VOCABULARY}`,
  );
}

function parseBackendRollout(
  environment = process.env,
  vocabulary = P0_1_ROLLOUT_VOCABULARY,
) {
  const settings = {};
  for (const [variable, field, accepted, fallback] of specificationFor(vocabulary)) {
    const raw = environment[variable];
    const value = raw === undefined ? fallback : raw;
    if (!accepted.includes(value)) {
      throw new BackendRolloutConfigurationError(
        'INVALID_ROLLOUT_VALUE',
        variable,
        value,
        `${variable} must be exactly one of ${accepted.join(', ')}, received ${JSON.stringify(value)}`,
      );
    }
    settings[field] = BOOLEAN_VARIABLES.has(variable) ? value === '1' : value;
  }
  return Object.freeze(settings);
}

function loadBackendRollout(
  environment = process.env,
  vocabulary = P0_1_ROLLOUT_VOCABULARY,
) {
  const settings = parseBackendRollout(environment, vocabulary);
  const effective = rolloutToEnvironment(settings, vocabulary);
  for (const [variable, _field, _accepted, fallback] of SPECIFICATION) {
    if (effective[variable] !== fallback) {
      throw new BackendRolloutConfigurationError(
        'ROLLOUT_ADAPTER_UNAVAILABLE',
        variable,
        effective[variable],
        `${variable}=${effective[variable]} has no registered P0 adapter`,
      );
    }
  }
  return settings;
}

function isShadowReadOnly(settings) {
  return settings.apiBackendMode === 'shadow';
}

function assertShadowRequestAllowed(settings, method) {
  const normalizedMethod = String(method).toUpperCase();
  if (isShadowReadOnly(settings) && !['GET', 'HEAD', 'OPTIONS'].includes(normalizedMethod)) {
    throw new BackendRolloutConfigurationError(
      'SHADOW_MUTATION_FORBIDDEN',
      'API_BACKEND_MODE',
      settings.apiBackendMode,
      `shadow mode cannot execute ${normalizedMethod} requests`,
    );
  }
}

function rolloutToEnvironment(settings, vocabulary = P0_1_ROLLOUT_VOCABULARY) {
  const environment = {};
  for (const [variable, field] of specificationFor(vocabulary)) {
    environment[variable] = BOOLEAN_VARIABLES.has(variable)
      ? (settings[field] ? '1' : '0')
      : settings[field];
  }
  return Object.freeze(environment);
}

module.exports = {
  BackendRolloutConfigurationError,
  DEFAULTS,
  P0_1_ROLLOUT_VOCABULARY,
  P5_ROLLOUT_VOCABULARY,
  assertShadowRequestAllowed,
  isShadowReadOnly,
  loadBackendRollout,
  parseBackendRollout,
  rolloutToEnvironment,
};
