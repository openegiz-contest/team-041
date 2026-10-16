// Top of the page: the simulated clock, shift and weather, the speed control
// and the connection state; under it the ribbon of the six figures the shift
// supervisor watches, each of which opens its detail: what it is, the process
// chain from the faces to the flotation, and the rest of the shift's figures.

import { sendMessage } from './api.js';
import { first, feature, byKind, cycleNow } from './store.js';
import { $, $$, el, setText, twinHref } from './dom.js';
import { stateText, formatFixed, formatMillions, formatNumber, formatSimClock, formatSimDate } from './labels.js';
import { rate, RATE_NOTE } from './rates.js';
import { millTwin } from './plant.js';

const mine = (name) => () => feature(first('mine'), name);
const millFeed = () => {
  const mill = millTwin();
  return mill && typeof feature(mill, 'feed_tph') === 'number' ? rate(mill, 'feed_tph') : rate(first('mine'), 'mill_feed_tph');
};
const num = (value, digits = 0) => (typeof value === 'number' ? formatNumber(value, digits) : '—');
// When the current shift began, as a clock time: the figures "за смену" count from it.
const shiftStart = () => { const t = feature(first('mine'), 'shift_start_sim_time'); return t ? `с ${formatSimClock(t)}` : 'с 08:00 или 20:00'; };

// The six figures of the ribbon: [key, label, unit, digits, read, sub, title, twin, tone].
// Digits 'M' mean millions of tenge.
const MAIN = [
  { key: 'mill', label: 'Подача на мельницу', unit: 'т/ч', digits: 0, read: millFeed,
    sub: () => { const d = first('mine')?.attributes?.spec?.mill_design_tph; return typeof d === 'number' ? `без модуля ${num(d)}` : ''; },
    title: `Подача руды на мельницу, ${RATE_NOTE}. Это то, что фабрика перерабатывает сейчас: если она падает, медь за смену падает вместе с ней. Это сульфидная фабрика ОФ-1; фабрика глинистых руд ОФ-2 — в подробностях ниже. Без модуля управления фабрикой уставка постоянна (проектная подача ОФ-1); модуль держит её у предела мельницы по мощности и глинистости руды, когда склад полон.`,
    twin: () => millTwin() },
  { key: 'stock', label: 'Запас склада', unit: 'ч', digits: 1, read: () => feature(first('stockpile'), 'hours_of_feed'),
    sub: () => { const s = first('stockpile'); return s ? `склад ${num(feature(s, 'level_pct'))} %` : ''; },
    title: 'На сколько часов хватит дроблёной руды на складе при нынешней подаче на мельницу, если карьер перестанет её подавать: запас склада, делённый на уставку питания. Это запас времени, который есть у смены, чтобы справиться со сбоем; если модуль управления фабрикой снизит подачу, руды хватит дольше.',
    twin: () => first('stockpile'),
    tone: (v) => (typeof v !== 'number' ? '' : v < 2 ? 'bad' : v < 4 ? 'warn' : '') },
  { key: 'copper', label: 'Медь в концентрате', unit: 'т/ч', digits: 1, read: () => rate(first('mine'), 'cu_tph'),
    sub: () => { const r = feature(first('mine'), 'recovery_pct'); return typeof r === 'number' ? `извлечение ${formatFixed(r, 1)} %` : ''; },
    title: `Медь, которая уходит в концентрат обеих фабрик, тонн в час, ${RATE_NOTE}: переработка × содержание меди × извлечение на ОФ-1 и на ОФ-2.`,
    twin: () => first('flotation') },
  { key: 'trucks', label: 'Самосвалы в работе', unit: () => `из ${byKind('truck').length}`, digits: 0,
    read: () => feature(first('mine'), 'trucks_active') ?? byKind('truck').filter((t) => !['down', 'service', 'standby', 'refuel'].includes(feature(t, 'state'))).length,
    sub: () => { const c = cycleNow(); return typeof c.value === 'number' ? `рейс ${c.forecast ? '≈' : ''}${formatFixed(c.value, 1)} мин` : ''; },
    title: 'Самосвалы, которые возят горную массу сейчас: остальные на заправке, обслуживании, в ремонте или в простое. Рядом — среднее время рейса.',
    twin: () => null,
    tone: (v) => { const n = byKind('truck').length; return typeof v !== 'number' || !n ? '' : v < n * 0.6 ? 'bad' : v < n * 0.75 ? 'warn' : ''; } },
  { key: 'margin', label: 'Маржа', unit: 'млн ₸/ч', digits: 'M', read: () => rate(first('mine'), 'margin_kzt_h'),
    sub: () => 'переменная',
    title: `Переменная маржа, миллионов тенге в час, ${RATE_NOTE}: выручка за оплачиваемую медь в концентрате за вычетом платы медеплавильному заводу, минус дизельное топливо и электроэнергия. Зарплата, ремонты, взрывчатка, реагенты, амортизация и другие постоянные затраты не учтены.`,
    twin: () => first('mine') },
  { key: 'co2', label: 'Выбросы CO₂', unit: 'т/ч', digits: 1, read: () => rate(first('mine'), 'co2_t_h'),
    sub: () => { const d = feature(first('mine'), 'diesel_l_per_t'); return typeof d === 'number' ? `дизель ${formatFixed(d, 2)} л/т` : ''; },
    title: `Выбросы CO₂, тонн в час, ${RATE_NOTE}: дизельное топливо техники и электроэнергия из сети.`,
    twin: () => first('mine') },
];

