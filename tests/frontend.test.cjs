const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');

function harness() {
  const cache = new Map();
  const intervals = new Map();
  const timeouts = [];
  const calls = [];
  let nextId = 0;
  const api = {
    downloadPoll: async () => ({ status: 'success', data: {} }),
    downloadPause: async () => ({ status: 'error', message: 'RPC unavailable' }),
    downloadCancel: async () => ({ status: 'error', message: 'RPC unavailable' }),
    getSettings: async () => ({ data: { auto_delete_after_install: true } }),
    deleteArchive: async payload => calls.push(['delete', payload]),
    importApplied: async payload => { calls.push(['import', payload]); return { status: 'success' }; },
  };
  const window = { dispatchEvent: event => calls.push(['event', event.type]) };
  function load(file) {
    if (cache.has(file)) return cache.get(file);
    const exports = {};
    cache.set(file, exports);
    const code = ts.transpileModule(fs.readFileSync(path.join(__dirname, '../src', file + '.ts'), 'utf8'), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
    }).outputText;
    vm.runInNewContext(code, {
      exports, window, console, CustomEvent: class { constructor(type) { this.type = type; } },
      require: name => name === '@decky/api' ? { toaster: { toast: payload => calls.push(['toast', payload]) } }
        : name === './api' ? api : name === './romImport' ? {} : load(name.replace('./', '')),
      setInterval: fn => { const id = ++nextId; intervals.set(id, fn); return id; },
      clearInterval: id => intervals.delete(id),
      setTimeout: fn => { timeouts.push(fn); return timeouts.length; },
      clearTimeout: () => {},
    }, { filename: file });
    return exports;
  }
  return { load, api, intervals, timeouts, calls, window };
}

test('download completion waits for extraction and import; failures remain failures', () => {
  const { load } = harness();
  const { downloadFailed, downloadSucceeded } = load('downloadState');
  for (const extract_status of [undefined, 'queued', 'running', 'complete']) {
    assert.equal(downloadSucceeded({ status: 'complete', game_id: 'game', extract_status }), false);
  }
  assert.equal(downloadSucceeded({ status: 'complete', game_id: 'game', extract_status: 'complete', import_status: 'complete' }), true);
  for (const failed of [{ status: 'failed' }, { status: 'complete', extract_status: 'failed' }, { status: 'complete', import_status: 'failed' }]) {
    assert.equal(downloadFailed(failed), true);
    assert.equal(downloadSucceeded(failed), false);
  }
});

test('queue keeps more than five tasks and preserves artwork on partial updates', () => {
  const store = harness().load('downloadStore');
  for (let i = 0; i < 8; i++) store.upsertDownload({ task_id: String(i), status: 'downloading' }, 'cover');
  store.upsertDownload({ task_id: '0', status: 'paused' });
  assert.equal(store.getDownloadQueue().length, 8);
  assert.equal(store.getDownloadQueue().find(t => t.task_id === '0').cover_url, 'cover');
});

test('failed pause or cancellation preserves the queue and polling', async () => {
  const h = harness();
  const poller = h.load('downloadPoller').getDownloadPoller();
  const store = h.load('downloadStore');
  store.upsertDownload({ task_id: 't', status: 'downloading' });
  poller.addTask('t');
  await poller.pauseTask('t');
  await poller.cancelTask('t');
  assert.equal(h.intervals.size, 1);
  assert.equal(store.getDownloadQueue().length, 1);
  assert.equal(h.calls.filter(c => c[0] === 'toast').length, 2);
});

test('failed installation stops polling but preserves the archive and error', async () => {
  const h = harness();
  const poller = h.load('downloadPoller').getDownloadPoller();
  h.api.downloadPoll = async () => ({ status: 'success', data: { task_id: 't', status: 'complete', game_id: 'game', extract_status: 'failed', file_path: '/tmp/game.zip' } });
  poller.addTask('t');
  await [...h.intervals.values()][0]();
  assert.equal(h.intervals.size, 0);
  assert.equal(h.load('downloadStore').getDownloadQueue().length, 1);
  assert.equal(h.calls.filter(c => c[0] === 'delete').length, 0);
});

test('concurrent polling ticks share one request and stopped results are ignored', async () => {
  const h = harness();
  const poller = h.load('downloadPoller').getDownloadPoller();
  let resolve, count = 0;
  h.api.downloadPoll = () => { count++; return new Promise(r => resolve = r); };
  poller.addTask('t');
  const tick = [...h.intervals.values()][0];
  const pending = tick();
  await tick();
  assert.equal(count, 1);
  poller.destroy();
  resolve({ status: 'success', data: { task_id: 't', status: 'downloading' } });
  await pending;
  assert.equal(h.load('downloadStore').getDownloadQueue().length, 0);
});

test('missing Steam API reports failed import instead of acknowledging success', async () => {
  const h = harness();
  const poller = h.load('downloadPoller').getDownloadPoller();
  await poller._createShortcutAndFinish({ task_id: 't', steam_exe: '/game.exe' });
  assert.equal(h.calls.filter(c => c[0] === 'import').length, 1);
  assert.ok(h.calls.find(c => c[0] === 'import')[1].error);
});

