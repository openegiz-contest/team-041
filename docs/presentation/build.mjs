// Builds presentation.pdf in the repository root from docs/presentation/slides.html.
//
//   node docs/presentation/build.mjs [output.pdf]
//   SUBMISSION=other.md node docs/presentation/build.mjs out.pdf   (to try a draft of the Concept)
//
// The Concept slide takes the team's own words from the section «Concept» of
// SUBMISSION.md (the five labels Enterprise, Users, Decision, Why this choice,
// Real or fictional); a label that is still empty shows the placeholder. Run
// the script again after editing SUBMISSION.md. The screenshots are the files
// in docs/presentation/img: captures of the running stand at 1440x810 in
// headless Chrome (the driver mine/ui/tests/cdp.mjs, as in
// mine/ui/tests/shots.mjs), daylight shots with ?time=HH:MM in the address.
//
// Needs Node 22+ and Chrome or Chromium (CHROME_BIN if it is not in a usual
// place). The page is laid out in headless Chrome through the DevTools
// protocol (the driver in mine/ui/tests/cdp.mjs): long Concept answers are fitted
// and every slide is checked for overflow; the result is then printed with
// Chrome's own --print-to-pdf. No network access is needed.

import { readFileSync, writeFileSync, mkdtempSync, rmSync, copyFileSync, readdirSync, mkdirSync, existsSync, statSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { dirname, join, resolve } from 'node:path';
import { tmpdir } from 'node:os';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { launch, sleep } from '../../mine/ui/tests/cdp.mjs';

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, '../..');
const output = resolve(process.argv[2] ?? join(root, 'presentation.pdf'));
const started = Date.now();

const LABELS = [
  ['Enterprise', 'enterprise'],
  ['Users', 'users'],
  ['Decision', 'decision'],
  ['Why this choice', 'why'],
  ['Real or fictional', 'real'],
];
const PLACEHOLDER = 'Заполняет команда своими словами';
const CHROME = [
  process.env.CHROME_BIN,
  '/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser', '/snap/bin/chromium',
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
].find((path) => path && existsSync(path));

// The text under each label of the Concept section, without HTML comments.
function conceptFromSubmission(markdown) {
  const section = /^## Concept\s*$([\s\S]*?)^## /m.exec(markdown)?.[1] ?? '';
  const out = {};
  LABELS.forEach(([label, key], i) => {
    const start = section.indexOf(`**${label}:**`);
    if (start < 0) { out[key] = ''; return; }
    const from = start + label.length + 5;
    const nextLabel = LABELS.slice(i + 1).map(([l]) => section.indexOf(`**${l}:**`, from)).filter((n) => n >= 0);
    const end = nextLabel.length ? Math.min(...nextLabel) : section.length;
    out[key] = section.slice(from, end).replace(/<!--[\s\S]*?-->/g, '').trim();
  });
  return out;
}

const escape = (text) => text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

// A little Markdown: paragraphs, "- " lists, **bold**, *italic*, `code`, [links](url).
function inline(text) {
  return escape(text)
    .replace(/\*\*(.+?)\*\*/g, '<b>$1</b>')
    .replace(/(^|[^*])\*(?!\s)(.+?)\*/g, '$1<i>$2</i>')
    .replace(/`(.+?)`/g, '<code>$1</code>')
    .replace(/\[(.+?)\]\((.+?)\)/g, '$1');
}
function markdown(text) {
  const blocks = text.split(/\n\s*\n/).map((b) => b.trim()).filter(Boolean);
  return blocks.map((block) => {
    const lines = block.split('\n').map((l) => l.trim());
    if (lines.every((l) => /^[-*] /.test(l))) return `<ul>${lines.map((l) => `<li>${inline(l.slice(2))}</li>`).join('')}</ul>`;
    return `<p>${lines.map(inline).join('<br>')}</p>`;
  }).join('');
}

const concept = conceptFromSubmission(readFileSync(process.env.SUBMISSION ?? join(root, 'SUBMISSION.md'), 'utf8'));
let html = readFileSync(join(here, 'slides.html'), 'utf8');
for (const [, key] of LABELS) {
  const text = concept[key];
  const filled = text
    ? `<div class="a">${markdown(text)}</div>`
    : `<div class="a empty">${PLACEHOLDER}</div>`;
  html = html.replace(`<!--CONCEPT:${key}-->`, filled);
}
const filledCount = LABELS.filter(([, key]) => concept[key]).length;

// Render from a temporary copy beside the images, so that relative paths hold.
const work = mkdtempSync(join(tmpdir(), 'mine-slides-'));
try {
  writeFileSync(join(work, 'slides.html'), html);
  copyFileSync(join(here, 'style.css'), join(work, 'style.css'));
  mkdirSync(join(work, 'img'));
  for (const file of readdirSync(join(here, 'img'))) copyFileSync(join(here, 'img', file), join(work, 'img', file));

  const page = await launch({ width: 1280, height: 720 });
  try {
    await page.goto(pathToFileURL(join(work, 'slides.html')).href);
    await page.waitFor('document.readyState === "complete" && [...document.images].every((i) => i.complete && i.naturalWidth > 0)', 30000, 'the slides and their images');
    await page.evaluate('document.fonts.ready.then(() => true)');
    // Long Concept answers: shrink their type until every box fits its slide.
    await page.evaluate(`(() => {
      const grid = document.querySelector('.concept');
      if (!grid) return;
      let size = 16;
      const over = () => grid.scrollHeight > grid.clientHeight + 1;
      while (over() && size > 10.5) { size -= 0.5; grid.style.setProperty('--concept-size', size + 'px'); }
    })()`);
    await sleep(300);
    const pages = await page.evaluate('document.querySelectorAll(".slide").length');
    const overflow = await page.evaluate(`[...document.querySelectorAll('.slide')].map((s, i) => [i + 1, s.scrollHeight > s.clientHeight + 1 || s.scrollWidth > s.clientWidth + 1]).filter(([, o]) => o).map(([i]) => i)`);
    // The laid-out page, with the fitted Concept type, is what gets printed.
    writeFileSync(join(work, 'slides.html'), `<!doctype html>
${await page.evaluate('document.documentElement.outerHTML')}`);
    await page.close();
    if (!CHROME) throw new Error('Chrome not found: set CHROME_BIN to its path');
    const args = ['--headless=new', '--disable-gpu', '--no-pdf-header-footer', '--run-all-compositor-stages-before-draw',
      `--user-data-dir=${join(work, 'profile')}`, `--print-to-pdf=${output}`, pathToFileURL(join(work, 'slides.html')).href];
    if (process.getuid?.() === 0) args.unshift('--no-sandbox');
    const run = spawnSync(CHROME, args, { encoding: 'utf8', timeout: 120000 });
    if (run.status !== 0 || !existsSync(output) || statSync(output).mtimeMs < started) {
      throw new Error(`Chrome did not print the slides: ${run.error?.message ?? run.stderr}`);
    }
    console.log(`${output}: ${pages} slides; Concept: ${filledCount} of ${LABELS.length} answers filled from SUBMISSION.md`);
    if (overflow.length) console.log(`warning: content overflows on slide(s) ${overflow.join(', ')}`);
  } catch (error) {
    await page.close().catch(() => {});
    throw error;
  }
} finally {
  rmSync(work, { recursive: true, force: true });
}