// The rest of the shift's figures, in the detail of every figure of the ribbon.
const MORE = [
  ['crusher', 'Дробилка', 'т/ч', 0, () => rate(first('crusher'), 'throughput_tph'), `Производительность дробилки, ${RATE_NOTE}: дробилка работает рывками, пока самосвалы разгружаются`],
  ['grade', 'Содержание Cu', '%', 2, mine('head_grade_pct'), 'Содержание меди в руде обеих фабрик'],
  ['recovery', 'Извлечение', '%', 1, mine('recovery_pct'), 'Извлечение меди в концентрат на обеих фабриках'],
  ['ofs1', 'ОФ-1: Cu в питании', '%', 2, mine('ofs1_head_grade_pct'), () => `Сульфидная фабрика ОФ-1: содержание меди в питании ${num(feature(first('mine'), 'ofs1_head_grade_pct'), 2)} %, извлечение ${num(feature(first('mine'), 'ofs1_recovery_pct'), 1)} %, глинистость питания ${num(feature(first('mine'), 'ofs1_clay_pct'), 1)} %`],
  ['ofs1rec', 'ОФ-1: извлечение', '%', 1, mine('ofs1_recovery_pct'), 'Извлечение меди на сульфидной фабрике ОФ-1'],
  ['ofs1clay', 'Глина в питании ОФ-1', '%', 1, mine('ofs1_clay_pct'), 'Глинистость руды в питании ОФ-1: глина снижает производительность мельницы и извлечение флотации'],
  ['ofs2', 'ОФ-2: переработка', 'т/ч', 0, mine('ofs2_feed_tph'), () => `Фабрика глинистых руд ОФ-2: переработка ${num(feature(first('mine'), 'ofs2_feed_tph'))} т/ч, содержание меди ${num(feature(first('mine'), 'ofs2_head_grade_pct'), 2)} %, извлечение ${num(feature(first('mine'), 'ofs2_recovery_pct'), 1)} %, медь ${num(feature(first('mine'), 'ofs2_cu_tph'), 1)} т/ч`],
  ['ofs2rec', 'ОФ-2: извлечение', '%', 1, mine('ofs2_recovery_pct'), 'Извлечение меди на фабрике глинистых руд ОФ-2 (обесшламливание, песковая и шламовая флотация)'],
  ['clay', 'Глинистая руда', '%', 1, mine('clay_share_pct'), () => `Доля глинистой руды в добытой руде; на складе глинистой руды ${num(feature(first('mine'), 'clay_stock_t'))} т`],
  ['cycle', 'Рейс', 'мин', 1, () => cycleNow().value, 'Среднее время рейса самосвала; пока идёт сценарий, который меряет рейс, — то же число, что в его карточке'],
  ['queue', 'Очереди', 'мин', 1, mine('avg_queue_min'), 'Среднее ожидание в очередях за рейс'],
  ['fleet', 'Парк в работе', '%', 0, mine('fleet_util_pct'), 'Доля времени, когда самосвалы заняты работой'],
  ['cu', 'Медь за смену', 'т', 1, mine('cu_shift_t'), () => `Медь в концентрате с начала смены (${shiftStart()}); с запуска модели — ${num(feature(first('mine'), 'cu_produced_t'), 0)} т`],
  ['ore', 'Руда за смену', 'т', 0, mine('ore_shift_t'), () => `Добыто руды (сульфидной, глинистой и забалансовой) с начала смены (${shiftStart()}); с запуска модели — ${num(feature(first('mine'), 'ore_mined_t'), 0)} т`],
  ['milled', 'Переработано за смену', 'т', 0, mine('milled_shift_t'), () => `Руда, переработанная обеими фабриками с начала смены (${shiftStart()})`],
  ['strip', 'Вскрыша', 'т/т', 2, mine('strip_ratio'), 'Тонн вскрыши на тонну добытой руды, считая забалансовую. Проектный коэффициент 0,7 считает иначе: вскрыша и забалансовая руда на тонну руды для фабрик'],
  ['diesel', 'Дизель', 'л/т', 2, mine('diesel_l_per_t'), 'Расход дизельного топлива на тонну горной массы'],
  ['energy', 'Энергия', 'кВт·ч/т', 1, mine('energy_kwh_per_t'), 'Расход электроэнергии на тонну руды'],
];

