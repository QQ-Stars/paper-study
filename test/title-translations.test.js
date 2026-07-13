const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const Database = require('better-sqlite3');
const test = require('node:test');

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
