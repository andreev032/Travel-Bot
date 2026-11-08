const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('webapp/record-sync.js', 'utf8');

async function start({local = {}, failLoad = false} = {}) {
  const elements = [];
  const script = {dataset: {kind: 'checklist', key: 'travel_checklist_v1'}, textContent: ''};
  let stored = {revision: 0, data: null};
  let failWrite = false;
  const storage = new Map(Object.entries(local));
  const context = vm.createContext({
    URL, URLSearchParams, AbortSignal, crypto: require('node:crypto').webcrypto,
    Blob, confirm: () => true, clearTimeout() {}, setTimeout: () => 1,
    location: {hash: '#api=https%3A%2F%2Ftest.up.railway.app', reload() {}},
    window: {Telegram: {WebApp: {initData: 'signed-test'}}},
    localStorage: {getItem: k => storage.get(k) || null,
      setItem: (k,v) => storage.set(k,v), removeItem: k => storage.delete(k)},
    document: {
      getElementById: () => script,
      createElement: tag => { const e = {tag, style: {}, append() {}}; elements.push(e); return e; },
      body: {style: {}, append() {}}
    },
    fetch: async (url, options) => {
      if (failLoad || (options.method === 'POST' && failWrite)) throw Error('offline');
      if (options.method === 'POST') {
        const body = JSON.parse(options.body);
        if (body.revision !== stored.revision) return {ok: false, status: 409};
        stored = {revision: stored.revision + 1, data: body.data};
      }
      return {ok: true, json: async () => ({...stored, user:'1'})};
    }
  });
  await vm.runInContext(source, context);
  return {context, storage, elements, offline: v => {failWrite=v;},
    current: () => stored, conflict: () => {stored.revision++;}};
}
(async () => {
  const app = await start({local: {travel_checklist_v1: '{"old":true}'}});
  assert(app.context.window.personalRecords);
  const retry = app.elements.find(e => e.tag === 'button');
  app.offline(true);
  await retry.onclick();
  assert(app.storage.has('travel_checklist_v1'));
  assert(app.storage.has('cloud_draft_1_checklist'));
  assert.equal(app.current().data, null);
  app.offline(false);
  await retry.onclick();
  assert.equal(app.current().data.old, true);
  assert(app.storage.has('travel_checklist_v1'));
  app.context.window.personalRecords.write('{}');
  await retry.onclick();
  assert.deepEqual(app.current().data, {});
  app.context.window.personalRecords.write('{"new":true}');
  app.conflict();
  await retry.onclick();
  assert(app.storage.has('cloud_draft_1_checklist'));
  const failed = await start({failLoad:true});
  assert.equal(failed.context.window.personalRecords, undefined);
  for (const kind of ['diary','checklist','stats','splitwise','russia']) {
    const page = fs.readFileSync('webapp/' + kind + '.html','utf8');
    const inline = page.match(/<script type="text\/plain"[^>]*>([\s\S]*?)<\/script>/);
    assert(inline, kind);
    new vm.Script(inline[1]);
    assert(page.includes('record-sync.js'));
  }
  console.log('6 browser scenarios and 5 Mini App scripts passed');
})().catch(error => {console.error(error); process.exitCode=1;});
