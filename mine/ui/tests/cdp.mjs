// A small driver for headless Chrome over the DevTools protocol, with no
// dependencies beyond Node 22+ (built-in WebSocket and fetch). Used by the
// page check and the screenshot script in this directory.
//
// Chrome is taken from CHROME_BIN or from its usual places on Linux, macOS
// and Windows. It runs with --disable-gpu, so the page is rendered in
// software, the way it is on a machine without a graphics card; WebGL then
// runs on Chrome's software renderer. CHROME_FLAGS adds flags, e.g.
// CHROME_FLAGS="--use-angle=swiftshader --enable-unsafe-swiftshader" to draw
// WebGL with SwiftShader, as headless Chrome on Linux does.

const EXTRA_FLAGS = (process.env.CHROME_FLAGS ?? '').split(' ').filter(Boolean);

import { spawn } from 'node:child_process';
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const CANDIDATES = [
  process.env.CHROME_BIN,
  '/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser', '/snap/bin/chromium',
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
];

export const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// The namespace of the mine on a stand: the first of the namespaces the
// page's server names (/config.json) that holds a mine twin, as the page
// itself chooses it.
export async function mineNamespace(base) {
  const config = await fetch(`${base}/config.json`).then((r) => r.json());
  const names = String(config.namespaces ?? '').split(/[\s,]+/).filter(Boolean);
  for (const name of names) {
    const filter = encodeURIComponent('eq(attributes/kind,"mine")');
    const found = await fetch(`${base}/ditto/api/2/search/things?namespaces=${name}&filter=${filter}&fields=thingId`)
      .then((r) => (r.ok ? r.json() : { items: [] })).catch(() => ({ items: [] }));
    if (found.items?.some((item) => item.thingId === `${name}:mine`)) return name;
  }
  throw new Error(`no mine twin in the namespaces the page's server names (${names.join(', ') || 'none'})`);
}

