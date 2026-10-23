// The advisor card at the top of the right column. When an incident appears
// (a loader down, the crusher stopped, a road closed, rain) the advisor module
// builds a few responses, the first of them "change nothing", has the
// simulator play each one 12 hours ahead (a planned reline of the SAG mill: over
// one horizon that holds the whole stop of every option) in a forecast slot of its own, ranks
// them by money and copper and publishes the ranking in its twin. The card
// shows that ranking and sends «Применить».

import { sendMessage } from './api.js';
import { NAMESPACE, twins, feature, resolveRef, titleOf } from './store.js';
import { $, el, setText, twinHref, twinLink } from './dom.js';
import { formatTenge, formatTengeSpread, formatDelta, formatNumber, formatMillions, formatSimClock } from './labels.js';
import { readForecast, sparkSvg, keySample, HORIZON_H } from './forecast.js';

export const advisorId = () => `${NAMESPACE}:module-advisor`;
const OPTIONS = 4;
const CONFIRM_MS = 20000;

let pending = null;                  // {option, sentAt, timer}: «Применить» sent, no answer yet

const advisor = () => twins.get(advisorId());
// A simulated moment as «DD.MM HH:MM»: a planned stop of the SAG mill lasts days.
const formatSimDay = (iso) => { const m = /^\d{4}-(\d\d)-(\d\d)T(\d\d:\d\d)/.exec(iso ?? ''); return m ? `${m[2]}.${m[1]} ${m[3]}` : '—'; };
// The card speaks of the module by its short name; the twin's full name is behind the link.
const nameOf = (twin) => (twin ? titleOf(twin).replace(/^Модуль-/, '').replace(/^./, (c) => c.toUpperCase()) : 'Советник');

function incidentOf(twin) {
  const key = String(feature(twin, 'incident') ?? '').trim();
  if (!key || key === 'none') return null;
  // The card shows the expected end on a line of its own; the incident's text says it too, for the journal.
  const text = String(feature(twin, 'incident_text') ?? '').trim().replace(/\s*Ожидаемое окончание — [^.]*?\([^)]*\)\./, '');
  return {
    key,
    text: text || key,
    thing: resolveRef(feature(twin, 'incident_thing')),
    since: String(feature(twin, 'incident_sim_time') ?? ''),
    end: String(feature(twin, 'incident_end_sim_time') ?? ''),
    basis: String(feature(twin, 'incident_basis') ?? '').trim(),
  };
}

// Parts of an option's margin difference, in the order they are read: what
// copper brings, then what the stocks and the moves cost. The model publishes
// them as option<N>_<part>_kzt; the names below are the ones it may use.
const SPLIT = [
  [/^(cu_)?revenue(_delta)?$|^copper(_revenue)?$/, 'медь'],
  [/^grade(_delta)?$/, 'из запасов'],
  [/^work(_delta)?$/, 'работа парка'],
  [/^(inventory|stock|stock_value|inventory_value)(_delta)?$/, 'запасы руды и вскрыша'],
  [/^(strip|stripping)(_delta)?$/, 'вскрыша'],
  [/^rehandl(e|ing)(_delta)?$/, 'перевалка'],
  [/^diesel(_cost)?(_delta)?$/, 'дизель'],
  [/^(power|energy|electricity)(_cost)?(_delta)?$/, 'электроэнергия'],
  [/^resources$/, 'ресурсы смены'],
];
function marginSplit(twin, n) {
  const parts = [];
  const prefix = `option${n}_`;
  for (const name of Object.keys(twin.features ?? {})) {
    if (!name.startsWith(prefix) || !name.endsWith('_kzt')) continue;
    const part = name.slice(prefix.length, -4).replace(/^(delta|split)_/, '');
    const value = feature(twin, name);
    if (part === 'delta' || part === '' || /(^|_)se$|spread|sigma|std/.test(part) || typeof value !== 'number') continue;
    const index = SPLIT.findIndex(([pattern]) => pattern.test(part));
    if (index < 0) continue;
    parts.push({ order: index, title: SPLIT[index][1], value });
  }
  return parts.sort((a, b) => a.order - b.order);
}