test('library combines search, platform, favorites and recent filters without mutating records', () => {
  const { selectLibraryGames, formatPlaytime } = harness().load('libraryModel');
  const games = [
    { game_id: 'one', title: 'Alpha', platform: 'ps1', favorite: true, running: false, last_played: 20, playtime_seconds: 90 },
    { game_id: 'two', title: 'Beta', platform: 'pc', favorite: false, running: true, last_played: 10, playtime_seconds: 200 },
    { game_id: 'three', title: 'Gamma', platform: 'ps1', favorite: false, running: false, last_played: 0, playtime_seconds: 0 },
    { game_id: 'ps1_emulator', title: 'Emulator', last_played: 99 },
  ];
  const ids = (...args) => Array.from(selectLibraryGames(games, ...args), g => g.game_id);
  assert.deepEqual(ids(' ALP ', 'favorites', 'ps1', 'recent'), ['one']);
  assert.deepEqual(ids('', 'recent', 'all', 'recent'), ['two', 'one']);
  assert.deepEqual(ids('', 'running', 'ps1', 'title'), []);
  assert.deepEqual(ids('', 'all', 'all', 'playtime'), ['two', 'one', 'three']);
  assert.equal(games[0].game_id, 'one');
  assert.equal(formatPlaytime(0), '尚未记录');
  assert.equal(formatPlaytime(59), '不足 1 分钟');
  assert.equal(formatPlaytime(3661), '1 小时 1 分钟');
});

const shortcut = { game_id: 'local_ps1_game', title: 'Game', executable: '/emu/app', start_dir: '/emu', launch_options: '/game.rom', appid: 0, token: 'pending' };

test('ROM imports reuse valid Steam shortcuts and commit before returning', async () => {
  const h = harness(); let added = 0; const commits = [];
  h.window.SteamClient = { Apps: { AddShortcut: async () => { added++; return 88; } } };
  h.window.appStore = { GetAppOverviewByAppID: () => ({ m_gameid: 'existing' }) };
  h.api.libraryCommitImport = async payload => { commits.push(payload); return { status: 'success', data: { ok: true } }; };
  assert.equal(await h.load('steamLaunch').ensureShortcut({ ...shortcut, appid: -1 }), 0xffffffff);
  assert.equal(added, 0); assert.equal(commits[0].appid, 0xffffffff);
});

test('concurrent import clicks create and commit only one shortcut', async () => {
  const h = harness(); let adds = 0, commits = 0, resolve;
  h.window.SteamClient = { Apps: { AddShortcut: () => { adds++; return new Promise(r => resolve = r); } } };
  h.api.libraryCommitImport = async () => { commits++; return { status: 'success', data: { ok: true } }; };
  const api = h.load('steamLaunch');
  const first = api.ensureShortcut(shortcut); const second = api.ensureShortcut(shortcut);
  resolve(-2);
  assert.deepEqual(await Promise.all([first, second]), [0xfffffffe, 0xfffffffe]);
  assert.equal(adds, 1); assert.equal(commits, 1);
});

test('failed backend commit rolls back Steam shortcut and allows retry', async () => {
  const h = harness(); let removed = 0, adds = 0;
  h.window.SteamClient = { Apps: { AddShortcut: async () => { adds++; return 123; }, RemoveShortcut: async id => removed = id } };
  h.api.libraryCommitImport = async () => ({ status: 'error', message: 'disk full' });
  const api = h.load('steamLaunch');
  await assert.rejects(api.ensureShortcut(shortcut), /disk full/);
  assert.equal(removed, 123);
  h.api.libraryCommitImport = async () => ({ status: 'success', data: { ok: true } });
  assert.equal(await api.ensureShortcut(shortcut), 123); assert.equal(adds, 2);
});

test('missing shortcut is recreated and saved through library link', async () => {
  const h = harness(); let linked;
  h.window.appStore = { GetAppOverviewByAppID: () => undefined };
  h.window.SteamClient = { Apps: { AddShortcut: async () => -3 } };
  h.api.libraryLinkShortcut = async payload => { linked = payload; return { status: 'success', data: { ok: true } }; };
  assert.equal(await h.load('steamLaunch').ensureShortcut({ ...shortcut, token: undefined, appid: 44 }), 0xfffffffd);
  assert.equal(linked.game_id, shortcut.game_id); assert.equal(linked.appid, 0xfffffffd);
});

test('launch uses non-Steam GameID before the Steam overview is available', async () => {
  const h = harness(); let launched;
  h.window.SteamClient = { Apps: { RunGame: async id => launched = id } };
  await h.load('steamLaunch').runShortcut(0x80000001);
  assert.equal(launched, ((0x80000001n << 32n) | 0x02000000n).toString());
  delete h.window.SteamClient;
  await assert.rejects(h.load('steamLaunch').runShortcut(1), /Steam/);
});

test('external catalog actions accept HTTPS source pages and never executable URLs', () => {
  const exports = {};
  const code = ts.transpileModule(fs.readFileSync(path.join(__dirname, '../src/externalCatalog.ts'), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  }).outputText;
  vm.runInNewContext(code, { exports, URL });
  const url = action_url => exports.externalGameUrl({ acquisition: 'external', action_url });
  assert.equal(url('https://flathub.org/apps/game'), 'https://flathub.org/apps/game');
  for (const invalid of ['javascript:alert(1)', 'file:///etc/passwd', 'https://user:pass@example.com/', 'not a URL', 'http://example.com/']) assert.equal(url(invalid), '');
  assert.equal(exports.externalGameUrl({ action_url: 'https://flathub.org/' }), '');
});
