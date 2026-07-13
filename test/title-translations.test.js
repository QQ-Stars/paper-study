const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const Database = require('better-sqlite3');
const test = require('node:test');

const {
  cleanTitleTranslation,
  createTitleTranslationService
} = require('../lib/title-translations');

function loadIsolatedDb(dbPath) {
  const previous = process.env.DB_PATH;
  process.env.DB_PATH = dbPath;
  delete require.cache[require.resolve('../db')];
  const dbapi = require('../db');
  return {
    dbapi,
    cleanup() {
      dbapi.db.close();
      delete require.cache[require.resolve('../db')];
      if (previous == null) delete process.env.DB_PATH;
      else process.env.DB_PATH = previous;
    }
  };
}

test('existing databases gain a nullable title_zh column without losing papers', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'study-app-title-'));
  const dbPath = path.join(root, 'legacy.db');
  const legacy = new Database(dbPath);
  const currentSchema = fs.readFileSync(path.join(__dirname, '..', 'db', 'schema.sql'), 'utf8');
  const legacySchema = currentSchema.replace(/^\s*title_zh\s+TEXT[^\n]*\r?\n/m, '');
  legacy.exec(legacySchema);
  legacy.prepare("INSERT INTO papers(id,source,title) VALUES('p1','manual','Original Title')").run();
  legacy.close();

  const loaded = loadIsolatedDb(dbPath);
  try {
    const columns = loaded.dbapi.db.prepare("SELECT name FROM pragma_table_info('papers')").all().map(row => row.name);
    assert.ok(columns.includes('title_zh'));
    assert.deepEqual(loaded.dbapi.db.prepare("SELECT title,title_zh FROM papers WHERE id='p1'").get(), {
      title: 'Original Title', title_zh: null
    });
  } finally {
    loaded.cleanup();
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('title translation repository lists blanks and never overwrites an existing value', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'study-app-title-'));
  const loaded = loadIsolatedDb(path.join(root, 'app.db'));
  try {
    const insert = loaded.dbapi.db.prepare(
      'INSERT INTO papers(id,source,title,title_zh) VALUES(?,?,?,?)'
    );
    insert.run('p1', 'manual', 'First Paper', null);
    insert.run('p2', 'manual', 'Second Paper', '第二篇论文');

    assert.equal(loaded.dbapi.countMissingTitleTranslations(), 1);
    assert.deepEqual(loaded.dbapi.listMissingTitleTranslations(), [{ id: 'p1', title: 'First Paper' }]);
    assert.equal(loaded.dbapi.setTitleTranslationIfMissing('p1', '第一篇论文'), 1);
    assert.equal(loaded.dbapi.setTitleTranslationIfMissing('p1', '覆盖文本'), 0);
    assert.equal(loaded.dbapi.getPaper('p1').title_zh, '第一篇论文');
    assert.equal(loaded.dbapi.getPaper('p2').title_zh, '第二篇论文');
  } finally {
    loaded.cleanup();
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('Chinese title cleaner accepts one academic title and rejects explanations', () => {
  assert.equal(cleanTitleTranslation('“验证链减少大型语言模型幻觉”'), '验证链减少大型语言模型幻觉');
  assert.equal(cleanTitleTranslation('中文标题：视觉变化缓解多模态大模型幻觉'), '视觉变化缓解多模态大模型幻觉');
  assert.equal(cleanTitleTranslation('Translation only'), '');
  assert.equal(cleanTitleTranslation('标题一\n补充说明'), '');
});

test('batch translation continues after one failure and persists only valid results', async () => {
  const saved = [];
  const events = [];
  const repository = {
    countMissingTitleTranslations: () => 3,
    listMissingTitleTranslations: () => [
      { id: 'p1', title: 'First Paper' },
      { id: 'p2', title: 'Second Paper' },
      { id: 'p3', title: 'Third Paper' }
    ],
    setTitleTranslationIfMissing(id, titleZh) { saved.push([id, titleZh]); return 1; }
  };
  const chat = async (_messages, _options) => {
    const title = _messages.at(-1).content;
    if (title === 'Second Paper') throw new Error('timeout');
    return title === 'First Paper' ? '第一篇论文' : '第三篇论文';
  };
  const service = createTitleTranslationService({ repository, chat });

  const summary = await service.runBatch({ onEvent: event => events.push(event) });

  assert.deepEqual(saved, [['p1', '第一篇论文'], ['p3', '第三篇论文']]);
  assert.equal(summary.total, 3);
  assert.equal(summary.done, 2);
  assert.equal(summary.failed.length, 1);
  assert.equal(summary.failed[0].id, 'p2');
  assert.ok(events.some(event => event.stage === 'item' && event.state === 'failed'));
});

test('batch translation stops before starting the next paper when cancelled', async () => {
  let cancelled = false;
  const saved = [];
  const service = createTitleTranslationService({
    repository: {
      countMissingTitleTranslations: () => 2,
      listMissingTitleTranslations: () => [
        { id: 'p1', title: 'First Paper' },
        { id: 'p2', title: 'Second Paper' }
      ],
      setTitleTranslationIfMissing(id, titleZh) { saved.push([id, titleZh]); cancelled = true; return 1; }
    },
    chat: async () => '中文题名'
  });

  const summary = await service.runBatch({ isCancelled: () => cancelled });

  assert.equal(summary.cancelled, true);
  assert.deepEqual(saved, [['p1', '中文题名']]);
});
