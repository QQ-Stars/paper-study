function nonNegativeInteger(value) {
  const number = Number(value);
  return Number.isSafeInteger(number) && number >= 0 ? number : 0;
}

function parseImporterResult(output) {
  if (output && typeof output === 'object' && !Array.isArray(output)) return output;
  try {
    const parsed = JSON.parse(String(output || ''));
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : null;
  } catch (_) {
    return null;
  }
}

function buildImportPdfsTerminal(exitCode, output) {
  const result = parseImporterResult(output);
  return {
    type: 'result',
    ok: exitCode === 0 && result !== null && result.ok !== false,
    total: nonNegativeInteger(result?.total),
    added: nonNegativeInteger(result?.added),
    dup: nonNegativeInteger(result?.dup),
    failed: nonNegativeInteger(result?.failed),
    error: result
      ? (typeof result.error === 'string' ? result.error : '')
      : '导入结果格式无效',
  };
}

module.exports = { buildImportPdfsTerminal };
