const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const {test} = require('node:test');

async function fixture(state = 'password') {
  const nodes = new Map();
  const node = id => {
    if (!nodes.has(id)) nodes.set(id, {value: '', textContent: '', disabled: false,
      classList: {contains: () => true}, listeners: {},
      addEventListener(name, callback) { this.listeners[name] = callback; },
      replaceChildren() { this.value = ''; }, add() {}});
    return nodes.get(id);
  };
  const calls = [];
  const active = {id: 7, url: 'https://example.test/login'};
  const sandbox = {document: {getElementById: node}, URL, AbortController,
    setTimeout, clearTimeout, Option: class {}, loginPage() {},
    chrome: {tabs: {query: async () => [active]},
      storage: {local: {get: async d => d, set: async () => {}},
        session: {get: async () => ({}), set: async () => {}, remove: async () => {}}},
      scripting: {executeScript: async request => {
        calls.push(request);
        return [{documentId: 'doc-1', result: {state: request.args[0] === 'check' ? state : 'filled'}}];
      }}},
    fetch: async (url, options) => {
      if (options.method === 'OPTIONS') return {status: 204, ok: true};
      calls.push({consume: true});
      return {ok: true, json: async () => ({username: 'synthetic-user', password: 'synthetic-secret'})};
    }};
  vm.createContext(sandbox);
  vm.runInContext(readFileSync(join(__dirname, '../../browser_extension/popup.js'), 'utf8'), sandbox);
  await new Promise(resolve => setImmediate(resolve));
  node('handle').value = 'synthetic-handle';
  node('handle').listeners.input();
  return {nodes, node, calls, sandbox, active, click: () => node('fill').listeners.click()};
}

test('initial refresh works and missing fields preserve the unused handoff', async () => {
  const f = await fixture('missing');
  assert.equal(f.node('fill').disabled, false);
  await f.click();
  assert.equal(f.calls.some(call => call.consume), false);
  assert.equal(f.node('handle').value, 'synthetic-handle');
  assert.match(f.node('status').textContent, /No handle was consumed/);
});

test('password delivery targets the preflight document and clears the handoff', async () => {
  const f = await fixture();
  await f.click();
  assert.equal(f.calls.filter(call => call.consume).length, 1);
  const fill = f.calls.find(call => call.args?.[0] === 'fill');
  assert.equal(fill.target.documentIds[0], 'doc-1');
  assert.equal(fill.args[1], 'https://example.test');
  assert.equal(f.node('handle').value, '');
});

test('concurrent clicks cannot consume a handoff twice', async () => {
  const f = await fixture();
  await Promise.all([f.click(), f.click()]);
  assert.equal(f.calls.filter(call => call.consume).length, 1);
});

test('selected request never fills a username on another origin', async () => {
  const f = await fixture('username');
  vm.runInContext(`pendingRequests = [{handle: 'synthetic-handle', agent_id: 'codex',
    target_origin: 'https://other.test', username: 'synthetic-user'}]`, f.sandbox);
  f.node('requests').value = '0';
  await f.click();
  assert.equal(f.calls.length, 0);
  assert.match(f.node('status').textContent, /another site/);
});

test('navigation after preflight prevents consumption', async () => {
  const f = await fixture();
  const execute = f.sandbox.chrome.scripting.executeScript;
  f.sandbox.chrome.scripting.executeScript = async request => {
    const result = await execute(request);
    if (request.args[0] === 'check') f.active.url = 'https://other.test/login';
    return result;
  };
  await f.click();
  assert.equal(f.calls.some(call => call.consume), false);
  assert.equal(f.node('handle').value, 'synthetic-handle');
});

test('navigation during delivery never retargets a new document', async () => {
  const f = await fixture();
  const execute = f.sandbox.chrome.scripting.executeScript;
  f.sandbox.chrome.scripting.executeScript = async request => {
    if (request.args[0] === 'fill') {
      assert.deepEqual(Array.from(request.target.documentIds), ['doc-1']);
      throw new Error('Original document no longer exists');
    }
    return execute(request);
  };
  await f.click();
  assert.equal(f.calls.filter(call => call.consume).length, 1);
  assert.equal(f.node('handle').value, '');
  assert.doesNotMatch(f.node('status').textContent, /Login filled/);
});

test('expired request never injects fields and clears the attempted handle', async () => {
  const f = await fixture();
  f.sandbox.fetch = async (url, options) => options.method === 'OPTIONS'
    ? {status: 204, ok: true}
    : {ok: false, json: async () => ({error: 'Credential handle expired'})};
  await f.click();
  assert.equal(f.calls.some(call => call.args?.[0] === 'fill'), false);
  assert.equal(f.node('handle').value, '');
  assert.match(f.node('status').textContent, /expired/);
});

test('a new browser session requires pairing again', async () => {
  const f = await fixture();
  // The fixture has persistent preferences but empty chrome.storage.session.
  await f.node('refresh-requests').listeners.click();
  assert.match(f.node('request-detail').textContent, /Pair this browser session/);
  assert.equal(f.calls.some(call => call.consume), false);
});

test('fill stays disabled until there is a selected request or manual handle', async () => {
  const f = await fixture();
  f.node('handle').value = '';
  f.node('handle').listeners.input();
  assert.equal(f.node('fill').disabled, true);
  f.node('handle').value = 'synthetic-handle';
  f.node('handle').listeners.input();
  assert.equal(f.node('fill').disabled, false);
  await f.click();
  assert.equal(f.node('fill').disabled, true);
});

test('expired approved request never fills the username step', async () => {
  const f = await fixture('username');
  vm.runInContext(`pendingRequests = [{handle: 'synthetic-handle', agent_id: 'codex',
    target_origin: 'https://example.test', username: 'synthetic-user',
    expires_at: '2020-01-01T00:00:00Z'}]`, f.sandbox);
  f.node('requests').value = '0';
  await f.click();
  assert.equal(f.calls.length, 0);
  assert.match(f.node('status').textContent, /expired/);
});

test('missing document identity refuses consumption', async () => {
  const f = await fixture();
  f.sandbox.chrome.scripting.executeScript = async () => [{result: {state: 'password'}}];
  await f.click();
  assert.equal(f.calls.some(call => call.consume), false);
  assert.match(f.node('status').textContent, /identify this document/);
});

test('username delivery stays bound to the checked document', async () => {
  const f = await fixture('username');
  vm.runInContext(`pendingRequests = [{handle: 'synthetic-handle', agent_id: 'codex',
    target_origin: 'https://example.test', username: 'synthetic-user',
    expires_at: new Date(Date.now() + 60000).toISOString()}]`, f.sandbox);
  f.node('requests').value = '0';
  await f.click();
  const delivery = f.calls.find(call => call.args?.[0] === 'username');
  assert.deepEqual(Array.from(delivery.target.documentIds), ['doc-1']);
  assert.equal(f.calls.some(call => call.consume), false);
});
