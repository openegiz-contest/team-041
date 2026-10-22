// Right panel: the advisor, what-if scenarios, decision modules and the
// journal. Every card is built from the features of its twin, so a scenario or
// a module the model gains later appears without a change here.

import { sendMessage, loadJournal } from './api.js';
import { twins, byKind, first, feature, unitOf, shortName, titleOf, titleById, resolveRef, listRefs } from './store.js';
import { $, el, setText, twinHref, twinLink } from './dom.js';
import { stateText, unitText, featureLabel, formatSmart, formatFixed, formatSimClock, formatSimTime, formatDuration, percentChange,
  simMinutesBetween, formatDelta, formatTenge, formatMillions, formatNumber, moneyWords, SEVERITY_TEXT } from './labels.js';
import * as forecast from './forecast.js';
import * as advisor from './advisor.js';

// One line on what the scenario or the module does, for the known ones.
const NOTES = {
  'scenario-road-closure': 'Участок закрыт → самосвалы едут в объезд → рейс длиннее → подача руды падает.',
  'scenario-shovel-failure': 'Экскаватор выходит из строя → самосвалы уходят к другим → растут очереди.',
  'scenario-crusher-stop': 'Дробилка стоит → руда идёт на рудные склады → мельница срабатывает запас.',
  'scenario-heavy-rain': 'Дождь → скорость ниже на всех дорогах, крутой съезд закрыт → рейс и расход топлива растут.',
  'scenario-blast': 'Взрыв блока → опасная зона, техника отходит, ближние дороги закрыты до допуска.',
  'scenario-clay-face': 'Забой вскрыл глинистую руду → в питании ОФ-1 растёт глина → мельница и флотация теряют; руду можно везти на ОФ-2 или на склад глинистой руды.',
  'scenario-clay-plant-stop': 'Фабрика глинистых руд ОФ-2 стоит → приёмный склад заполняется → глинистая руда идёт на склад, медь ОФ-2 теряется.',
  'module-dispatch': 'Назначает самосвалу экскаватор, пункт разгрузки и маршрут. Выключен — самосвалы закреплены за экскаваторами.',
  'module-grade': 'Решает, куда везти руду блока: сульфидную на ОФ-1 по содержанию меди и глинистости, глинистую на ОФ-2 или на её склад. Выключен — руда идёт по блочной модели: сульфидная выше бортового содержания в дробилку, глинистая на ОФ-2, без управления смешиванием.',
  'module-plant': 'Задаёт подачу на мельницу по запасу склада и крепости руды. Выключен — подача постоянная.',
  'module-maint': 'Ставит технику на обслуживание до отказа. Выключен — ремонт только после поломки.',
  'module-advisor': 'При происшествии предлагает варианты действий, проверенные прогнозом на 12 часов. Выключен — вариантов нет.',
};

// Short names for the rows of the stack; the twin's full name is in its card.
const SHORT = {
  'scenario-heavy-rain': 'Сильный дождь',
  'module-dispatch': 'Диспетчеризация',
  'module-grade': 'Качество руды',
  'module-plant': 'Управление фабрикой',
  'module-maint': 'Техобслуживание',
  'module-advisor': 'Советник',
};
const rowTitle = (twin) => SHORT[shortName(twin.thingId)] ?? titleOf(twin).replace(/^Модуль[- ]/, '');

const CONFIRM_MS = 12000;
const num = (value) => (typeof value === 'number' ? formatSmart(value) : '—');

// Cards of the stack are compact rows; a row opens into its card. Which are
// open is kept across rebuilds; a scenario that starts opens by itself.
const expanded = new Set();

function expander(id, bodyId, title, ...after) {
  const button = el('button', { type: 'button', className: 'expand', 'aria-expanded': String(expanded.has(id)), 'aria-controls': bodyId, dataset: { expand: id } },
    el('span', { className: 'expand-title' }, title), ...after);
  button.addEventListener('click', () => setExpanded(id, !expanded.has(id)));
  return button;
}

export function setExpanded(id, open) {
  if (open) expanded.add(id); else expanded.delete(id);
  for (const card of document.querySelectorAll(`.card[data-id="${id}"]`)) {
    const button = $('button.expand', card);
    const body = $('.card-body', card);
    if (button) button.setAttribute('aria-expanded', String(open));
    if (body) body.hidden = !open;
  }
}

// Money in a unit of the model's (₸, млн тг, KZT/h): how many tenge one unit is, or 0 for other units.
function tengePerUnit(unit) {
  const text = String(unit ?? '');
  if (!/₸|тг|тенге|kzt/i.test(text)) return 0;
  return /млрд/.test(text) ? 1e9 : /млн/.test(text) ? 1e6 : 1;
}
const perHour = (unit) => (/\/\s*(ч|h)$/i.test(String(unit ?? '').trim()) ? '/ч' : '');

// A command was sent: say so until the twin answers, and say when it does not.
function pending(card, text) {
  const hint = $('.hint', card);
  clearTimeout(card.pendingTimer);
  setText(hint, text);
  hint.dataset.tone = 'wait';
  card.pendingTimer = setTimeout(() => {
    setText(hint, 'Подтверждения нет: состояние двойника не изменилось.');
    hint.dataset.tone = 'warn';
  }, CONFIRM_MS);
}

function settle(card, text = '', tone = '') {
  const hint = $('.hint', card);
  clearTimeout(card.pendingTimer);
  setText(hint, text);
  hint.dataset.tone = tone;
}

async function command(card, thingId, subject, body, text) {
  pending(card, text);
  try {
    await sendMessage(thingId, subject, body);
  } catch (error) {
    settle(card, `Команда не принята платформой: ${error.message}`, 'warn');
  }
}

// The button that opens a forecast panel in a card; shown only when the model
// has a forecast slot for the page.
function forecastButton(text, label) {
  return el('button', { type: 'button', className: 'fc-open', dataset: { action: 'forecast' }, 'aria-expanded': 'false', 'aria-label': label },
    el('span', { className: 'fc-icon', 'aria-hidden': 'true' }), text);
}

// -- scenarios ----------------------------------------------------------------

function chosenTarget(card) {
  const select = $('.target select:not(.cause):not(.clay)', card);
  return select ? select.value : $('.target', card)?.dataset.target || '';
}

// What a scenario may be started for (the crusher: a jam or a mechanical fault), each with its expected duration.
const causesOf = (twin) => [].concat(twin?.attributes?.spec?.causes ?? []).filter((cause) => cause && cause.id);
function chosenCause(card) {
  return $('select.cause', card)?.value || '';
}
// The body of `activate`: the target and, where the scenario has causes, the one chosen;
// the clay share of the face where the scenario takes one (the clay face).
function activateBody(card) {
  const target = chosenTarget(card);
  const cause = chosenCause(card);
  const clay = Number($('select.clay', card)?.value);
  const params = { ...(cause ? { cause } : {}), ...(clay > 0 ? { clay_pct: clay } : {}) };
  return { ...(target ? { target } : {}), ...(Object.keys(params).length ? { params } : {}) };
}

