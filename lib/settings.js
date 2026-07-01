const fs = require('fs');
const path = require('path');

const DEFAULT_LLM_PRESETS = {
  deepseek: ['https://api.deepseek.com', 'deepseek-v4-flash'],
  qwen: ['https://dashscope.aliyuncs.com/compatible-mode/v1', 'qwen-plus'],
  openai: ['https://api.openai.com/v1', 'gpt-4o-mini'],
  anthropic: ['https://api.anthropic.com', 'claude-3-5-sonnet-latest'],
};

const resolveDir = (root, dir) => (path.isAbsolute(dir) ? dir : path.join(root, dir));
const maskKey = (key) => key ? '****' + String(key).slice(-4) : '';
const trimValue = (value) => String(value || '').trim();

function readEnvFile(root, fsImpl = fs) {
  const env = {};
  try {
    const text = fsImpl.readFileSync(path.join(root, '.env'), 'utf8');
    text.split(/\r?\n/).forEach((line) => {
      const match = /^([A-Z0-9_]+)=(.*)$/.exec(line.trim());
      if (match) env[match[1]] = match[2];
    });
  } catch (e) {}
  return env;
}

function createSettingsStore({
  root = process.cwd(),
  settingsPath = path.join(root, 'data', 'settings.json'),
  fsImpl = fs,
  presets = DEFAULT_LLM_PRESETS,
} = {}) {
  const read = () => {
    try {
      return JSON.parse(fsImpl.readFileSync(settingsPath, 'utf8'));
    } catch (e) {
      return {};
    }
  };

  const write = (settings) => {
    fsImpl.mkdirSync(path.dirname(settingsPath), { recursive: true });
    fsImpl.writeFileSync(settingsPath, JSON.stringify(settings, null, 2));
  };

  const readEnv = () => readEnvFile(root, fsImpl);

  const llmConfig = () => {
    const settings = read();
    const env = readEnv();
    const provider = String(settings.provider || env.LLM_PROVIDER || 'deepseek').toLowerCase();
    const [presetBaseUrl, presetModel] = presets[provider] || presets.deepseek;
    return {
      apiKey: settings.apiKey || env.LLM_API_KEY || '',
      baseUrl: settings.baseUrl || env.LLM_BASE_URL || presetBaseUrl,
      model: settings.model || env.LLM_MODEL || presetModel,
    };
  };

  return { path: settingsPath, read, write, readEnv, llmConfig };
}

function buildSettingsView({
  root = process.cwd(),
  settings = {},
  env = {},
  defaultDirs = {},
} = {}) {
  const defaultPdfDir = defaultDirs.pdfDir || path.join(root, 'data', 'pdfs');
  const defaultExplainerDir = defaultDirs.explainerDir || path.join(root, 'data', 'explainers');
  const defaultTranslationDir = defaultDirs.translationDir || path.join(root, 'data', 'translations');
  const relativePdfDir = path.relative(root, defaultPdfDir);
  const relativeExplainerDir = path.relative(root, defaultExplainerDir);
  const relativeTranslationDir = path.relative(root, defaultTranslationDir);
  const configuredDir = (key, fallback) => resolveDir(root, settings[key] || fallback);

  return {
    provider: settings.provider || env.LLM_PROVIDER || 'deepseek',
    baseUrl: settings.baseUrl || env.LLM_BASE_URL || '',
    model: settings.model || env.LLM_MODEL || '',
    apiKeyTail: maskKey(settings.apiKey || env.LLM_API_KEY),
    hasApiKey: !!(settings.apiKey || env.LLM_API_KEY),
    s2KeyTail: maskKey(settings.s2ApiKey),
    hasS2Key: !!settings.s2ApiKey,
    pdfDir: settings.pdfDir || '',
    explainerDir: settings.explainerDir || '',
    translationDir: settings.translationDir || '',
    defaultPdfDir: relativePdfDir,
    defaultExplainerDir: relativeExplainerDir,
    defaultTranslationDir: relativeTranslationDir,
    resolvedPdfDir: configuredDir('pdfDir', relativePdfDir),
    resolvedExplainerDir: configuredDir('explainerDir', relativeExplainerDir),
    resolvedTranslationDir: configuredDir('translationDir', relativeTranslationDir),
    researchTheme: settings.researchTheme || '',
    embedProvider: settings.embedProvider || 'local',
    embedApiBase: settings.embedApiBase || '',
    embedApiModel: settings.embedApiModel || '',
    embedKeyTail: maskKey(settings.embedApiKey),
    hasEmbedKey: !!settings.embedApiKey,
  };
}

function applySettingsUpdate(current = {}, patch = {}) {
  const next = { ...current };
  if (patch.provider) next.provider = patch.provider;
  if (patch.baseUrl !== undefined) next.baseUrl = patch.baseUrl;
  if (patch.model !== undefined) next.model = patch.model;
  if (patch.apiKey) next.apiKey = patch.apiKey;
  if (patch.s2ApiKey) next.s2ApiKey = patch.s2ApiKey;
  if (patch.pdfDir !== undefined) next.pdfDir = trimValue(patch.pdfDir);
  if (patch.explainerDir !== undefined) next.explainerDir = trimValue(patch.explainerDir);
  if (patch.translationDir !== undefined) next.translationDir = trimValue(patch.translationDir);
  if (patch.researchTheme !== undefined) next.researchTheme = trimValue(patch.researchTheme);
  if (patch.embedProvider) next.embedProvider = patch.embedProvider;
  if (patch.embedApiBase !== undefined) next.embedApiBase = trimValue(patch.embedApiBase);
  if (patch.embedApiModel !== undefined) next.embedApiModel = trimValue(patch.embedApiModel);
  if (patch.embedApiKey) next.embedApiKey = patch.embedApiKey;
  return next;
}

function ensureSettingsDirs(settings = {}, {
  root = process.cwd(),
  fsImpl = fs,
  keys = ['pdfDir', 'explainerDir', 'translationDir'],
} = {}) {
  for (const key of keys) {
    if (settings[key]) fsImpl.mkdirSync(resolveDir(root, settings[key]), { recursive: true });
  }
}

module.exports = {
  DEFAULT_LLM_PRESETS,
  applySettingsUpdate,
  buildSettingsView,
  createSettingsStore,
  ensureSettingsDirs,
  maskKey,
  readEnvFile,
  resolveDir,
};
