import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import test from 'node:test';
import { Tab } from '../lib/tab.js';

function fixture() {
  const page = new EventEmitter();
  page.setDefaultNavigationTimeout = () => {};
  page.setDefaultTimeout = () => {};
  page._snapshotForAI = async () => '- heading "Fixture"';
  page.url = () => 'http://fixture.invalid/';
  page.title = async () => 'Fixture';
  const tab = new Tab({ config: { spanSize: 5000 } }, page, () => {});
  return { page, tab };
}

test('a noisy page has bounded console history without retaining protocol handles', () => {
  const { page, tab } = fixture();
  for (let index = 0; index < 10000; index++) {
    const message = {
      payload: new Array(3000).fill(index),
      type: () => 'log',
      text: () => `${index}:` + 'x'.repeat(20000),
      location: () => ({ url: 'http://fixture.invalid/', lineNumber: index }),
    };
    page.emit('console', message);
    // Reading retained output must not call back into a disposed protocol
    // object. The old closure also kept message.payload alive indefinitely.
    message.text = () => { throw new Error('Protocol handle was disposed'); };
  }
  const messages = tab.consoleMessages();
  assert.equal(messages.length, 1000);
  assert.match(messages.at(-1).toString(), /9999:/);
  assert(messages.every(message => message.text.length <= 2003));
});

test('snapshot emits recent console messages once, retaining bounded history', async () => {
  const { page, tab } = fixture();
  for (let index = 0; index < 100; index++)
    page.emit('pageerror', new Error(`error-${index}`));
  const first = await tab.captureSnapshot();
  const second = await tab.captureSnapshot();
  assert.equal((first.match(/^- Error: error-/gm) || []).length, 50);
  assert(!second.includes('### New console messages'));
  assert.equal(tab.consoleMessages().length, 100);
});

test('network history is bounded and late responses cannot resurrect evicted requests', () => {
  const { page, tab } = fixture();
  const requests = Array.from({ length: 3000 }, (_, index) => ({ index }));
  for (const request of requests)
    page.emit('request', request);
  page.emit('response', { request: () => requests[0] });
  const recentResponse = { request: () => requests.at(-1) };
  page.emit('response', recentResponse);
  assert.equal(tab.requests().size, 1000);
  assert(!tab.requests().has(requests[0]));
  assert.equal(tab.requests().get(requests.at(-1)), recentResponse);
});