// The clay share a clay-face scenario may be started with (the model takes 15–40 %).
const CLAY_SHARES = [15, 20, 25, 32, 40];
const CLAY_DEFAULT = 32;

// «Ожидается N ч: why» before the start, for the cause chosen.
function showDuration(card, twin) {
  const holder = $('.duration', card);
  if (!holder) return;
  const cause = causesOf(twin).find((item) => item.id === chosenCause(card));
  const hours = cause ? cause.hours : feature(twin, 'expected_duration_h');
  const basis = cause ? cause.basis : String(feature(twin, 'duration_basis') ?? '');
  const text = typeof hours === 'number' && hours > 0 && basis ? `Ожидаемая длительность ${formatNumber(hours, 1)} ч: ${basis}.` : '';
  setText(holder, text);
  holder.hidden = !text;
}

function askScenarioForecast(card, button) {
  const id = card.dataset.id;
  const twin = twins.get(id);
  const title = titleOf(twin);
  const status = feature(twin, 'status') ?? 'inactive';
  if (status === 'inactive') {
    const target = chosenTarget(card);
    forecast.request({
      key: id, holder: $('.slots', card), button, subject: id, title: `сценарий «${title}»`,
      heading: `«${title}»: прогноз на ${forecast.HORIZON_H} ч`,
      subheading: target ? `Объект: ${titleById(target)}` : '',
      labelA: 'Без изменений', labelB: 'Если запустить сейчас',
      actionsB: [{ thing: id, subject: 'activate', body: activateBody(card) }],
      verdictLead: 'Если запустить сейчас',
    });
  } else {
    forecast.request({
      key: id, holder: $('.slots', card), button, subject: id, title: `сценарий «${title}»`,
      heading: `«${title}»: прогноз на ${forecast.HORIZON_H} ч`,
      subheading: `Сценарий идёт${resolveRef(feature(twin, 'target')) ? ` · ${titleById(resolveRef(feature(twin, 'target')))}` : ''}`,
      labelA: 'Сценарий продолжается', labelB: 'Если отменить сейчас',
      actionsB: [{ thing: id, subject: 'cancel', body: {} }],
      verdictLead: 'Если отменить сейчас',
    });
  }
}

// -- which targets a scenario can take now --------------------------------------

const BLOCK_REASONS = { blasted: 'уже взорван', mining: 'в отработке', depleted: 'отработан' };

// {ok, reason}: can the scenario start on this target now? The scenario's own
// list `valid_targets` decides when the model publishes it; otherwise the
// state of the target says what the model would refuse.
function targetValidity(scenario, targetId) {
  const target = twins.get(targetId);
  const kind = target?.attributes?.kind;
  const status = feature(target, 'status');
  const state = feature(target, 'state');
  let reason = '';
  if (kind === 'block') {
    if (BLOCK_REASONS[status]) reason = BLOCK_REASONS[status];
    else if (byKind('loader').some((loader) => resolveRef(feature(loader, 'block')) === targetId)) reason = 'в отработке';
  } else if (kind === 'loader' && state === 'down') reason = 'уже неисправен';
  else if (kind === 'road-segment' && status === 'closed') reason = 'уже закрыт';
  const published = feature(scenario, 'valid_targets');
  if (typeof published === 'string') {
    const valid = new Set(listRefs(scenario, 'valid_targets'));
    if (valid.has(targetId)) return { ok: true, reason: '' };
    return { ok: false, reason: reason || (status === 'insitu' ? 'не обурен' : 'сейчас нельзя') };
  }
  return { ok: !reason, reason };
}

// Why the scenario cannot start at all now, whatever its target, or ''.
function blockedReason(scenario) {
  const name = shortName(scenario.thingId);
  if (name === 'scenario-blast') {
    const blast = feature(first('pit'), 'blast_state');
    if (blast && blast !== 'idle') return 'идёт взрыв по графику: дождитесь допуска';
  }
  if (name === 'scenario-heavy-rain') {
    const weather = first('weather');
    if (feature(weather, 'condition') === 'rain') return 'дождь уже идёт';
    if (feature(weather, 'road_surface') === 'wet' || feature(weather, 'drying') === true) return 'дороги ещё не просохли';
  }
  return '';
}

// The default target: the first of the scenario's own list of valid targets
// (the model ranks it: the block whose blast shows the whole chain first),
// else its default when that is valid, else the first valid one by state
// (a charged block before a drilled one before one in situ).
const BLOCK_RANK = { charged: 0, drilled: 1, insitu: 2 };
function defaultTarget(scenario, targets) {
  const ranked = listRefs(scenario, 'valid_targets').filter((id) => targets.includes(id));
  if (ranked.length) return ranked[0];
  const fallback = [].concat(scenario.attributes?.links?.default_target ?? [])[0];
  if (fallback && targets.includes(fallback) && targetValidity(scenario, fallback).ok) return fallback;
  const valid = targets.filter((target) => targetValidity(scenario, target).ok);
  const rank = (id) => BLOCK_RANK[feature(twins.get(id), 'status')] ?? 9;
  valid.sort((a, b) => rank(a) - rank(b));
  return valid[0] ?? fallback ?? targets[0];
}

// Fill the target list: valid targets first, each invalid one with its reason and not selectable.
function fillTargets(card, scenario) {
  const select = $('.target select:not(.cause):not(.clay)', card);
  if (!select) return;
  const targets = JSON.parse(select.dataset.targets);
  const shown = targets.map((target) => ({ target, ...targetValidity(scenario, target) }));
  const key = shown.map((item) => `${item.target}:${item.reason}`).join('|');
  if (select.dataset.key === key) return;
  select.dataset.key = key;
  const kept = select.value;
  const option = (item) => el('option', { value: item.target, disabled: !item.ok },
    item.ok ? titleById(item.target) : `${titleById(item.target)} — ${item.reason}`);
  const good = shown.filter((item) => item.ok);
  const bad = shown.filter((item) => !item.ok);
  if (good.length && bad.length) {
    select.replaceChildren(el('optgroup', { label: 'Можно выбрать' }, ...good.map(option)),
      el('optgroup', { label: 'Сейчас нельзя' }, ...bad.map(option)));
  } else {
    select.replaceChildren(...shown.map(option));
  }
  // The visitor's own choice stays while it is valid; otherwise a valid default.
  const keep = select.dataset.chosen === 'true' && good.some((item) => item.target === kept);
  select.value = keep ? kept : defaultTarget(scenario, targets);
  if (!keep) select.dataset.chosen = 'false';
  showChoice(select);
}