export async function launch({ width = 1280, height = 720 } = {}) {
  const binary = CANDIDATES.find((path) => path && existsSync(path));
  if (!binary) throw new Error('Chrome not found: set CHROME_BIN to its path');
  const profile = mkdtempSync(join(tmpdir(), 'mine-chrome-'));
  const child = spawn(binary, ['--headless=new', '--disable-gpu', ...EXTRA_FLAGS, '--remote-debugging-port=0', `--user-data-dir=${profile}`,
    `--window-size=${width},${height}`, '--hide-scrollbars', '--no-first-run', '--no-default-browser-check',
    '--force-device-scale-factor=1', '--disable-background-timer-throttling', '--disable-renderer-backgrounding',
    '--disable-backgrounding-occluded-windows', 'about:blank'], { stdio: 'ignore' });

  const portFile = join(profile, 'DevToolsActivePort');
  for (let i = 0; i < 100 && !existsSync(portFile); i += 1) await sleep(100);
  if (!existsSync(portFile)) { child.kill(); throw new Error('Chrome did not start'); }
  const port = readFileSync(portFile, 'utf8').split('\n')[0].trim();
  let target = null;
  for (let i = 0; i < 50 && !target; i += 1) {
    const list = await fetch(`http://127.0.0.1:${port}/json/list`).then((r) => r.json()).catch(() => []);
    target = list.find((item) => item.type === 'page');
    if (!target) await sleep(100);
  }
  if (!target) { child.kill(); throw new Error('Chrome has no page'); }

  const socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
  let nextId = 1;
  const waiting = new Map();
  const handlers = new Map();
  socket.onmessage = (message) => {
    const data = JSON.parse(message.data);
    if (data.id && waiting.has(data.id)) {
      const { resolve, reject } = waiting.get(data.id);
      waiting.delete(data.id);
      if (data.error) reject(new Error(`${data.error.message}`));
      else resolve(data.result);
    } else if (data.method) {
      for (const handler of handlers.get(data.method) ?? []) handler(data.params);
    }
  };

  const page = {
    send(method, params = {}) {
      const id = nextId;
      nextId += 1;
      return new Promise((resolve, reject) => {
        waiting.set(id, { resolve, reject });
        socket.send(JSON.stringify({ id, method, params }));
      });
    },
    on(method, handler) {
      if (!handlers.has(method)) handlers.set(method, []);
      handlers.get(method).push(handler);
    },
    // Value of a JavaScript expression in the page; promises are awaited.
    async evaluate(expression) {
      const result = await page.send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
      if (result.exceptionDetails) {
        throw new Error(`page: ${result.exceptionDetails.exception?.description ?? result.exceptionDetails.text}`);
      }
      return result.result.value;
    },
    async goto(url) {
      await page.send('Page.navigate', { url });
    },
    // Wait until the expression is truthy; returns its value.
    async waitFor(expression, timeoutMs = 30000, what = expression) {
      const deadline = Date.now() + timeoutMs;
      for (;;) {
        const value = await page.evaluate(expression).catch(() => null);
        if (value) return value;
        if (Date.now() > deadline) throw new Error(`timed out waiting for: ${what}`);
        await sleep(250);
      }
    },
    // A real mouse click in the middle of the element.
    async click(selector) {
      const box = await page.evaluate(`(() => {
        const node = document.querySelector(${JSON.stringify(selector)});
        if (!node) return null;
        node.scrollIntoView({ block: 'nearest', inline: 'nearest' });
        const r = node.getBoundingClientRect();
        return { x: r.left + r.width / 2, y: r.top + r.height / 2, disabled: Boolean(node.disabled) };
      })()`);
      if (!box) throw new Error(`no element: ${selector}`);
      if (box.disabled) throw new Error(`element is disabled: ${selector}`);
      for (const type of ['mousePressed', 'mouseReleased']) {
        await page.send('Input.dispatchMouseEvent', { type, x: box.x, y: box.y, button: 'left', clickCount: 1 });
      }
    },
    async screenshot(file) {
      const { data } = await page.send('Page.captureScreenshot', { format: 'png' });
      writeFileSync(file, Buffer.from(data, 'base64'));
      return file;
    },
    async close() {
      // Chrome may close the socket before it answers: do not wait for the answer for ever.
      try { await Promise.race([page.send('Browser.close'), sleep(2000)]); } catch { /* already gone */ }
      socket.close();
      await sleep(300);
      child.kill();
      try { rmSync(profile, { recursive: true, force: true }); } catch { /* the profile is in the temp directory */ }
    },
  };

  await page.send('Page.enable');
  await page.send('Runtime.enable');
  await page.send('Network.enable');
  await page.send('Log.enable');
  await page.send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: false });
  return page;
}

// Collects what must stay empty on a healthy page: console errors, uncaught
// exceptions and requests that failed or were answered with an error.
export function watchProblems(page) {
  const problems = [];
  const urls = new Map();
  page.on('Network.requestWillBeSent', ({ requestId, request }) => urls.set(requestId, `${request.method} ${request.url}`));
  page.on('Runtime.consoleAPICalled', ({ type, args }) => {
    if (type === 'error' || type === 'assert') problems.push(`console.${type}: ${args.map((a) => a.value ?? a.description ?? '').join(' ')}`);
  });
  page.on('Runtime.exceptionThrown', ({ exceptionDetails }) => {
    problems.push(`exception: ${exceptionDetails.exception?.description ?? exceptionDetails.text}`);
  });
  page.on('Log.entryAdded', ({ entry }) => {
    if (entry.level === 'error') problems.push(`log: ${entry.text} ${entry.url ?? ''}`);
  });
  page.on('Network.loadingFailed', ({ requestId, errorText, canceled }) => {
    if (!canceled) problems.push(`request failed: ${urls.get(requestId) ?? requestId} (${errorText})`);
  });
  page.on('Network.responseReceived', ({ requestId, response }) => {
    if (response.status >= 400) problems.push(`HTTP ${response.status}: ${urls.get(requestId) ?? response.url}`);
  });
  return problems;
}