const unitOf = (item) => (typeof item.unit === 'function' ? item.unit() : item.unit);
const show = (value, digits) => (digits === 'M' ? formatMillions(value, { digits: 1 }) : formatFixed(value, digits));

let openKey = null;
let hooks = { reveal: () => {}, openList: () => {}, layout: () => {} };

function send(subject, body) {
  const twin = first('mine');
  if (twin) sendMessage(twin.thingId, subject, body).catch(() => setLinkState(false));
}

function openDetail(key) {
  openKey = key === openKey ? null : key;
  for (const tile of $$('.kpi')) tile.setAttribute('aria-expanded', String(tile.dataset.key === openKey));
  const panel = $('#kpi-detail');
  panel.hidden = !openKey;
  if (openKey) {
    document.dispatchEvent(new CustomEvent('mine:popover', { detail: 'kpi' }));
    renderDetail();
  }
  hooks.layout();
}

export function closeDetail() {
  if (openKey) openDetail(openKey);
}

function renderDetail() {
  const item = MAIN.find((entry) => entry.key === openKey);
  if (!item) return;
  setText($('#kpi-detail-title'), `${item.label}: ${show(item.read(), item.digits)} ${unitOf(item)}`);
  setText($('#kpi-detail-text'), item.title);
  const twin = item.twin();
  const actions = [];
  if (twin) actions.push(el('button', { type: 'button', onclick: () => { hooks.reveal(twin.thingId); } }, `Показать на сцене: ${shortName(twin)}`));
  if (item.key === 'trucks') actions.push(el('button', { type: 'button', onclick: () => hooks.openList('Самосвал') }, 'Список самосвалов'));
  if (twin) actions.push(el('a', { className: 'twin-link', href: twinHref(twin.thingId), style: { alignSelf: 'center', fontSize: '12px' } }, 'Карточка объекта'));
  $('#kpi-detail-actions').replaceChildren(...actions);
}

const shortName = (twin) => {
  const kind = twin?.attributes?.kind;
  return { mill: 'измельчение', 'sag-mill': 'МПСИ', stockpile: 'склад руды', flotation: 'флотация', mine: 'весь рудник' }[kind] ?? (twin?.attributes?.name ?? '');
};