// A long target name is cut in the narrow card: the whole of it is in the tooltip.
function showChoice(select) {
  const text = select.selectedOptions[0]?.textContent ?? '';
  if (select.title !== text) select.title = text;
}

function buildScenario(twin) {
  const id = twin.thingId;
  const title = titleOf(twin);
  const links = twin.attributes?.links ?? {};
  const targets = [].concat(links.targets ?? []).filter((target) => twins.has(target));
  const fallback = [].concat(links.default_target ?? [])[0];
  let chooser;
  if (targets.length > 1) {
    const select = el('select', { 'aria-label': `Объект сценария «${title}»`, dataset: { targets: JSON.stringify(targets), chosen: 'false' } });
    select.addEventListener('change', () => { select.dataset.chosen = 'true'; showChoice(select); });
    chooser = el('label', { className: 'target' }, select);
  } else {
    const only = targets[0] ?? fallback;
    chooser = el('span', { className: 'target target-fixed', title: only && twins.has(only) ? titleById(only) : '' }, only && twins.has(only) ? twinLink(only) : 'без объекта');
    chooser.dataset.target = only ?? '';
  }
  // A scenario with several causes (the crusher stop) offers them in place of its one fixed target.
  const causes = causesOf(twin);
  let cause = null;
  if (causes.length > 1) {
    cause = el('select', { className: 'cause', 'aria-label': `Причина: сценарий «${title}»` },
      ...causes.map((item) => el('option', { value: item.id }, `${item.title.replace(/^./, (c) => c.toUpperCase())}, ≈${formatNumber(item.hours, 1)} ч`)));
    cause.addEventListener('change', () => showDuration(card, twins.get(id)));
    if (targets.length <= 1) chooser = el('label', { className: 'target target-cause', dataset: { target: chooser.dataset.target ?? '' } }, cause);
  }
  // The clay face: how clayey the ore the face shows.
  const clay = shortName(id) === 'scenario-clay-face'
    ? el('select', { className: 'clay', 'aria-label': `Глинистость руды в забое: сценарий «${title}»` },
      ...CLAY_SHARES.map((value) => el('option', { value: String(value), selected: value === CLAY_DEFAULT }, `глина ${value} %`)))
    : null;
  const start = el('button', { type: 'button', className: 'start', dataset: { action: 'activate' }, 'aria-label': `Запустить сценарий «${title}»` }, 'Запустить');
  const cancel = el('button', { type: 'button', className: 'cancel', dataset: { action: 'cancel' }, 'aria-label': `Отменить сценарий «${title}»` }, 'Отменить');
  const ask = forecast.available()
    ? forecastButton(`Прогноз на ${forecast.HORIZON_H} ч`, `Прогноз на ${forecast.HORIZON_H} часов: сценарий «${title}»`) : null;
  const bodyId = `card-${shortName(id)}`;
  const card = el('article', { className: 'card scenario', dataset: { id } },
    el('div', { className: 'card-head' },
      expander(id, bodyId, rowTitle(twin), el('span', { className: 'badge' }, ''))),
    el('div', { className: 'card-body', id: bodyId, hidden: !expanded.has(id) },
      el('p', { className: 'note' }, NOTES[shortName(id)] ?? ''),
      el('div', { className: 'controls' }, chooser, start, cancel),
      cause && targets.length > 1 ? el('div', { className: 'controls' }, el('label', { className: 'target' }, cause)) : null,
      clay ? el('div', { className: 'controls' }, el('label', { className: 'target' }, clay)) : null,
      el('p', { className: 'duration', hidden: true }),
      el('p', { className: 'hint', role: 'status' }),
      el('div', { className: 'live', hidden: true },
        el('p', { className: 'elapsed' }),
        el('p', { className: 'expected', hidden: true }),
        el('table', { className: 'compare' },
          el('thead', {}, el('tr', {}, el('th', { scope: 'col' }, 'Цепочка'), el('th', { scope: 'col' }, 'До'),
            el('th', { scope: 'col' }, 'Сейчас'), el('th', { scope: 'col' }, 'Изменение'))),
          el('tbody', {})),
        el('p', { className: 'window', hidden: true }),
        el('p', { className: 'delay' }),
        el('p', { className: 'impact' }),
        el('p', { className: 'affected' })),
      ask ? el('div', { className: 'card-tools' }, ask) : null,
      // Panels of the card (the forecast); empty until something fills it.
      el('div', { className: 'slots' }),
      el('a', { className: 'twin-link card-link', href: twinHref(id) }, 'Карточка сценария')));
  start.addEventListener('click', () => {
    const target = chosenTarget(card);
    const scenario = twins.get(id);
    const blocked = blockedReason(scenario);
    const check = target && $('.target select:not(.cause):not(.clay)', card) ? targetValidity(scenario, target) : { ok: true };
    if (blocked || !check.ok) {
      settle(card, `Сейчас не запустить: ${blocked || `${titleById(target)} ${check.reason}`}.`, 'warn');
      return;
    }
    command(card, id, 'activate', activateBody(card), 'Запуск отправлен, ждём ответа модели…');
  });
  cancel.addEventListener('click', () => command(card, id, 'cancel', {}, 'Отмена отправлена, ждём ответа модели…'));
  ask?.addEventListener('click', () => askScenarioForecast(card, ask));
  return card;
}

// Which way a change is for the worse, from what the indicator is: times,
// distances, specific consumption, hardness and emissions are worse when they
// grow, flows, speeds, stocks, copper and money when they fall. Ore sent to
// the ROM pads is a response, neither better nor worse.
function worseWay(label, unit) {
  const name = label.toLowerCase();
  if (/рудные склады|взорванная/.test(name)) return '';
  if (unit === 'км' || unit === 'мин' || /л\/т|квт·ч\/т|co₂|co2|мм/i.test(unit)) return 'up';
  if (/крепост|бонд|очеред|ожидан|расход|выброс|p80|крупност|закрыт/.test(name)) return 'up';
  return 'down';
}

