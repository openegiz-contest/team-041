// «О модели»: what the twin represents. The text is written in index.html;
// the list «В модели сейчас» is filled from the twins, so it always says what
// the running model holds.

import { twins, byKind, first, feature, kindOf } from './store.js';
import { $, el } from './dom.js';
import { formatNumber } from './labels.js';

const plural = (n, one, few, many) => {
  const tens = n % 100;
  const units = n % 10;
  if (tens >= 11 && tens <= 14) return many;
  if (units === 1) return one;
  if (units >= 2 && units <= 4) return few;
  return many;
};

// The most common value of a spec key among twins of a kind.
function commonSpec(kind, key) {
  const counts = new Map();
  for (const twin of byKind(kind)) {
    const value = twin.attributes?.spec?.[key];
    if (value !== undefined && value !== null && value !== '') counts.set(value, (counts.get(value) ?? 0) + 1);
  }
  let best = null;
  for (const [value, count] of counts) if (!best || count > best[1]) best = [value, count];
  return best ? best[0] : null;
}

function roadLength(roadClass) {
  let metres = 0;
  for (const segment of byKind('road-segment')) {
    const spec = segment.attributes?.spec ?? {};
    if (roadClass && spec.road_class !== roadClass) continue;
    if (typeof spec.length_m === 'number') metres += spec.length_m;
  }
  return metres;
}

function lines() {
  const out = [];
  const trucks = byKind('truck').length;
  if (trucks) {
    const model = commonSpec('truck', 'model');
    const payload = commonSpec('truck', 'payload_t');
    out.push(`${trucks} ${plural(trucks, 'самосвал', 'самосвала', 'самосвалов')}${model ? ` ${model}` : ''}${typeof payload === 'number' ? `, ${formatNumber(payload, 0)} т` : ''}`);
  }
  const loaders = byKind('loader').length;
  if (loaders) {
    const model = commonSpec('loader', 'model');
    const bucket = commonSpec('loader', 'bucket_m3');
    out.push(`${loaders} ${plural(loaders, 'экскаватор', 'экскаватора', 'экскаваторов')}${model ? ` ${model}` : ''}${typeof bucket === 'number' ? `, ковш ${formatNumber(bucket, 0)} м³` : ''}`);
  }
  const rigs = byKind('drill-rig').length;
  if (rigs) {
    const model = commonSpec('drill-rig', 'model');
    out.push(`${rigs} ${plural(rigs, 'буровой станок', 'буровых станка', 'буровых станков')}${model ? ` ${model}` : ''}`);
  }
  const pit = first('pit');
  const spec = pit?.attributes?.spec ?? {};
  const benches = byKind('bench').length;
  if (pit) {
    const parts = [];
    if (typeof spec.rim_elevation_m === 'number' && typeof spec.floor_elevation_m === 'number') {
      parts.push(`глубина ${formatNumber(spec.rim_elevation_m - spec.floor_elevation_m, 0)} м (борт ${formatNumber(spec.rim_elevation_m, 0)} м, дно ${formatNumber(spec.floor_elevation_m, 0)} м)`);
    }
    if (typeof spec.rim_length_m === 'number' && typeof spec.rim_width_m === 'number') {
      parts.push(`по бровке ${formatNumber(spec.rim_length_m, 0)} × ${formatNumber(spec.rim_width_m, 0)} м`);
    }
    if (typeof spec.final_wall_angle_deg === 'number') parts.push(`угол борта ${formatNumber(spec.final_wall_angle_deg, 0)}°`);
    if (benches) {
      // Benches that say whether they are worked: how many are.
      const flagged = byKind('bench').filter((bench) => typeof bench.attributes?.spec?.working === 'boolean');
      const working = flagged.filter((bench) => bench.attributes.spec.working).length;
      const height = typeof spec.bench_height_m === 'number' ? ` по ${formatNumber(spec.bench_height_m, 0)} м` : '';
      parts.push(`${benches} ${plural(benches, 'уступ', 'уступа', 'уступов')}${height}${flagged.length ? `, из них рабочих ${working}` : ''}`);
    }
    const blocks = byKind('block').length;
    if (blocks) parts.push(`${blocks} ${plural(blocks, 'взрывной блок', 'взрывных блока', 'взрывных блоков')}`);
    if (parts.length) out.push(`Карьер: ${parts.join(', ')}`);
  }
  const roads = roadLength();
  if (roads > 0) {
    const ramps = roadLength('ramp');
    out.push(`Дороги: ${formatNumber(roads / 1000, 1)} км${ramps > 0 ? `, из них съезды в карьере ${formatNumber(ramps / 1000, 1)} км` : ''}`);
  }
  const design = first('mine')?.attributes?.spec?.mill_design_tph;
  if (typeof design === 'number') out.push(`Фабрика: расчётная подача на мельницу ${formatNumber(design, 0)} т/ч`);
  const physical = [...twins.values()].filter((twin) => !['scenario', 'module', 'forecast', 'journal'].includes(kindOf(twin))).length;
  out.push(`Двойников объектов рудника: ${physical}; ещё ${twins.size - physical} — сценарии, модули, прогнозы и журнал`);
  const speedup = feature(first('mine'), 'speedup');
  if (typeof speedup === 'number') out.push(`Модельное время идёт в ${formatNumber(speedup, 0)} раз быстрее настоящего`);
  return out;
}

// What the mine twin says about itself (attributes.about), when the model gives it.
const ABOUT_FIELDS = [['object', 'Объект'], ['slice', 'Что в модели'], ['data_basis', 'На чём основана'], ['disclaimer', 'Оговорка']];

export function refresh() {
  const list = $('#about-live');
  if (!list || !twins.size) return;
  list.replaceChildren(...lines().map((text) => el('li', {}, text)));
  const passport = $('#about-passport');
  const about = first('mine')?.attributes?.about;
  if (passport) {
    const rows = about && typeof about === 'object' ? ABOUT_FIELDS.filter(([key]) => typeof about[key] === 'string' && about[key]) : [];
    passport.hidden = !rows.length;
    $('#about-passport-list').replaceChildren(...rows.map(([key, title]) => el('li', {}, el('strong', {}, `${title}: `), about[key])));
  }
}

export function init() {
  const dialog = $('#about');
  $('#about-open').addEventListener('click', () => { refresh(); dialog.showModal(); });
  $('#about-close').addEventListener('click', () => dialog.close());
  dialog.addEventListener('click', (event) => { if (event.target === dialog) dialog.close(); });
}