export function init(options = {}) {
  hooks = { ...hooks, ...options };
  $('#kpis').replaceChildren(...MAIN.map((item) => el('button', { type: 'button', className: 'kpi', title: item.title, 'aria-expanded': 'false', 'aria-controls': 'kpi-detail',
    dataset: { key: item.key }, onclick: () => openDetail(item.key) },
  el('span', { className: 'kpi-label' }, item.label),
  el('span', { className: 'kpi-main' }, el('span', { className: 'kpi-value', dataset: { kpi: item.key } }, '—'), el('span', { className: 'kpi-unit' }, '')),
  el('span', { className: 'kpi-sub' }, ''))));
  $('#kpi-more').replaceChildren(...MORE.map(([key, caption, unit, , , title]) => el('div', { title: typeof title === 'function' ? '' : title, dataset: { more: key } },
    el('dt', {}, caption),
    el('dd', {}, el('span', { className: 'kpi-value', dataset: { kpi: key } }, '—'), el('small', {}, unit)))));
  $('#kpi-detail-close').addEventListener('click', () => closeDetail());
  for (const button of $$('[data-speedup]')) {
    button.addEventListener('click', () => send('set-speed', { speedup: Number(button.dataset.speedup) }));
  }
  $('#pause-toggle').addEventListener('click', () => {
    send(feature(first('mine'), 'sim_state') === 'paused' ? 'resume' : 'pause', {});
  });
  document.addEventListener('mine:popover', (event) => { if (event.detail !== 'kpi') closeDetail(); });
}

export function update() {
  for (const item of MAIN) {
    const value = item.read();
    const tile = $(`.kpi[data-key="${item.key}"]`);
    setText($('.kpi-value', tile), show(value, item.digits));
    setText($('.kpi-unit', tile), unitOf(item));
    setText($('.kpi-sub', tile), item.sub?.() ?? '');
    const tone = item.tone ? item.tone(value) : '';
    if (tile.dataset.tone !== tone) tile.dataset.tone = tone;
  }
  for (const [key, , , digits, read, title] of MORE) {
    const value = read();
    const node = $(`[data-kpi="${key}"]`);
    setText(node, formatFixed(value, digits));
    const box = node.closest('div');
    if (box.hidden !== (value === undefined)) box.hidden = value === undefined;
    if (typeof title === 'function') box.title = title();
  }
  if (openKey) renderDetail();
  const twin = first('mine');
  if (!twin) return;
  const paused = feature(twin, 'sim_state') === 'paused';
  const time = feature(twin, 'sim_time');
  setText($('#sim-clock'), `${formatSimClock(time)}`);
  $('#sim-clock').title = `Модельное время: ${formatSimDate(time)} ${formatSimClock(time)}`;
  setText($('#sim-shift'), paused ? 'пауза' : stateText(feature(twin, 'shift')));
  $('#sim-shift').dataset.paused = String(paused);
  for (const button of $$('[data-speedup]')) {
    button.setAttribute('aria-pressed', String(Number(button.dataset.speedup) === feature(twin, 'speedup')));
  }
  const toggle = $('#pause-toggle');
  setText(toggle, paused ? 'Продолжить' : 'Пауза');
  toggle.setAttribute('aria-pressed', String(paused));
  const weather = first('weather');
  const link = $('#weather');
  if (weather && link) {
    const condition = feature(weather, 'condition');
    const parts = [stateText(condition)];
    if (condition !== 'dry' && typeof feature(weather, 'rain_mm_h') === 'number') parts.push(`${formatNumber(feature(weather, 'rain_mm_h'), 1)} мм/ч`);
    if (typeof feature(weather, 'temperature_c') === 'number') parts.push(`${formatNumber(feature(weather, 'temperature_c'), 0)} °C`);
    setText(link, parts.join(' · '));
    link.title = `Погода: ${parts.join(', ')}`;
    link.dataset.tone = condition && condition !== 'dry' ? 'wet' : '';
    link.setAttribute('href', twinHref(weather.thingId));
  }
}

// 'ok' while changes arrive, 'down' while the platform cannot be reached.
export function setLinkState(online) {
  const node = $('#link-state');
  node.dataset.state = online ? 'ok' : 'down';
  setText(node, online ? 'Данные идут' : 'Нет связи, повтор…');
  node.title = online ? 'Изменения двойников приходят с платформы' : 'Платформа недоступна, страница подключается снова';
  node.setAttribute('aria-label', online ? 'Связь с платформой: данные идут' : 'Связь с платформой потеряна, страница подключается снова');
}