// Rows of the comparison: {caption, unit, before, now, worse, key, significant}.
// A scenario that publishes its chain (kpi1 … kpi4, in the order of cause and
// effect) shows only that chain; `kpiN_significant` false means the change is
// within the measured noise.
const NOISE_SHARE = 0.03;          // without a word from the model: a change under 3 % is noise
const LARGE_SHARE = 0.5;           // without a word from the model: a change by half or more is never noise
// «До» may be missing: no clean window before the start (the model says so
// with `kpiN_baseline_known` false, or in words). «Сейчас» may be the model's
// forecast rather than a measurement in the first minutes of a scenario.
// The names below are the ones the model may use; any of them may be absent.
const FORECAST_WORDS = /прогноз|forecast|predict|estimat/i;
function baselineState(f, n) {
  for (const name of [`kpi${n}_baseline_text`, `kpi${n}_baseline_note`, `kpi${n}_baseline_status`, 'kpi_baseline_note', 'kpi_baseline_text', 'baseline_text']) {
    const text = String(f(name) ?? '').trim();
    if (/недоступ|нет\s+(базы|чистого)|unavailable|missing|none/i.test(text)) return { missing: true, text: /[а-яё]/i.test(text) && text.length <= 24 ? text : 'база недоступна', note: /[а-яё]/i.test(text) ? text : '' };
  }
  const known = f(`kpi${n}_baseline_known`) ?? f(`kpi${n}_baseline_available`) ?? f('kpi_baseline_known') ?? f('baseline_available');
  if (known === false) return { missing: true, text: 'база недоступна', note: '' };
  return { missing: false };
}
function currentState(f, n, features) {
  const own = new RegExp(`^kpi${n}_(current|now)_?`);
  for (const name of features) {
    if (!own.test(name) && !/^kpi_(current|now)_|^current_(kind|source|basis|mode)$/.test(name)) continue;
    if (/_(label|unit)$/.test(name) && !/current_label$|now_label$/.test(name)) continue;
    const value = f(name);
    if (value === true && FORECAST_WORDS.test(name)) return { forecast: true, text: 'прогноз' };
    if (typeof value === 'string' && FORECAST_WORDS.test(value)) {
      const words = value.trim();
      return { forecast: true, text: /[а-яё]/i.test(words) && words.length <= 16 ? words : 'прогноз', note: /[а-яё]/i.test(words) && words.length > 16 ? words : '' };
    }
  }
  for (const name of [`kpi${n}_forecast`, `kpi${n}_is_forecast`, `kpi${n}_predicted`, 'kpi_forecast', 'kpi_is_forecast']) {
    if (f(name) === true) return { forecast: true, text: 'прогноз' };
  }
  return { forecast: false };
}
function compareRows(twin) {
  const f = (name) => feature(twin, name);
  const features = Object.keys(twin.features ?? {});
  const rows = [];
  for (let n = 1; n <= 6; n += 1) {
    const label = f(`kpi${n}_label`);
    if (!label) continue;
    const rawUnit = f(`kpi${n}_unit`);
    const money = tengePerUnit(rawUnit);
    const unit = money ? `млн ₸${perHour(rawUnit)}` : unitText(rawUnit) || String(rawUnit ?? '');
    const significant = f(`kpi${n}_significant`);
    const base = baselineState(f, n);
    const now = currentState(f, n, features);
    rows.push({ caption: String(label), unit, before: base.missing ? undefined : f(`kpi${n}_baseline`), now: f(`kpi${n}_current`),
      worse: worseWay(String(label), unit), key: `kpi${n}`, significant: typeof significant === 'boolean' ? significant : undefined,
      money: money || 0, missing: base.missing ? base.text : '', missingNote: base.note ?? '',
      forecast: now.forecast ? now.text : '', forecastNote: now.note ?? '' });
  }
  if (rows.length) return rows;
  if (f('baseline_feed_tph') !== undefined || f('current_feed_tph') !== undefined) {
    rows.push({ caption: 'Подача в дробилку', unit: 'т/ч', before: f('baseline_feed_tph'), now: f('current_feed_tph'), worse: 'down', key: 'feed' });
  }
  if (f('baseline_cycle_min') !== undefined || f('current_cycle_min') !== undefined) {
    rows.push({ caption: 'Время рейса', unit: 'мин', before: f('baseline_cycle_min'), now: f('current_cycle_min'), worse: 'up', key: 'cycle' });
  }
  if (f('baseline_margin_kzt_h') !== undefined || f('current_margin_kzt_h') !== undefined) {
    rows.push({ caption: 'Переменная маржа', unit: 'млн ₸/ч', before: f('baseline_margin_kzt_h'), now: f('current_margin_kzt_h'), worse: 'down', key: 'margin', money: 1 });
  }
  if (f('kpi_label') && (f('kpi_baseline') !== undefined || f('kpi_current') !== undefined)) {
    const money = tengePerUnit(f('kpi_unit'));
    const unit = money ? `млн ₸${perHour(f('kpi_unit'))}` : unitText(f('kpi_unit')) || String(f('kpi_unit') ?? '');
    rows.push({ caption: String(f('kpi_label')), unit, before: f('kpi_baseline'), now: f('kpi_current'), worse: worseWay(String(f('kpi_label')), unit), key: 'kpi', money });
  }
  return rows;
}