// How sure the advisor is of an option's gain, as it says it (in words; a
// share from 0 to 1 is put in words here), with a level for the colour.
function confidenceOf(value) {
  if (typeof value === 'number' && Number.isFinite(value)) {
    const share = value > 1 ? value / 100 : value;
    const word = share >= 0.8 ? 'высокая' : share >= 0.5 ? 'средняя' : 'низкая';
    return { text: `достоверность: ${word}`, level: share >= 0.8 ? 'high' : share >= 0.5 ? 'mid' : 'low', full: `${word} (${formatNumber(100 * share, 0)} %)` };
  }
  const full = String(value ?? '').trim();
  if (!full || !/[а-яё]/i.test(full)) return null;
  // «нет: в пределах разброса», «нет: вариант хуже»: the reason is what the card shows.
  const none = /^нет\s*[:—-]\s*(.+)$/i.exec(full);
  if (none) return { text: none[1], level: 'low', full };
  const level = /высок/i.test(full) ? 'high' : /низк|мал|слаб/i.test(full) ? 'low' : 'mid';
  // A long sentence is kept for the tooltip; the card shows its first words.
  const words = full.length <= 22 ? full : full.split(/[,.;:(]/)[0].slice(0, 22).trim();
  return { text: /уверен|надёж|надеж|достовер/i.test(words) ? words : `достоверность: ${words}`, level, full };
}

// The options as published, in the order of their numbers: the number is the
// one the advisor uses in the journal and in `applied_option`.
function optionsOf(twin) {
  const options = [];
  for (let n = 1; n <= OPTIONS; n += 1) {
    const f = (name) => feature(twin, `option${n}_${name}`);
    const label = String(f('label') ?? '').trim();
    if (!label) continue;
    const slot = resolveRef(f('forecast'));
    const result = slot && twins.has(slot) ? readForecast(twins.get(slot)) : null;
    const ready = Boolean(result && result.status === 'ready');
    const rank = Number(f('rank')) || 0;
    options.push({
      n,
      rank,
      label,
      ready,
      failed: result?.status === 'error',
      deltaKzt: rank && typeof f('delta_kzt') === 'number' ? f('delta_kzt') : ready ? (result.delta.total ?? result.delta.margin) : undefined,
      deltaCu: rank && typeof f('cu_delta_t') === 'number' ? f('cu_delta_t') : ready ? result.delta.cu : undefined,
      spreadKzt: typeof f('gain_se_kzt') === 'number' && f('gain_se_kzt') > 0 ? f('gain_se_kzt') : undefined,
      confidence: confidenceOf(f('confidence')),
      summary: String(f('summary') ?? ''),
      copperText: String(f('copper_text') ?? ''),
      actions: String(f('actions_text') || f('actions') || ''),
      // What the option's response resources cost, and why it pays or does not.
      cost: typeof f('cost_kzt') === 'number' ? f('cost_kzt') : 0,
      reason: String(f('reason') ?? '').trim(),
      split: marginSplit(twin, n),
      result: ready ? result : null,
    });
  }
  return options;
}

function tone(value) {
  if (typeof value !== 'number' || Math.abs(value) < 50000) return '';
  return value > 0 ? 'better' : 'worse';
}

const end = (values) => (values.length ? values[values.length - 1] : undefined);
// The first `n` sentences of a Russian text (a sentence ends with ". " before a capital letter).
const firstSentences = (text, n) => text.split(/(?<=\.)\s+(?=[А-ЯЁA-Z])/).slice(0, n).join(' ');

// A short line from the option's forecast: copper and the stockpile at the horizon, with and without it.
function shortSummary(option) {
  const r = option.result;
  if (!r) return option.failed ? 'Прогноз не получен.' : 'Прогноз считается…';
  const parts = [];
  if (typeof r.a.cu === 'number' && typeof r.b.cu === 'number') parts.push(`медь ${formatNumber(r.b.cu, 1)} т против ${formatNumber(r.a.cu, 1)}`);
  const sa = end(r.a.stockpile);
  const sb = end(r.b.stockpile);
  if (typeof sa === 'number' && typeof sb === 'number') parts.push(`склад через ${formatNumber(r.horizon, 0)} ч ${formatNumber(sb, 0)} % против ${formatNumber(sa, 0)} %`);
  return parts.length ? `${parts.join(', ')}.` : '';
}

function optionRow(option, { recommended, applied, ready, reference, horizon }) {
  const apply = el('button', { type: 'button', className: 'apply', dataset: { option: String(option.n) }, disabled: !ready,
    'aria-label': `Применить вариант ${option.n} «${option.label}»`, title: option.actions ? `Что будет отправлено: ${option.actions}` : '' }, 'Применить');
  apply.addEventListener('click', () => send(option));
  const isReference = option.n === reference;
  // The gain with its spread (one standard error) when the advisor gives it, and its confidence in words.
  const spread = typeof option.spreadKzt === 'number' ? `; разброс ±${formatTenge(option.spreadKzt)} (стандартная ошибка)` : '';
  const sure = option.confidence;
  const figures = el('p', { className: 'adv-figures' },
    isReference
      ? el('span', { className: 'muted' }, 'точка отсчёта')
      : el('span', { className: 'adv-money', dataset: { tone: tone(option.deltaKzt) },
        title: `Разница итога с поправкой за ${horizon} ч против «ничего не менять»: переменная маржа плюс оценка запасов руды и вскрыши${spread}` },
      typeof option.deltaKzt === 'number' ? formatTengeSpread(option.deltaKzt, option.spreadKzt, { sign: true }) : '—'),
    !isReference && typeof option.deltaCu === 'number'
      ? el('span', { className: 'adv-cu', title: `Разница меди в концентрате за ${horizon} ч` }, `${formatDelta(option.deltaCu, 1)} т Cu`) : null,
    !isReference && sure
      ? el('span', { className: 'adv-conf', dataset: { level: sure.level },
        title: `Достоверность выигрыша против «ничего не менять» по разбросу прогноза: ${sure.full}` }, sure.text) : null,
    !isReference && option.cost >= 50000
      ? el('span', { className: 'adv-cost', title: `Ресурсы смены за ${horizon} ч: бригады, техника и материалы этого варианта; уже вычтены из итога` },
        `ресурсы ${formatTenge(option.cost)}`) : null);
  // Where the margin difference comes from, in millions of tenge.
  // Parts that round to zero are left out; the tooltip names all of them.
  const parts = option.split.filter((part) => Math.abs(part.value) >= 50000);
  const split = !isReference && parts.length
    ? el('p', { className: 'adv-split',
      title: `Из чего складывается разница за ${horizon} ч, млн ₸: ${option.split.map((part) => `${part.title} ${formatMillions(part.value, { sign: true, digits: 1 })}`).join(', ')}. «Из запасов» — медь сверх средней руды месторождения, которую отдали склады и забои: она вычитается, потому что эта руда не будет переработана потом; «работа парка» — выемка руды и вскрыши, которую иначе пришлось бы сделать позже.` },
      ...parts.flatMap((part, i) => [i ? ' · ' : '', `${part.title} `, el('b', { dataset: { tone: tone(part.value) } }, formatMillions(part.value, { sign: true, digits: 1 }))]),
      ' млн ₸')
    : null;
  const spark = option.result ? sparkSvg(option.result.a.mill, option.result.b.mill, 58, 26,
    `Подача на мельницу за ${option.result.horizon} ч: пунктир — ничего не менять, сплошная — этот вариант`) : null;
  // Where the copper difference comes from: the first sentences on the card, all of them in the tooltip.
  // Why it pays or does not, in one sentence; without it, where its copper comes from.
  const copper = !isReference && option.reason
    ? el('p', { className: 'adv-reason', title: option.copperText || option.reason }, option.reason)
    : !isReference && option.copperText
      ? el('p', { className: 'adv-copper', title: option.copperText }, firstSentences(option.copperText, 2)) : null;
  return el('li', { className: 'adv-option', title: option.summary,
    dataset: { option: String(option.n), rank: String(option.rank), recommended: String(recommended), applied: String(applied) } },
  el('span', { className: 'adv-rank', title: `Вариант ${option.n}` }, String(option.n)),
  el('p', { className: 'adv-label' }, option.label,
    recommended ? el('span', { className: 'adv-tag' }, 'рекомендуется') : null,
    applied ? el('span', { className: 'adv-tag adv-tag-done' }, 'применён') : null),
  figures,
  apply,
  split,
  copper,
  el('div', { className: 'adv-detail' }, spark, el('p', { className: 'adv-summary' }, shortSummary(option))));
}

async function send(option) {
  clearTimeout(pending?.timer);
  pending = { option: option.n, sentAt: Date.now() };
  showHint(`«Применить» отправлено: вариант ${option.n} «${option.label}». Ждём ответа советника…`, 'wait');
  pending.timer = setTimeout(() => {
    if (!pending) return;
    const warning = pending.warning;
    pending = null;
    showHint(warning || 'Подтверждения нет: советник не ответил.', 'warn');
  }, CONFIRM_MS);
  try {
    await sendMessage(advisorId(), 'apply', { option: option.n });
  } catch (error) {
    clearTimeout(pending?.timer);
    pending = null;
    showHint(`Команда не принята платформой: ${error.message}`, 'warn');
  }
}

let hintText = '';
let hintTone = '';
function showHint(text, toneName = '') {
  hintText = text;
  hintTone = toneName;
  const hint = $('#advisor .hint');
  if (!hint) return;
  setText(hint, text);
  hint.dataset.tone = toneName;
}

// What the journal says about the advisor after «Применить» is its answer.
// Other services may also note the message; the advisor's own record is the
// one that says what was applied, so a warning only counts if nothing else comes.
export function journalEvent(event, thingId) {
  if (!pending || thingId !== advisorId()) return;
  if (event.severity && event.severity !== 'info') {
    pending.warning = event.text;
    return;
  }
  clearTimeout(pending.timer);
  pending = null;
  showHint(`Советник ответил в ${formatSimClock(event.sim_time)}, запись есть в журнале.`, '');
}

// The advisor's record of what it applied, as one sentence: the option's
// label (the number is in the bold lead) and how many messages went out; the
// raw messages stay in the tooltip.
function appliedSentence(text, applied, options) {
  const label = options.find((option) => option.n === applied)?.label;
  const sent = /сообщений отправлено: \d+/.exec(text)?.[0];
  if (label) return `«${label}»${sent ? `; ${sent}.` : '.'}`;
  return text.replace(/^Советн?и[кч]: /, '').replace(/(сообщений отправлено: \d+\.).*$/s, '$1');
}

let key = '';
let incidentKey = '';

export function render() {
  const holder = $('#advisor');
  if (!holder) return;
  const twin = advisor();
  if (!twin) {
    if (key !== 'none') { key = 'none'; holder.replaceChildren(); holder.hidden = true; }
    return;
  }
  holder.hidden = false;
  const name = nameOf(twin);
  const incident = incidentOf(twin);
  const enabled = feature(twin, 'enabled') !== false;
  if (!incident) {
    const next = `quiet|${enabled}|${name}`;
    if (key !== next) {
      key = next;
      holder.replaceChildren(el('p', { className: 'adv-quiet',
        title: 'При отказе экскаватора, остановке дробилки, закрытой дороге или дожде здесь появятся варианты действий, проверенные прогнозом на 12 часов.' },
      el('span', { className: 'dot', dataset: { tone: enabled ? 'good' : 'idle' } }),
      el('span', { className: 'adv-quiet-text' },
        el('a', { className: 'twin-link', href: twinHref(advisorId()) }, name),
        enabled ? ': происшествий нет' : ' выключен',
        el('span', { className: 'muted' }, enabled ? ' · варианты действий появятся при сбое' : ' · при сбое вариантов не будет'))));
    }
    return;
  }
  if (incident.key !== incidentKey) {
    // A new incident: what was said about the previous one is gone, and the
    // card comes to the top of the stack.
    incidentKey = incident.key;
    hintText = '';
    hintTone = '';
    const stack = document.getElementById('control');
    if (stack) stack.scrollTop = 0;
  }
  const status = String(feature(twin, 'analysis_status') ?? '');
  const ready = status === 'ready' && enabled;
  const options = optionsOf(twin);
  const recommended = Number(feature(twin, 'recommended')) || 0;
  // An option applied for this incident (the advisor keeps the last one it applied).
  const appliedAt = String(feature(twin, 'applied_sim_time') ?? '');
  const appliedNow = Number(feature(twin, 'applied_option')) && appliedAt && (!incident.since || appliedAt >= incident.since);
  const applied = appliedNow ? Number(feature(twin, 'applied_option')) : 0;
  const appliedText = appliedNow ? String(feature(twin, 'applied_text') ?? '') : '';
  const reference = options.find((option) => /^ничего не (менять|делать)/i.test(option.label))?.n ?? 1;
  const slots = options.map((option) => (option.result ? option.result.summary.length + option.result.a.mill.join() : option.failed));
  const next = JSON.stringify([incident, status, enabled, options.map(({ result, ...rest }) => rest), slots, recommended, applied, appliedText, name,
    feature(twin, 'analysis_horizon_h')]);
  if (key === next) return;
  key = next;

  // The horizon of this analysis: the forecast's 12 h, or longer when every option's whole stop must fit in it
  // (a planned reline of the SAG mill: the same horizon for all of its options).
  const horizon = Number(feature(twin, 'analysis_horizon_h')) || HORIZON_H;
  const whole = horizon > HORIZON_H ? ' (один горизонт для всех: вся остановка и пуск мельницы каждого варианта)' : '';
  const lead = !enabled ? `${name} выключен: варианты не применяются.`
    : status === 'ready' ? `Каждый вариант проигран на ${horizon} ч вперёд${whole} на копии рудника и сравнён с «ничего не менять».`
      : `${name} готовит варианты и считает их прогнозы на ${horizon} ч${whole}…`;
  const card = el('article', { className: 'card advisor', dataset: { id: advisorId(), status }, 'aria-labelledby': 'advisor-title' },
    el('header', {},
      el('h3', { id: 'advisor-title' }, el('a', { className: 'twin-link', href: twinHref(advisorId()) }, `${name}: что делать`)),
      el('span', { className: 'badge' }, 'происшествие')),
    el('p', { className: 'adv-incident' }, incident.text),
    incident.thing ? el('p', { className: 'adv-where' }, 'Объект: ', twinLink(incident.thing),
      incident.since ? ` · с ${formatSimClock(incident.since)}` : '') : null,
    incident.end ? el('p', { className: 'adv-end', title: 'Прогнозы вариантов считают, что инцидент закончится в это время, если вариант его не меняет' },
      el('b', {}, /^sag-reline/.test(incident.key)
        ? `Пуск мельницы после замены футеровки ${formatSimDay(incident.end)}`
        : `Ожидаемое окончание ${formatSimClock(incident.end)}`),
      incident.basis ? el('span', { className: 'muted' }, ` · ${incident.basis}`) : null) : null,
    // The record ends with the raw messages sent; the card keeps the sentence, the tooltip all of it.
    appliedText ? el('p', { className: 'adv-result', title: appliedText },
      el('b', {}, `Применён вариант ${applied}${appliedAt ? ` в ${formatSimClock(appliedAt)}` : ''}: `),
      appliedSentence(appliedText, applied, options)) : null,
    el('p', { className: 'hint', role: 'status' }),
    el('p', { className: 'adv-lead' }, lead, ' ',
      status === 'ready' ? el('span', { className: 'adv-key' },
        el('span', { className: 'fc-key' }, keySample('a'), 'ничего не менять'), ' ',
        el('span', { className: 'fc-key' }, keySample('b'), 'вариант')) : null),
    options.length ? el('ol', { className: 'adv-options' },
      ...options.map((option) => optionRow(option, { recommended: ready && option.n === recommended, applied: option.n === applied, ready, reference, horizon }))) : null);
  const focused = document.activeElement?.closest?.('#advisor button.apply')?.dataset.option;
  holder.replaceChildren(card);
  if (focused) $(`button.apply[data-option="${focused}"]`, card)?.focus();
  const hint = $('.hint', card);
  setText(hint, hintText);
  hint.dataset.tone = hintTone;
}

// Call with null after the twins were (re)loaded, or with the set of changed IDs.
export function update(changed) {
  if (!changed) { key = ''; render(); return; }
  for (const id of changed) {
    if (id === advisorId() || /:forecast-[2-9]\d*$/.test(id)) { render(); return; }
  }
}
