const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const {test} = require('node:test');
const source = readFileSync(join(__dirname, '../../browser_extension/login_fields.js'), 'utf8');

function fixture({users = 1, passwords = 1, hidden = false, newPassword = false} = {}) {
  class Input {
    constructor() { this.form = 'login'; this.events = []; this.currentValue = ''; this.isConnected = true; }
    get value() { return this.currentValue; }
    set value(value) { this.currentValue = value; }
    getClientRects() { return hidden ? [] : [1]; }
    dispatchEvent(event) { this.events.push(event.type); }
  }
  const user = Array.from({length: users}, () => new Input());
  const pass = Array.from({length: passwords}, () => new Input());
  pass.forEach(input => { input.autocomplete = newPassword ? 'new-password' : 'current-password'; });
  const sandbox = {location: {origin: 'https://example.test'}, HTMLInputElement: Input,
    Event: class { constructor(type) { this.type = type; } },
    getComputedStyle: () => ({visibility: 'visible'}),
    document: {querySelectorAll: selector => selector === 'input[type="password"]' ? pass : user}};
  vm.createContext(sandbox);
  vm.runInContext(source, sandbox);
  return {sandbox, user, pass, run: (...args) => sandbox.loginPage(...args)};
}

test('preflight never changes values; delivery supports password-only steps', () => {
  const f = fixture({users: 0});
  assert.equal(f.run('check', 'https://example.test').state, 'password');
  assert.equal(f.pass[0].value, '');
  assert.equal(f.run('fill', 'https://example.test', 'user', 'synthetic').state, 'filled');
  assert.equal(f.pass[0].value, 'synthetic');
});
test('wrong origin and changed documents never receive values', () => {
  const f = fixture();
  assert.equal(f.run('check', 'https://example.test').state, 'password');
  f.sandbox.location.origin = 'https://other.test';
  assert.equal(f.run('fill', 'https://example.test', 'user', 'synthetic').state, 'origin_changed');
  assert.equal(f.user[0].value, '');
  assert.equal(f.pass[0].value, '');
});
test('ambiguous, hidden and new-password forms require user recovery', () => {
  assert.equal(fixture({passwords: 2}).run('check', 'https://example.test').state, 'ambiguous');
  assert.equal(fixture({users: 2}).run('check', 'https://example.test').state, 'ambiguous');
  assert.equal(fixture({hidden: true}).run('check', 'https://example.test').state, 'missing');
  assert.equal(fixture({newPassword: true}).run('check', 'https://example.test').state, 'new_password');
});
test('username-first step fills only the username and does not submit', () => {
  const f = fixture({passwords: 0});
  assert.equal(f.run('username', 'https://example.test', 'synthetic-user').state, 'username_filled');
  assert.equal(f.user[0].value, 'synthetic-user');
  assert.deepEqual(f.user[0].events, ['input', 'change']);
  assert.equal(f.run('fill', 'https://example.test', 'user', 'secret').state, 'changed');
});

test('reactive replacement never reports a successful fill on a detached field', () => {
  const f = fixture();
  f.user[0].dispatchEvent = () => { f.pass[0].isConnected = false; };
  assert.equal(f.run('fill', 'https://example.test', 'synthetic-user', 'synthetic-secret').state, 'changed');
  assert.equal(f.pass[0].value, '');
});

test('a page that clears the password does not receive a false success', () => {
  const f = fixture();
  f.pass[0].dispatchEvent = () => { f.pass[0].value = ''; };
  assert.equal(f.run('fill', 'https://example.test', 'synthetic-user', 'synthetic-secret').state, 'changed');
});