function updateScenario(twin) {
  const card = $(`.scenario[data-id="${twin.thingId}"]`);
  if (!card) return;
  const status = feature(twin, 'status') ?? 'inactive';
  const running = status !== 'inactive';
  const badge = $('.badge', card);
  if (card.dataset.status !== status) {
    // A scenario that runs shows itself: its card opens and scrolls into view.
    if (running && !expanded.has(twin.thingId) && (card.dataset.status || status === 'active')) setExpanded(twin.thingId, true);
    if (card.dataset.status && running) setTimeout(() => card.scrollIntoView({ block: 'nearest' }), 0);
    card.dataset.status = status;
    setText(badge, stateText(status));
    settle(card);
  }
  const target = resolveRef(feature(twin, 'target'));
  const select = $('.target select:not(.cause):not(.clay)', card);
  const causeSelect = $('select.cause', card);
  if (causeSelect) causeSelect.disabled = running;
  const claySelect = $('select.clay', card);
  if (claySelect) claySelect.disabled = running;
  if (select) {
    if (!running) fillTargets(card, twin);
    else if (target && select.value !== target) {
      // The running target may be one the list no longer offers (a blasted block).
      if (![...select.options].some((option) => option.value === target)) select.append(el('option', { value: target }, titleById(target)));
      [...select.options].forEach((option) => { if (option.value === target) option.disabled = false; });
      select.value = target;
      showChoice(select);
    }
    select.disabled = running;
  }
  const blocked = running ? '' : blockedReason(twin);
  const noTarget = select && !running && ![...select.options].some((option) => !option.disabled);
  $('[data-action="activate"]', card).disabled = running || Boolean(blocked) || noTarget;
  const why = blocked || (noTarget ? 'нет подходящего объекта' : '');
  const hint = $('.hint', card);
  if (why && hint.dataset.tone !== 'wait') { setText(hint, `Сейчас не запустить: ${why}.`); hint.dataset.tone = 'blocked'; }
  else if (!why && hint.dataset.tone === 'blocked') settle(card);
  $('[data-action="cancel"]', card).disabled = !running;
  const ask = $('[data-action="forecast"]', card);
  if (ask) ask.disabled = status === 'cancelling';
  $('.note', card).hidden = running || !$('.note', card).textContent;
  if (running) $('.duration', card).hidden = true;
  else showDuration(card, twin);

  const live = $('.live', card);
  live.hidden = !running;
  if (!running) return;
  showExpected(card, twin);

  let elapsed = feature(twin, 'elapsed_min');
  if (typeof elapsed !== 'number') elapsed = simMinutesBetween(feature(twin, 'started_sim_time'), feature(first('mine'), 'sim_time'));
  const since = feature(twin, 'started_sim_time');
  const parts = [status === 'cancelling' ? 'Отменяется' : typeof elapsed === 'number' ? `Длится ${formatDuration(elapsed)}` : 'Активен'];
  if (since) parts.push(`с ${formatSimClock(since)}`);
  if (target) parts.push(titleById(target));
  setText($('.elapsed', card), parts.join(' · '));

  const rows = compareRows(twin);
  const body = $('tbody', card);
  if ([...body.children].map((row) => row.dataset.row).join() !== rows.map((row) => row.key).join()) {
    body.replaceChildren(...rows.map((row, i) => el('tr', { dataset: { row: row.key } },
      el('th', { scope: 'row' }, i && rows.length > 1 && /^kpi\d/.test(row.key) ? el('span', { className: 'chain-arrow', 'aria-hidden': 'true' }, '→ ') : null, el('span', {})),
      el('td', { dataset: { cell: 'before' } }), el('td', { dataset: { cell: 'now' } }), el('td', { dataset: { cell: 'change' } }))));
  }
  rows.forEach(({ caption, unit, before, now, worse, significant, money, missing, missingNote, forecast: estimate, forecastNote }, i) => {
    const row = body.children[i];
    setText(row.children[0].lastElementChild, unit ? `${caption}, ${unit}` : caption);
    // Money is written in millions of tenge, like every sum on the page.
    const figure = (value) => (typeof value === 'number' && money ? formatMillions(value * money) : num(value));
    const beforeCell = row.children[1];
    if (missing) {
      if (beforeCell.dataset.state !== 'missing' || beforeCell.textContent !== missing) {
        beforeCell.replaceChildren(el('span', { className: 'cell-note' }, missing));
        beforeCell.dataset.state = 'missing';
      }
      beforeCell.title = missingNote || 'Нет спокойного окна такой же длины до начала сценария: сравнивать не с чем, изменение не считается';
    } else {
      if (beforeCell.dataset.state) { beforeCell.replaceChildren(); delete beforeCell.dataset.state; }
      setText(beforeCell, figure(before));
      beforeCell.title = '';
    }
    const nowCell = row.children[2];
    if (estimate) {
      const key = `${figure(now)}|${estimate}`;
      if (nowCell.dataset.key !== key) {
        nowCell.dataset.key = key;
        nowCell.replaceChildren(`≈${figure(now)}`, el('span', { className: 'cell-tag' }, estimate));
      }
      nowCell.dataset.state = 'forecast';
      nowCell.title = forecastNote || 'Прогноз модели: рейсов, начатых после запуска, закончено ещё мало для замера';
    } else {
      if (nowCell.dataset.state) { nowCell.replaceChildren(); delete nowCell.dataset.state; delete nowCell.dataset.key; nowCell.title = ''; }
      setText(nowCell, figure(now));
    }
    const both = typeof before === 'number' && typeof now === 'number' && !missing;
    // From zero a percentage says nothing: the change is written as it is.
    const change = both && !before && now ? formatDelta(now, Math.abs(now) >= 100 ? 0 : 1) : percentChange(before, now);
    // Inside the noise: the model says so (it measured the swing of the indicator over windows of this
    // length), or, without its word, the change is under 3 % and not by half or more. A change from or to
    // zero is never called noise.
    const zero = both && now !== before && !before !== !now;
    const large = both && now !== before && (Math.abs(now - before) >= LARGE_SHARE * Math.abs(before) || zero);
    const noise = both && !zero && (significant === false || (significant === undefined && !large && Math.abs(now - before) <= NOISE_SHARE * Math.abs(before || 1)));
    const cell = row.children[3];
    setText(cell, missing ? '—' : noise ? 'в пределах колебаний' : change || '—');
    cell.title = noise ? `Изменение ${change || '0 %'} не больше обычных колебаний этого показателя` : '';
    const direction = both ? Math.sign(now - before) : 0;
    cell.dataset.tone = noise ? 'noise' : !direction || !change || change === '0 %' ? '' : (worse === 'up' && direction > 0) || (worse === 'down' && direction < 0) ? 'worse'
      : worse ? 'better' : 'changed';
  });
  $('.compare', card).hidden = rows.length === 0;

  const windowMin = feature(twin, 'kpi_window_min');
  const notes = [];
  if (typeof windowMin === 'number' && windowMin > 0 && rows.some((row) => /^kpi\d/.test(row.key))) {
    notes.push(`«До» и «сейчас» — средние за равные окна по ${formatNumber(windowMin, 0)} мин модельного времени.`);
  }
  if (rows.some((row) => row.forecast)) notes.push('«Сейчас» со знаком ≈ — прогноз модели по новым маршрутам: замер заменит его, когда закончится достаточно рейсов, начатых после запуска.');
  // The model's own word on the «before» window: missing, or taken further back.
  const baseNote = String(feature(twin, 'kpi_baseline_note') ?? '').trim();
  if (rows.some((row) => row.missing)) notes.push('«База недоступна»: до запуска не было спокойного окна такой длины (модель работает недолго или шли другие сценарии), поэтому изменение не считается.');
  else if (baseNote) notes.push(`${baseNote.replace(/[а-яёa-z]/i, (c) => c.toUpperCase())}${/[.!]$/.test(baseNote) ? '' : '.'}`);
  const windowText = notes.join(' ');
  setText($('.window', card), windowText);
  $('.window', card).hidden = !windowText;
  const delay = String(feature(twin, 'effect_delay_text') ?? '').trim();
  setText($('.delay', card), delay);
  $('.delay', card).hidden = !delay;
  setText($('.impact', card), String(feature(twin, 'impact_text') ?? ''));

  const affected = listRefs(twin, 'affected').filter((id) => id !== twin.thingId);
  const holder = $('.affected', card);
  const key = affected.join(',');
  if (holder.dataset.key !== key) {
    holder.dataset.key = key;
    holder.replaceChildren(...(affected.length
      ? [el('span', { className: 'affected-title' }, `Затронуто объектов: ${affected.length}. `), ...affected.flatMap((id, i) => [i ? ', ' : '', twinLink(id)])]
      : []));
  }
  holder.hidden = affected.length === 0;
}

