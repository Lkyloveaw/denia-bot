const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const html = readFileSync(join(__dirname, '..', 'dashboard.html'), 'utf8');

function sourceBetween(start, end) {
  const begin = html.indexOf(start);
  const finish = html.indexOf(end, begin);
  assert.ok(begin >= 0 && finish > begin, start);
  return html.slice(begin, finish);
}

function element(value = '') {
  return {
    value, children: [], dataset: {}, textContent: '',
    replaceChildren() { this.children = []; },
    append(...children) { this.children.push(...children); },
    setAttribute() {},
  };
}

function openLogsPage() {
  const requests = [];
  const timeouts = new Map();
  const nodes = new Map([
    ['logSource', element('nonebot.log')],
    ['logTail', element('200')],
    ['logFilter', element('all')],
  ]);
  const $ = id => {
    if (!nodes.has(id)) nodes.set(id, element());
    return nodes.get(id);
  };
  let poll;
  const context = vm.createContext({
    $, AbortController,
    document: { hidden: false, createElement: () => element() },
    location: { hash: '#events' },
    setInterval(callback, delay) {
      assert.equal(delay, 5000);
      assert.equal(poll, undefined, 'the poll timer is a singleton');
      poll = callback;
      return 1;
    },
    setTimeout(callback, delay) {
      const id = Symbol();
      timeouts.set(id, { callback, delay });
      return id;
    },
    clearTimeout(id) { timeouts.delete(id); },
    fetch(path, options) {
      return new Promise((resolve, reject) => {
        options.signal?.addEventListener('abort', () => reject(options.signal.reason), { once: true });
        requests.push({
          path,
          complete(data, ok = true) { resolve({ ok, json: async () => data }); },
        });
      });
    },
  });
  vm.runInContext(
    sourceBetween('async function api(path,', 'async function run(f)') +
    sourceBetween('let eventEntries=', 'const CHAT_MAX_MESSAGES=') +
    sourceBetween('async function loadLogsPage()', 'function confirmClearEvents()'),
    context,
  );
  return {
    $, requests, timeouts,
    run: source => vm.runInContext(source, context),
    tick() { assert.ok(poll); poll(); },
  };
}

const flush = () => new Promise(resolve => setImmediate(resolve));
const runtime = label => ({ available: true, label, lines: ['INFO example'] });

test('a slow first runtime response renders and polling resumes', async () => {
  const page = openLogsPage();
  const initial = page.run('loadLogsPage()');
  const [logs, parser] = page.requests;

  page.tick();
  assert.equal(page.requests.length, 2, 'do not replace the initial logs request');
  logs.complete(runtime('First result'));
  await flush();
  assert.match(page.$('logMeta').textContent, /First result/);

  page.tick();
  assert.equal(page.requests.length, 3, 'poll even while parser events are still pending');
  page.requests[2].complete(runtime('Polled result'));
  parser.complete({ entries: [] });
  await initial;
  assert.match(page.$('logMeta').textContent, /Polled result/);
});

test('switching log sources supersedes an old request', async () => {
  const page = openLogsPage();
  const old = page.run('loadRuntimeLogs()');
  page.$('logSource').value = 'nonebot.err.log';
  const current = page.run('loadRuntimeLogs()');
  assert.match(page.requests[1].path, /name=nonebot\.err\.log/);

  page.requests[1].complete(runtime('New source'));
  await current;
  page.requests[0].complete(runtime('Old source'));
  await old;
  assert.match(page.$('logMeta').textContent, /New source/);
});

test('a timed-out runtime request releases the poll', async () => {
  const page = openLogsPage();
  page.run('startLogPolling()');
  page.tick();
  const [timeout] = page.timeouts.values();
  assert.ok(timeout.delay > 5000, 'allow a response to outlast one poll interval');
  timeout.callback();
  await flush();
  assert.match(page.$('logMeta').textContent, /超时/);
  assert.equal(page.timeouts.size, 0);

  page.tick();
  assert.equal(page.requests.length, 2);
  page.requests[1].complete(runtime('Recovered'));
  await flush();
  assert.match(page.$('logMeta').textContent, /Recovered/);
});

test('parser errors survive filtering until a successful refresh', async () => {
  const page = openLogsPage();
  const failing = page.run('loadEvents()');
  page.requests[0].complete({ detail: '503' }, false);
  await failing;

  page.$('eventSearch').value = 'keyword';
  page.run('renderEvents()');
  assert.match(page.$('eventEmpty').textContent, /读取失败.*503/);
  page.$('eventKind').value = 'error';
  page.run('renderEvents()');
  assert.match(page.$('eventEmpty').textContent, /读取失败.*503/);

  const stale = page.run('loadEvents()');
  const refresh = page.run('loadEvents()');
  page.requests[2].complete({ entries: [] });
  await refresh;
  page.requests[1].complete({ detail: 'old error' }, false);
  await stale;
  assert.match(page.$('eventEmpty').textContent, /暂时没有记录/);
});