// While it runs: when it is expected to end and why, and what was done to end it sooner.
function showExpected(card, twin) {
  const holder = $('.expected', card);
  const end = String(feature(twin, 'expected_end_sim_time') ?? '');
  if (!end) { holder.hidden = true; return; }
  const left = simMinutesBetween(feature(first('mine'), 'sim_time'), end);
  const status = feature(twin, 'status');
  const lead = status === 'active'
    ? `Ожидаемое окончание ${formatSimClock(end)}${typeof left === 'number' && left > 0 ? `, через ${formatDuration(left)}` : ''}`
    : `Окончание было ожидаемо в ${formatSimClock(end)}`;
  const basis = String(feature(twin, 'duration_basis') ?? '').trim();
  const response = String(feature(twin, 'response_text') ?? '').trim();
  const cost = feature(twin, 'response_cost_kzt');
  const key = `${lead}|${basis}|${response}|${cost}`;
  if (holder.dataset.key !== key) {
    holder.dataset.key = key;
    holder.replaceChildren(...[el('b', {}, lead), basis ? el('span', { className: 'muted' }, ` · ${basis}`) : null,
      response ? el('span', { className: 'expected-response' }, ` Принято: ${response}${typeof cost === 'number' && cost > 0 ? `; ресурсы смены ${formatTenge(cost)}` : ''}.`) : null]
      .filter(Boolean));
    holder.title = 'Сценарий на стенде идёт, пока его не отменят; прогнозы считают, что инцидент закончится в ожидаемое время.';
  }
  holder.hidden = false;
}

function countScenarios() {
  const active = byKind('scenario').filter((twin) => (feature(twin, 'status') ?? 'inactive') !== 'inactive').length;
  const node = $('#scenario-count');
  setText(node, active ? `активно: ${active}` : `${byKind('scenario').length}`);
  node.dataset.tone = active ? 'scenario' : '';
}

// -- modules ------------------------------------------------------------------

const MODULE_BASE = new Set(['enabled', 'status', 'enabled_sim_time', 'decisions_total', 'last_decision', 'kpi_label', 'kpi_unit', 'kpi_value', 'effect_text',
  'effect_cu_t', 'effect_margin_kzt', 'effect_total_kzt', 'effect_basis', 'effect_forecast', 'effect_count', 'effect_sim_time']);

// What each known module shows besides its main indicator:
// [feature, caption, unit, decimals, extra(twin) -> small text under the value].
const pct = (value, digits) => `${formatFixed(value, digits)} %`;
const MODULE_FACTS = {
  'module-dispatch': [
    ['tonnes_per_h', 'Вывозка', 'т/ч', 0],
    ['ore_share_pct', 'Доля руды', '%', 1, (f) => (typeof f('plan_ore_share_pct') === 'number' ? `план ${pct(f('plan_ore_share_pct'), 1)}` : '')],
    ['reassigned_total', 'Переназначено', 'рейсов', 0],
    ['diverted_total', 'На рудный склад', 'рейсов', 0, () => 'вместо очереди'],
  ],
  'module-grade': [
    ['feed_grade_pct', 'Cu в питании', '%', 3, (f) => (typeof f('band_low_pct') === 'number' && typeof f('band_high_pct') === 'number'
      ? `полоса ${formatFixed(f('band_low_pct'), 2)}–${formatFixed(f('band_high_pct'), 2)}` : '')],
    ['blend_grade_pct', 'Cu в шихте', '%', 3, (f) => (typeof f('target_grade_pct') === 'number' ? `цель ${formatFixed(f('target_grade_pct'), 2)}` : '')],
    ['reclaim_tph', 'Со склада', 'т/ч', 0],
    ['blocks_classified', 'Блоков оценено', '', 0],
  ],
  'module-plant': [
    ['feed_setpoint_tph', 'Уставка', 'т/ч', 0, (f) => (typeof f('power_limit_tph') === 'number' ? `потолок сейчас ${formatFixed(f('power_limit_tph'), 0)}` : '')],
    ['sustained_tph', 'Устойчивый потолок', 'т/ч', 0, () => 'средняя 2024 г.'],
    ['peak_left_min', 'Пик в эту смену', 'мин', 0, (f) => (typeof f('peak_tph') === 'number' ? `осталось; до ${formatFixed(f('peak_tph'), 0)}` : 'осталось')],
    ['power_limit_tph', 'Предел по мощности', 'т/ч', 0, () => { const m = first('mill'); return m && typeof feature(m, 'max_feed_tph') === 'number' ? 'потолок — 97 %' : ''; }, (f) => { const m = first('mill'); return m ? feature(m, 'max_feed_tph') : undefined; }],
    ['ore_inflow_tph', 'Приток руды', 'т/ч', 0],
    ['hours_of_feed', 'Запас склада', 'ч', 1],
    ['predicted_recovery_pct', 'Извлечение', '%', 1, () => 'прогноз'],
    ['predicted_cu_tph', 'Медь', 'т/ч', 2, () => 'прогноз'],
    ['mill_stops', 'Остановок', '', 0, () => 'мельницы'],
  ],
  'module-maint': [
    ['defects_found', 'Дефектов', '', 0, () => 'найдено'],
    ['services_booked', 'ТО', '', 0, () => 'назначено'],
    ['breakdowns', 'Поломок', '', 0],
    ['units_watched', 'Наблюдает', 'машин', 0],
    ['units_at_risk', 'В зоне риска', 'links'],
  ],
  'module-advisor': [],
};

function askModuleForecast(card, button) {
  const id = card.dataset.id;
  const twin = twins.get(id);
  const on = feature(twin, 'enabled') === true;
  const set = (enabled) => [{ thing: id, subject: 'set-enabled', body: { enabled } }];
  forecast.request({
    key: id, holder: $('.slots', card), button, subject: id, title: `модуль «${titleOf(twin)}»`,
    heading: `«${titleOf(twin)}»: что даёт модуль за ${forecast.HORIZON_H} ч`,
    subheading: on ? 'Сейчас модуль включён' : 'Сейчас модуль выключен',
    labelA: 'С модулем', labelB: 'Без модуля',
    actionsA: on ? [] : set(true), actionsB: on ? set(false) : [],
    verdictLead: 'Без модуля',
  });
}

function buildModule(twin) {
  const id = twin.thingId;
  const title = titleOf(twin);
  const toggle = el('button', { type: 'button', className: 'switch', role: 'switch', 'aria-checked': 'false', dataset: { action: 'toggle' } },
    el('span', { className: 'track', 'aria-hidden': 'true' }, el('span', { className: 'thumb' })),
    el('span', { className: 'switch-text' }, 'Выключен'));
  const ask = forecast.available() && id !== advisor.advisorId()
    ? forecastButton('Что даёт модуль?', `Что даёт модуль «${title}»: прогноз на ${forecast.HORIZON_H} часов с ним и без него`) : null;
  const bodyId = `card-${shortName(id)}`;
  const card = el('article', { className: 'card module', dataset: { id } },
    el('div', { className: 'card-head' }, expander(id, bodyId, rowTitle(twin)), toggle),
    // What a switch answered shows also while the card is closed.
    el('p', { className: 'hint', role: 'status' }),
    el('div', { className: 'card-body', id: bodyId, hidden: !expanded.has(id) },
      el('p', { className: 'note' }, NOTES[shortName(id)] ?? ''),
      el('p', { className: 'kpi-line' }),
      el('dl', { className: 'facts' }),
      el('p', { className: 'meta' }),
      el('p', { className: 'decision' }),
      el('p', { className: 'effect', hidden: true }),
      el('p', { className: 'impact' }),
      ask ? el('div', { className: 'card-tools' }, ask) : null,
      el('div', { className: 'slots' }),
      el('a', { className: 'twin-link card-link', href: twinHref(id) }, 'Карточка модуля')));
  toggle.addEventListener('click', () => {
    const enable = !(feature(twins.get(id), 'enabled') === true);
    command(card, id, 'set-enabled', { enabled: enable }, enable ? 'Включение отправлено, ждём ответа модуля…' : 'Выключение отправлено, ждём ответа модуля…');
  });
  ask?.addEventListener('click', () => askModuleForecast(card, ask));
  return card;
}

// [caption, value text, small text, links] for the facts of one module.
function moduleFacts(twin) {
  const f = (name) => feature(twin, name);
  const known = MODULE_FACTS[shortName(twin.thingId)];
  const facts = [];
  if (known) {
    for (const [name, caption, unit, digits, extra, read] of known) {
      if (unit === 'links') {
        const ids = listRefs(twin, name);
        if (f(name) !== undefined) facts.push([caption, ids.length ? '' : 'нет', '', ids]);
        continue;
      }
      // A fact may read its value from another twin (the mill's power limit on the plant module's card).
      const value = read ? read(f) : f(name);
      if (typeof value !== 'number') continue;
      facts.push([caption, `${formatFixed(value, digits)}${unit ? ` ${unit}` : ''}`, extra ? extra(f) : '', null]);
    }
    return facts;
  }
  // A module this page does not know still shows the numbers it publishes.
  for (const name of Object.keys(twin.features ?? {})) {
    if (MODULE_BASE.has(name) || typeof f(name) !== 'number' || facts.length >= 4) continue;
    const unit = unitText(unitOf(twin, name));
    const money = tengePerUnit(unitOf(twin, name)) || (/_kzt(_h)?$/.test(name) ? 1 : 0);
    const text = money ? `${formatTenge(f(name) * money)}${perHour(unitOf(twin, name)) || (/_h$/.test(name) ? '/ч' : '')}`
      : `${num(f(name))}${unit ? ` ${unit}` : ''}`;
    facts.push([featureLabel(name, 'module'), text, '', null]);
  }
  return facts;
}

function updateModule(twin) {
  const card = $(`.module[data-id="${twin.thingId}"]`);
  if (!card) return;
  const f = (name) => feature(twin, name);
  const on = f('enabled') === true;
  const title = titleOf(twin);
  const toggle = $('.switch', card);
  if (toggle.getAttribute('aria-checked') !== String(on) || !toggle.hasAttribute('aria-label')) {
    if (card.dataset.enabled !== undefined && card.dataset.enabled !== String(on)) settle(card);
    card.dataset.enabled = String(on);
    toggle.setAttribute('aria-checked', String(on));
    toggle.setAttribute('aria-label', `${title}: ${on ? 'включён, выключить' : 'выключен, включить'}`);
    setText($('.switch-text', toggle), on ? 'Включён' : 'Выключен');
  }

  // The main indicator in one line, the module's own figures under it.
  const kpiLine = $('.kpi-line', card);
  if (f('kpi_label')) {
    const unit = unitText(f('kpi_unit')) || String(f('kpi_unit') ?? '');
    const money = tengePerUnit(f('kpi_unit'));
    const value = money && typeof f('kpi_value') === 'number' ? `${formatTenge(f('kpi_value') * money)}${perHour(f('kpi_unit'))}`
      : `${num(f('kpi_value'))}${unit ? ` ${unit}` : ''}`;
    if (kpiLine.dataset.key !== `${f('kpi_label')}|${value}`) {
      kpiLine.dataset.key = `${f('kpi_label')}|${value}`;
      kpiLine.replaceChildren(el('span', { className: 'kpi-caption' }, String(f('kpi_label'))), el('b', { className: 'kpi-figure' }, value));
    }
  }
  kpiLine.hidden = !f('kpi_label');

  const facts = moduleFacts(twin);
  const list = $('.facts', card);
  const shape = facts.map((fact) => `${fact[0]}|${fact[3] ? fact[3].join(',') : ''}`).join(';');
  if (list.dataset.shape !== shape) {
    list.dataset.shape = shape;
    list.replaceChildren(...facts.map(([, , , ids]) => el('div', {}, el('dt', {}),
      el('dd', {}, ids && ids.length ? el('span', { className: 'fact-links' }, ...ids.flatMap((id, i) => [i ? ', ' : '', twinLink(id)])) : el('span', { className: 'fact-value' }),
        el('small', {})))));
  }
  facts.forEach(([caption, value, small], i) => {
    const item = list.children[i];
    setText(item.firstElementChild, caption);
    const valueNode = $('.fact-value', item);
    if (valueNode) setText(valueNode, value);
    setText($('small', item), small);
  });
  list.hidden = facts.length === 0;

  const meta = [];
  if (f('status') !== undefined) meta.push(`сервис: ${stateText(f('status'))}`);
  if (f('decisions_total') !== undefined) meta.push(`решений: ${num(f('decisions_total'))}`);
  if (on && f('enabled_sim_time')) meta.push(`включён в ${formatSimClock(f('enabled_sim_time'))}`);
  setText($('.meta', card), meta.join(' · '));

  const decision = String(f('last_decision') ?? '');
  setText($('.decision', card), decision ? `Последнее решение: ${decision}` : '');
  $('.decision', card).hidden = !decision;
  const effect = String(f('effect_text') ?? '');
  setText($('.impact', card), effect);
  $('.impact', card).hidden = !effect;

  // The module's effect in copper and money, and how it was measured. No
  // figures before the first measurement (`effect_count` 0): zeros would read as «no effect».
  const figures = [];
  const measured = typeof f('effect_count') !== 'number' || f('effect_count') > 0;
  if (measured && typeof f('effect_cu_t') === 'number') figures.push(`${formatDelta(f('effect_cu_t'), 1)} т меди`);
  // The money figure is the decision figure, the total with the adjustment; the plain margin only where an
  // older module does not publish it.
  if (measured && typeof f('effect_total_kzt') === 'number') figures.push(`итог с поправкой ${formatTenge(f('effect_total_kzt'), { sign: true })}`);
  else if (measured && typeof f('effect_margin_kzt') === 'number') figures.push(`маржа ${formatTenge(f('effect_margin_kzt'), { sign: true })}`);
  const basis = String(f('effect_basis') ?? '').trim();
  const hours = /Прогноз на (\d+) ч/.exec(basis)?.[1];
  const count = f('effect_count');
  const holder = $('.effect', card);
  const key = `${figures.join('|')}|${basis}|${count}`;
  if (holder.dataset.key !== key) {
    holder.dataset.key = key;
    // The method in a few words; all of it in the tooltip.
    const how = typeof count === 'number' && count > 0
      ? ` · среднее по ${count} ${count === 1 ? 'прогнозу' : 'прогнозам'} «с модулем / без модуля»` : '';
    holder.title = moneyWords(basis);
    holder.replaceChildren(...(figures.length ? [el('b', {}, `Эффект${hours ? ` за ${hours} ч` : ''}: ${figures.join(', ')}`)] : []),
      how ? el('span', { className: 'effect-basis' }, how) : null);
  }
  holder.hidden = !figures.length;
}

function countModules() {
  const modules = byKind('module');
  const on = modules.filter((twin) => feature(twin, 'enabled') === true).length;
  setText($('#module-count'), `включено: ${on} из ${modules.length}`);
}

// -- journal ------------------------------------------------------------------

const JOURNAL_LIMIT = 150;
const seen = new Set();

// `old` events come from the history and go below what is already listed.
function addEvent(event, { old = false } = {}) {
  if (!event.text) return;
  const key = `${event.sim_time}|${event.text}`;
  if (seen.has(key)) return;
  const list = $('#journal');
  if (old && list.children.length >= JOURNAL_LIMIT) return;
  seen.add(key);
  const severity = SEVERITY_TEXT[event.severity] ? event.severity : 'info';
  const thing = resolveRef(event.thing);
  const item = el('li', { dataset: { severity } },
    el('span', { className: 'meta' },
      el('time', { title: formatSimTime(event.sim_time) }, formatSimClock(event.sim_time)),
      el('span', { className: 'severity' }, SEVERITY_TEXT[severity]),
      thing ? twinLink(thing) : null),
    el('span', { className: 'text' }, event.text));
  if (old) list.append(item);
  else list.prepend(item);
  while (list.children.length > JOURNAL_LIMIT) list.lastElementChild.remove();
  setText($('#journal-count'), String(list.children.length));
  updateTicker();

  // An event about a scenario or a module is the answer to a command sent from its card.
  if (old || !thing) return;
  for (const card of document.querySelectorAll(`.card[data-id="${thing}"]`)) {
    const hint = $('.hint', card);
    if (hint && hint.dataset.tone === 'wait' && !card.classList.contains('advisor')) settle(card, event.text, severity === 'info' ? '' : 'warn');
  }
  advisor.journalEvent(event, thing);
}

// The line at the bottom of the page: the latest event of the journal.
function updateTicker() {
  const latest = $('#journal li');
  const toggle = $('#journal-toggle');
  if (!latest || !toggle) return;
  setText($('#ticker-time'), $('time', latest)?.textContent ?? '');
  const thing = $('.twin-link', latest)?.textContent;
  const text = $('.text', latest)?.textContent ?? '';
  setText($('#ticker-text'), thing && !text.includes(thing) ? `${thing}: ${text}` : text);
  toggle.dataset.severity = latest.dataset.severity ?? 'info';
  toggle.title = `${text} — открыть журнал событий`;
}

function journalEvent(twin) {
  return { severity: feature(twin, 'severity'), thing: feature(twin, 'thing'), text: feature(twin, 'text'), sim_time: feature(twin, 'sim_time') };
}

// The journal twin carries one event per update, so each update is taken as
// it arrives, not with the batched changes.
export function journal(twin) {
  addEvent(journalEvent(twin));
}

// Events from before the page was opened come from the history database.
async function backfill() {
  let events = [];
  try {
    events = await loadJournal();
  } catch {
    return;                         // no history yet: the journal fills as events arrive
  }
  for (const event of events.reverse()) addEvent(event, { old: true });
}

// -- wiring -------------------------------------------------------------------

// The groups of the stack fold away to their heading.
export function init() {
  for (const button of document.querySelectorAll('.group-head .collapse')) {
    button.addEventListener('click', () => {
      const open = button.getAttribute('aria-expanded') !== 'true';
      button.setAttribute('aria-expanded', String(open));
      $(`#${button.getAttribute('aria-controls')}`).hidden = !open;
    });
  }
}

// In the order the mine twin lists them; any twin it does not list follows.
function ordered(kind, relation) {
  const listed = [].concat(first('mine')?.attributes?.links?.[relation] ?? []);
  const rank = (twin) => (listed.includes(twin.thingId) ? listed.indexOf(twin.thingId) : listed.length);
  return [...byKind(kind)].sort((a, b) => rank(a) - rank(b));
}

function buildAll() {
  const scenarios = ordered('scenario', 'scenarios');
  $('#scenarios').replaceChildren(...(scenarios.length ? scenarios.map(buildScenario)
    : [el('p', { className: 'muted pad' }, 'В модели нет сценариев.')]));
  scenarios.forEach(updateScenario);
  const modules = ordered('module', 'modules');
  $('#modules').replaceChildren(...(modules.length ? modules.map(buildModule)
    : [el('p', { className: 'muted pad' }, 'В модели нет модулей.')]));
  modules.forEach(updateModule);
  // An open forecast panel moves into the rebuilt card of its scenario or module.
  forecast.forget((key) => document.querySelector(`#control .card[data-id="${key}"] .slots`));
  countScenarios();
  countModules();
  advisor.update(null);
  byKind('journal').forEach(journal);
}

// Call with null after the twins were (re)loaded, or with the set of changed IDs.
export function update(changed) {
  if (!changed) {
    buildAll();
    backfill();
    return;
  }
  let counts = false;
  let targetsMoved = false;
  for (const id of changed) {
    const twin = twins.get(id);
    const kind = twin?.attributes?.kind;
    if (kind === 'scenario') { updateScenario(twin); counts = true; }
    else if (kind === 'module') { updateModule(twin); counts = true; }
    else if (kind === 'mine') {
      // The clock moved: scenarios that do not publish their own duration get it from the clock.
      for (const scenario of byKind('scenario')) {
        if ((feature(scenario, 'status') ?? 'inactive') !== 'inactive' && typeof feature(scenario, 'elapsed_min') !== 'number') updateScenario(scenario);
      }
    } else if (['block', 'loader', 'road-segment', 'pit', 'weather'].includes(kind)) targetsMoved = true;
  }
  // What a scenario may be started on depends on its targets' state.
  if (targetsMoved) {
    for (const scenario of byKind('scenario')) {
      if ((feature(scenario, 'status') ?? 'inactive') === 'inactive' && !changed.has(scenario.thingId)) updateScenario(scenario);
    }
  }
  if (counts) { countScenarios(); countModules(); }
  advisor.update(changed);
}
