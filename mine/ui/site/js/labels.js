// Russian names for everything that comes from the twins: kinds, features,
// units, states. A name this page does not know is shown as it arrives, so a
// feature added to the model later still appears.

// Order of the groups in the object list; kinds not named here follow in alphabetical order.
export const KIND_ORDER = ['mine', 'truck', 'loader', 'drill-rig', 'crusher', 'conveyor', 'stockpile', 'mill',
  'sag-mill', 'ball-mill', 'flotation', 'clay-plant', 'desliming', 'clay-flotation', 'clay-stockpile', 'concentrate', 'tailings', 'rom-pad', 'waste-dump', 'substation', 'fuel-station', 'workshop', 'weather', 'pit', 'bench',
  'block', 'road-segment', 'road-node', 'scenario', 'module', 'forecast', 'journal'];

// [group title in the list, name of one object]
const KINDS = {
  mine: ['Рудник', 'Рудник'],
  pit: ['Карьер', 'Карьер'],
  bench: ['Уступы', 'Уступ'],
  block: ['Блоки', 'Взрывной блок'],
  'drill-rig': ['Буровые станки', 'Буровой станок'],
  loader: ['Экскаваторы', 'Экскаватор'],
  truck: ['Самосвалы', 'Самосвал'],
  crusher: ['Дробилка', 'Дробилка'],
  conveyor: ['Конвейер', 'Конвейер'],
  stockpile: ['Склад дроблёной руды', 'Склад дроблёной руды'],
  'rom-pad': ['Рудные склады', 'Рудный склад'],
  'waste-dump': ['Отвал', 'Отвал'],
  mill: ['Мельница', 'Мельница'],
  'sag-mill': ['Мельница полусамоизмельчения', 'Мельница полусамоизмельчения'],
  'ball-mill': ['Шаровые мельницы', 'Шаровые мельницы'],
  flotation: ['Флотация', 'Флотация'],
  'clay-plant': ['ОФ-2: фабрика глинистых руд', 'Фабрика глинистых руд ОФ-2'],
  desliming: ['Обесшламливание ОФ-2', 'Обесшламливание ОФ-2'],
  'clay-flotation': ['Флотация ОФ-2', 'Флотация ОФ-2'],
  'clay-stockpile': ['Склад глинистой руды', 'Склад глинистой руды'],
  concentrate: ['Концентрат', 'Склад концентрата'],
  tailings: ['Хвостохранилище', 'Хвостохранилище'],
  substation: ['Подстанция', 'Подстанция'],
  'fuel-station': ['Заправочная станция', 'Заправочная станция'],
  workshop: ['Мастерская', 'Мастерская'],
  weather: ['Погода', 'Погода'],
  module: ['Модули', 'Модуль'],
  'road-node': ['Узлы дорог', 'Узел дорог'],
  'road-segment': ['Участки дорог', 'Участок дороги'],
  scenario: ['Сценарии', 'Сценарий «что если»'],
  forecast: ['Прогнозы', 'Прогноз на 12 часов'],
  journal: ['Журнал', 'Журнал событий'],
};
export const kindTitle = (kind) => KINDS[kind]?.[0] ?? kind;
export const kindName = (kind) => KINDS[kind]?.[1] ?? kind;

const STATES = {
  // trucks
  queue_load: 'очередь на погрузку',
  approach: 'подъезд под погрузку',
  loading: 'погрузка',
  hauling: 'везёт груз',
  queue_dump: 'очередь на разгрузку',
  dumping: 'разгрузка',
  returning: 'едет порожним',
  refuel: 'заправка',
  service: 'обслуживание',
  to_fuel: 'едет на заправку',
  to_service: 'едет на ТО',
  down: 'неисправен',
  standby: 'простой',
  // loaders, drill rigs and the plant
  idle: 'простой',
  waiting: 'ждёт самосвал',
  relocating: 'переезд',
  setup: 'подготовка забоя',
  drilling: 'бурение',
  tramming: 'перегон',
  positioning: 'переход к скважине',
  running: 'работает',
  blocked: 'склад полон',
  starved: 'нет руды',
  stopped: 'остановлена',
  starting: 'запуск',
  overload: 'перегрузка',
  paused: 'пауза',
  // roads and scenarios
  open: 'открыт',
  closed: 'закрыт',
  restricted: 'ограничение',
  inactive: 'не активен',
  active: 'активен',
  cancelling: 'отменяется',
  // blocks
  insitu: 'в массиве',
  drilled: 'обурен',
  charged: 'заряжен',
  blasted: 'взорван',
  mining: 'в отработке',
  depleted: 'отработан',
  // blasts
  clearing: 'оцепление',
  reentry: 'ожидание допуска',
  // modules
  offline: 'сервис не запущен',
  online: 'работает',
  disabled: 'выключен',
  enabled: 'включён',
  // forecasts
  ready: 'готов',
  // weather and road surface
  dry: 'сухо',
  wet: 'мокро',
  rain: 'дождь',
  // shifts
  day: 'дневная смена',
  night: 'ночная смена',
};
export const stateText = (value) => STATES[value] ?? String(value ?? '');

// Features whose value is one of the words above.
const STATE_FEATURES = new Set(['state', 'status', 'sim_state', 'shift', 'condition', 'blast_state', 'surface', 'road_surface']);

// Words specific to one feature.
const ENUMS = {
  material: { ore: 'сульфидная руда', clay: 'глинистая руда', 'low-grade': 'забалансовая руда', waste: 'вскрыша', none: 'нет' },
  role: { 'high-grade': 'богатая руда', 'low-grade': 'забалансовая руда', page: 'прогнозы страницы', advisor: 'прогнозы советника', module: 'замер эффекта модуля' },
  setpoint_source: { constant: 'постоянная уставка', module: 'модуль управления фабрикой',
    'module-plant': 'модуль управления фабрикой', 'plant-control': 'модуль управления фабрикой', operator: 'указание оператора' },
  assignment_source: { static: 'закрепление за экскаватором', module: 'модуль диспетчеризации',
    dispatch: 'модуль диспетчеризации', 'module-dispatch': 'модуль диспетчеризации', scenario: 'сценарий', operator: 'указание оператора' },
  destination_source: { cutoff: 'бортовое содержание', module: 'модуль качества руды',
    'module-grade': 'модуль качества руды', 'grade-control': 'модуль качества руды', operator: 'указание оператора' },
  blast_source: { plan: 'по плану буровзрывных работ', scenario: 'сценарий', operator: 'указание оператора' },
  severity: { info: 'сообщение', warning: 'внимание', alarm: 'авария' },
  road_class: { ramp: 'съезд', bench: 'дорога уступа', surface: 'дорога на поверхности' },
  reclaim_source: { 'grade-control': 'модуль качества руды', 'module-grade': 'модуль качества руды', rule: 'постоянное правило', operator: 'указание оператора' },
  analysis_status: { idle: 'нет происшествия', running: 'считаются прогнозы', ready: 'варианты готовы' },
  service_kind: { planned: 'плановое', scheduled: 'плановое', predictive: 'по прогнозу состояния', condition: 'по прогнозу состояния',
    defect: 'по найденному дефекту', repair: 'ремонт после поломки', breakdown: 'ремонт после поломки', unplanned: 'внеплановое' },
  precip_class: { none: 'нет', light: 'слабые', moderate: 'умеренные', heavy: 'сильные', rain: 'дождь', snow: 'снег', sleet: 'мокрый снег', drizzle: 'морось' },
  ramp: { east: 'восточный', west: 'западный', main: 'главный' },
  status: { error: 'ошибка' },
};

export const MATERIAL_TEXT = ENUMS.material;
export const SEVERITY_TEXT = ENUMS.severity;

const FEATURES = {
  // mine
  sim_time: 'Модельное время',
  sim_state: 'Состояние модели',
  speedup: 'Ускорение времени',
  shift: 'Смена',
  ore_mined_t: 'Добыто руды с запуска модели',
  waste_mined_t: 'Вывезено вскрыши с запуска модели',
  cu_shift_t: 'Медь в концентрате за смену',
  ore_shift_t: 'Добыто руды за смену',
  waste_shift_t: 'Вывезено вскрыши за смену',
  milled_shift_t: 'Переработано руды за смену',
  shift_start_sim_time: 'Смена началась',
  strip_ratio: 'Вскрыша на тонну руды (с забалансовой)',
  mill_feed_tph: 'Подача на мельницу',
  head_grade_pct: 'Содержание Cu в питании',
  recovery_pct: 'Извлечение меди',
  cu_produced_t: 'Медь в концентрате с запуска модели',
  avg_cycle_min: 'Средний рейс',
  avg_queue_min: 'Ожидание в очередях за рейс',
  fleet_util_pct: 'Использование парка',
  fleet_avail_pct: 'Готовность парка самосвалов за последние 24 ч',
  trucks_active: 'Самосвалов в работе',
  diesel_l_per_t: 'Расход дизеля',
  energy_kwh_per_t: 'Расход электроэнергии',
  revenue_kzt_h: 'Выручка за оплачиваемую медь',
  diesel_cost_kzt_h: 'Затраты на дизельное топливо',
  power_cost_kzt_h: 'Затраты на электроэнергию',
  margin_kzt_h: 'Переменная маржа',
  co2_t_h: 'Выбросы CO₂',
  // both plants: ОФ-1 (sulphide ore) and ОФ-2 (clay ore)
  processed_tph: 'Переработка обеих фабрик',
  ofs1_head_grade_pct: 'Содержание Cu в питании ОФ-1',
  ofs1_recovery_pct: 'Извлечение меди на ОФ-1',
  ofs1_clay_pct: 'Глинистость питания ОФ-1',
  ofs2_feed_tph: 'Переработка ОФ-2',
  ofs2_head_grade_pct: 'Содержание Cu в питании ОФ-2',
  ofs2_recovery_pct: 'Извлечение меди на ОФ-2',
  ofs2_cu_tph: 'Медь в концентрате ОФ-2',
  clay_mined_t: 'Добыто глинистой руды с запуска модели',
  clay_share_pct: 'Доля глинистой руды в добыче',
  clay_stock_t: 'На складе глинистой руды',
  clay_pct: 'Глинистость',
  feed_clay_pct: 'Глинистость питания',
  clay_factor_pct: 'Снижение производительности от глины',
  clay_loss_pct: 'Потери извлечения от глины',
  bin_t: 'Приёмный склад',
  bin_pct: 'Заполнение приёмного склада',
  bin_hours: 'Запас приёмного склада',
  room_t: 'Свободно',
  slimes_pct: 'Доля шламов',
  slimes_tph: 'Шламы',
  sands_tph: 'Пески',
  slimes_cu_pct: 'Доля меди в шламах',
  sand_recovery_pct: 'Извлечение из песков',
  slime_recovery_pct: 'Извлечение из шламов',
  blend_clay_pct: 'Глинистость шихты',
  clay_limit_pct: 'Предел глинистости для ОФ-1',
  clay_blocks: 'Глинистых блоков в работе',
  cancel_remaining_min: 'До отмены осталось',
  replicas: 'Повторов прогноза',
  delta_total_se_kzt: 'Разброс разницы итога',
  delta_margin_se_kzt: 'Разброс разницы маржи',
  delta_cu_se_t: 'Разброс разницы меди',
  delta_total_replicas_kzt: 'Разница итога по повторам',
  delta_cu_replicas_t: 'Разница меди по повторам',
  // pit, benches, blocks
  active_faces: 'Рабочих забоев',
  depth_m: 'Глубина',
  broken_stock_t: 'Запас взорванной массы',
  blast_state: 'Взрывные работы',
  blast_block: 'Взрываемый блок',
  blast_radius_m: 'Радиус опасной зоны',
  next_blast_sim_time: 'Следующий взрыв',
  elevation_m: 'Отметка',
  blocks_remaining: 'Блоков осталось',
  tonnes_remaining: 'Остаток',
  pushback: 'Этап отработки',
  status: 'Состояние',
  state: 'Состояние',
  grade_pct: 'Содержание Cu',
  hardness_bwi: 'Крепость (индекс Бонда)',
  material: 'Материал',
  frag_p80_mm: 'Крупность после взрыва, P80',
  powder_factor_kg_m3: 'Удельный расход ВВ',
  destination: 'Куда везти',
  destination_source: 'Кто назначил',
  destination_reason: 'Почему туда',
  // drill rigs and loaders
  block: 'Блок',
  holes_done: 'Пробурено скважин',
  holes_planned: 'Скважин по проекту',
  penetration_m_h: 'Скорость бурения',
  metres_drilled: 'Пробурено в текущем блоке (с начала его бурения)',
  pos_x: 'Координата X',
  pos_y: 'Координата Y',
  pos_z: 'Высотная отметка',
  truck: 'Самосвал под погрузкой',
  passes: 'Ковшей в погрузке',
  dig_rate_tph: 'Производительность',
  queue_len: 'Очередь',
  power_kw: 'Мощность',
  health_pct: 'Техническое состояние',
  hours_since_service: 'Наработка после ТО',
  service_reason: 'Причина обслуживания',
  ready_in_min: 'Освободится через',
  // trucks
  payload_t: 'Груз',
  speed_kmh: 'Скорость',
  heading_deg: 'Курс',
  segment: 'Участок дороги',
  assigned_loader: 'Экскаватор',
  assigned_dump: 'Пункт разгрузки',
  route: 'Маршрут',
  assignment_source: 'Кто назначил рейс',
  fuel_pct: 'Топливо',
  fuel_rate_lph: 'Расход топлива',
  engine_load_pct: 'Нагрузка двигателя',
  engine_temp_c: 'Температура двигателя',
  tyre_temp_c: 'Температура шин',
  trips: 'Рейсов сделано',
  cycle_min: 'Последний рейс',
  // roads
  trucks_waiting: 'Самосвалов ждёт',
  closed_by: 'Кем закрыт',
  speed_limit_kmh: 'Ограничение скорости',
  rolling_resistance_pct: 'Сопротивление качению',
  trucks_on: 'Самосвалов на участке',
  surface: 'Покрытие',
  speed_cap_kmh: 'Временное ограничение скорости',
  // plant
  throughput_tph: 'Производительность',
  capacity_tph: 'Паспортная производительность',
  feed_grade_pct: 'Содержание Cu в питании',
  feed_p80_mm: 'Крупность питания, P80',
  hopper_t: 'Руда в бункере',
  restart_expected_sim_time: 'Пуск ожидается в',
  repair_crews: 'Бригад на ремонте',
  repair_end_sim_time: 'Ремонт закончится в',
  tonnes_total: 'Всего с запуска модели',
  load_tph: 'Грузопоток',
  load_t: 'Руда на ленте',
  level_t: 'Запас',
  level_pct: 'Заполнение',
  hours_of_feed: 'Запас хода мельницы',
  role: 'Назначение склада',
  reclaim_tph: 'Отгрузка в дробилку',
  reclaim_source: 'Кто задаёт отгрузку',
  load_pct: 'Загрузка',
  drive_load_pct: 'Загрузка привода',
  charge_pct: 'Заполнение барабана',
  installed_mw: 'Установленная мощность',
  overload_t: 'Перегруз: руда сверх измельчённой',
  overload_pct: 'Перегруз',
  tails_tph: 'Хвосты',
  inflow_tph: 'Поступление',
  produced_t: 'Получено',
  shipped_t: 'Отгружено',
  shipments_total: 'Отгрузок',
  last_shipment_sim_time: 'Последняя отгрузка',
  stored_t: 'Уложено',
  stored_m3: 'Уложено по объёму',
  cu_lost_t: 'Медь, ушедшая в хвосты',
  cu_t: 'Медь в концентрате на складе',
  kpi_window_min: 'Окно сравнения «до» и «сейчас»',
  stops_total: 'Остановок с запуска модели',
  feed_tph: 'Подача',
  feed_setpoint_tph: 'Уставка подачи',
  setpoint_source: 'Источник уставки',
  max_feed_tph: 'Предел мельницы по мощности',
  power_mw: 'Мощность',
  specific_energy_kwh_t: 'Удельный расход энергии',
  feed_bwi: 'Крепость питания',
  conc_grade_pct: 'Содержание Cu в концентрате',
  conc_tph: 'Выход концентрата',
  cu_tph: 'Медь в концентрате',
  tails_grade_pct: 'Содержание Cu в хвостах',
  cu_total_t: 'Медь с запуска модели',
  load_mw: 'Нагрузка',
  plant_mw: 'Нагрузка фабрики',
  pit_mw: 'Нагрузка карьера',
  energy_mwh_shift: 'Энергия за смену',
  trucks_fuelling: 'На заправке',
  litres_issued: 'Выдано топлива',
  bays_busy: 'Занято боксов',
  bays_total: 'Всего боксов',
  jobs_queued: 'Ждут ремонта',
  jobs_done: 'Выполнено работ',
  breakdowns_total: 'Поломок с запуска модели',
  bays_lent: 'Бригад мастерской в забое',
  overtime_until: 'Сверхурочная смена до',
  response_cost_kzt: 'Ресурсы смены на инциденты',
  // weather
  condition: 'Погода',
  rain_mm_h: 'Интенсивность дождя',
  visibility_m: 'Видимость',
  temperature_c: 'Температура воздуха',
  road_surface: 'Дороги',
  // modules
  enabled: 'Включён',
  decisions_total: 'Принято решений',
  last_decision: 'Последнее решение',
  kpi_label: 'Показатель',
  kpi_unit: 'Единица показателя',
  kpi_value: 'Значение показателя',
  effect_text: 'Эффект',
  effect_cu_t: 'Эффект: медь',
  effect_margin_kzt: 'Эффект: маржа',
  effect_total_kzt: 'Эффект: итог с поправкой',
  effect_basis: 'Как измерен эффект',
  effect_forecast: 'Прогноз для замера эффекта',
  effect_count: 'Замеров эффекта',
  effect_sim_time: 'Последний замер эффекта',
  valid_targets: 'Можно запустить на',
  effect_delay_text: 'Когда виден результат',
  expected_end_sim_time: 'Ожидаемое окончание',
  expected_duration_h: 'Ожидаемая длительность',
  expected_remaining_h: 'До ожидаемого окончания',
  duration_basis: 'Почему столько',
  response_text: 'Что сделано, чтобы закончить раньше',
  incident_end_sim_time: 'Ожидаемое окончание инцидента',
  incident_basis: 'Почему столько',
  assumption_text: 'Когда прогноз считает инцидент законченным',
  enabled_sim_time: 'Включён в',
  tonnes_per_h: 'Вывозка горной массы',
  ore_share_pct: 'Доля руды в вывозке',
  plan_ore_share_pct: 'Доля руды по плану',
  reassigned_total: 'Переназначено рейсов',
  diverted_total: 'Рейсов на рудный склад вместо очереди',
  target_grade_pct: 'Целевое содержание Cu',
  band_low_pct: 'Нижняя граница полосы',
  band_high_pct: 'Верхняя граница полосы',
  in_band_pct: 'Время в полосе',
  blend_grade_pct: 'Содержание Cu в шихте',
  blocks_classified: 'Блоков классифицировано',
  power_limit_tph: 'Потолок уставки модуля',
  ore_inflow_tph: 'Приток руды на склад',
  predicted_recovery_pct: 'Прогноз извлечения',
  predicted_cu_tph: 'Прогноз выпуска меди',
  mill_stops: 'Остановок мельницы',
  defects_found: 'Найдено дефектов',
  services_booked: 'Назначено ТО',
  breakdowns: 'Поломок',
  units_watched: 'Машин под наблюдением',
  units_at_risk: 'Машины в зоне риска',
  service_hold_until: 'ТО отложено оператором до',
  setpoint_hold_until: 'Уставка оператора действует до',
  reclaim_hold_until: 'Указание оператора по отгрузке до',
  destination_hold_until: 'Указание оператора по блоку до',
  blast_source: 'Кто назначил взрыв',
  incident: 'Происшествие',
  incident_thing: 'Объект происшествия',
  incident_text: 'Что случилось',
  incident_sim_time: 'Происшествие с',
  analysis_status: 'Разбор вариантов',
  analysis_sim_time: 'Разбор закончен',
  options_count: 'Вариантов',
  recommended: 'Рекомендуемый вариант',
  applied_option: 'Применённый вариант',
  applied_label: 'Что применено',
  applied_sim_time: 'Применено в',
  applied_text: 'Запись о применении',
  cu_tph: 'Медь в концентрате',
  diesel_l_h: 'Расход дизельного топлива',
  energy_mwh_h: 'Расход электроэнергии',
  request_id: 'Номер запроса',
  fork_sim_time: 'Копия рудника снята',
  ready_sim_time: 'Прогноз готов',
  compute_s: 'Время счёта',
  error_text: 'Ошибка',
  actions_a: 'Действия варианта А',
  actions_b: 'Действия варианта Б',
  delta_cu_t: 'Разница меди Б − А',
  delta_co2_t: 'Разница CO₂ Б − А',
  // forecasts
  requested_by: 'Кто запросил',
  subject: 'Предмет прогноза',
  horizon_h: 'Горизонт',
  label_a: 'Вариант А',
  label_b: 'Вариант Б',
  a_cu_t: 'Медь, вариант А',
  b_cu_t: 'Медь, вариант Б',
  a_ore_t: 'Руда, вариант А',
  b_ore_t: 'Руда, вариант Б',
  a_margin_kzt: 'Маржа, вариант А',
  b_margin_kzt: 'Маржа, вариант Б',
  delta_margin_kzt: 'Разница маржи Б − А',
  a_co2_t: 'CO₂, вариант А',
  b_co2_t: 'CO₂, вариант Б',
  a_series_mill_tph: 'Подача на мельницу по часам, А',
  b_series_mill_tph: 'Подача на мельницу по часам, Б',
  a_series_stockpile_pct: 'Склад руды по часам, А',
  b_series_stockpile_pct: 'Склад руды по часам, Б',
  summary_text: 'Прогноз подробно',
  // scenarios
  target: 'Объект сценария',
  started_sim_time: 'Запущен',
  elapsed_min: 'Длится',
  baseline_feed_tph: 'Подача в дробилку до сценария',
  current_feed_tph: 'Подача в дробилку сейчас',
  baseline_cycle_min: 'Рейс до сценария',
  current_cycle_min: 'Рейс сейчас',
  kpi_baseline: 'Показатель до сценария',
  kpi_current: 'Показатель сейчас',
  affected: 'Затронутые объекты',
  impact_text: 'Последствия',
  baseline_margin_kzt_h: 'Маржа до сценария',
  current_margin_kzt_h: 'Маржа сейчас',
  throughput_now_tph: 'Производительность сейчас',
  kpi_baseline_note: 'Окно «до»',
  metres_total: 'Пробурено станком с запуска модели',
  service_kind: 'Вид обслуживания',
  standby_reason: 'Причина простоя',
  precip_class: 'Осадки',
  normal_mean_c: 'Норма: средняя температура',
  normal_max_c: 'Норма: средний максимум',
  normal_min_c: 'Норма: средний минимум',
  wet_day_chance_pct: 'Вероятность дня с осадками',
  diesel_l_per_engine_h: 'Расход дизеля на моточас',
  rehandle_t_h: 'Перевалка руды со складов',
  rehandle_cost_kzt_h: 'Затраты на перевалку руды',
  refused_actions: 'Отклонённые действия',
  applied_rank: 'Место применённого варианта',
  delta_adjust_kzt: 'Разница поправки Б − А',
  delta_total_kzt: 'Разница итога с поправкой Б − А',
  verdict_text: 'Итог прогноза',
  copper_text: 'Откуда разница меди',
  // journal
  seq: 'Номер события',
  severity: 'Важность',
  thing: 'Объект',
  text: 'Текст',
};

// The same feature name means different things on some kinds.
const FEATURES_BY_KIND = {
  'road-segment': { grade_pct: 'Уклон', status: 'Проезд' },
  truck: { grade_pct: 'Содержание Cu в грузе', block: 'Блок' },
  loader: { material: 'Материал в забое' },
  stockpile: { hardness_bwi: 'Крепость руды', clay_pct: 'Глинистость руды' },
  'rom-pad': { hardness_bwi: 'Крепость руды', clay_pct: 'Глинистость руды' },
  'clay-stockpile': { reclaim_tph: 'Отгрузка на ОФ-2', level_t: 'На складе', level_pct: 'Заполнение', clay_pct: 'Глинистость руды', grade_pct: 'Содержание Cu' },
  'clay-plant': { reclaim_tph: 'Подача погрузчиком со склада глинистой руды', feed_tph: 'Переработка', recovery_pct: 'Извлечение меди', cu_tph: 'Медь в концентрате', conc_tph: 'Концентрат' },
  'clay-flotation': { recovery_pct: 'Извлечение меди' },
  desliming: { feed_tph: 'Питание' },
  mine: { recovery_pct: 'Извлечение меди' },
  flotation: { recovery_pct: 'Извлечение меди' },
  crusher: { tonnes_total: 'Переработано' },
  mill: { tonnes_total: 'Переработано', load_pct: 'Перегруз: руда сверх измельчённой', power_limit_tph: 'Предел производительности по мощности' },
  concentrate: { level_t: 'На складе', level_pct: 'Заполнение склада', grade_pct: 'Содержание Cu' },
  tailings: { grade_pct: 'Содержание Cu в хвостах' },
  module: { hours_of_feed: 'Запас склада' },
  'waste-dump': { tonnes_total: 'Уложено' },
};

// Families of features: the branch figures of a forecast and the options of the advisor.
const BRANCH = {
  cu_t: 'Медь в концентрате', ore_t: 'Руда добыта', milled_t: 'Руда переработана', diesel_l: 'Дизельное топливо',
  energy_mwh: 'Электроэнергия', revenue_kzt: 'Выручка за медь', diesel_kzt: 'Затраты на дизель', power_kzt: 'Затраты на электроэнергию',
  stock_kzt: 'Изменение стоимости запаса руды', margin_kzt: 'Переменная маржа', co2_t: 'Выбросы CO₂',
  waste_t: 'Вскрыша вывезена', strip_kzt: 'Стоимость вскрытой руды',
  series_mill_tph: 'Подача на мельницу по часам', series_stockpile_pct: 'Склад руды по часам',
  rehandle_t: 'Перевалка руды со складов', rehandle_kzt: 'Затраты на перевалку', adjust_kzt: 'Поправка: запасы руды и вскрыша',
  total_kzt: 'Маржа с поправкой', resources_kzt: 'Ресурсы смены', queue_min: 'Ожидание в очередях', in_band_pct: 'Время в полосе содержания Cu',
  avail_pct: 'Готовность техники', breakdowns: 'Поломок', mill_stops: 'Остановок мельницы',
  milled_cu_t: 'Медь в переработанной руде ОФ-1', from_stock_t: 'Руда со склада дроблёной руды', from_stock_cu_t: 'Медь в руде со склада',
  from_pads_t: 'Руда с рудных складов', from_pads_cu_t: 'Медь в руде с рудных складов', from_pit_t: 'Руда из карьера', from_pit_cu_t: 'Медь в руде из карьера',
  clay_milled_t: 'Руда переработана на ОФ-2', clay_milled_cu_t: 'Медь в руде ОФ-2', clay_cu_t: 'Медь в концентрате ОФ-2',
  to_clay_stock_t: 'Глинистая руда на склад', ofs1_clay_pct: 'Глинистость питания ОФ-1', stock_end_h: 'Запас склада в конце',
  grade_kzt: 'Стоимость содержания запасов руды', work_kzt: 'Работа парка в запасах руды', pads_kzt: 'Перевалка, нужная рудным складам',
  overloads: 'Перегрузок мельницы',
};
const OPTION = {
  label: 'что сделать', actions: 'сообщения', delta_kzt: 'разница маржи', cu_delta_t: 'разница меди', rank: 'место',
  forecast: 'прогноз', summary: 'итог прогноза', actions_text: 'что будет сделано', recommendable: 'можно рекомендовать',
  verdict: 'вывод', gain_se_kzt: 'разброс выигрыша', confidence: 'уверенность',
  split_revenue_kzt: 'вклад меди', split_inventory_kzt: 'вклад запасов руды и вскрыши', split_rehandle_kzt: 'вклад перевалки',
  split_diesel_kzt: 'вклад дизеля', split_power_kzt: 'вклад электроэнергии', split_resources_kzt: 'ресурсы смены',
  cost_kzt: 'цена ресурсов смены', reason: 'почему окупается или нет', copper_text: 'откуда разница меди',
};

// Words for a feature name the page does not know yet, from its parts and unit.
function unknownLabel(name) {
  const unit = [['_kzt_h', 'деньги в час'], ['_kzt', 'деньги'], ['_tph', 'поток'], ['_pct', 'доля'], ['_min', 'время'], ['_t', 'масса'],
    ['_text', 'текст'], ['_sim_time', 'момент'], ['_h', 'часы'], ['_m', 'длина']].find(([suffix]) => name.endsWith(suffix));
  return unit ? `Показатель модели (${unit[1]})` : 'Показатель модели';
}

export function featureLabel(name, kind) {
  const known = FEATURES_BY_KIND[kind]?.[name] ?? FEATURES[name];
  if (known) return known;
  let match = /^([ab])_(.+)$/.exec(name);
  if (match && BRANCH[match[2]]) return `${BRANCH[match[2]]}, вариант ${match[1] === 'a' ? 'А' : 'Б'}`;
  match = /^option(\d+)_(.+)$/.exec(name);
  if (match && OPTION[match[2]]) return `Вариант ${match[1]}: ${OPTION[match[2]]}`;
  if (match && /_kzt$/.test(match[2])) return `Вариант ${match[1]}: составляющая маржи`;
  if (match) return `Вариант ${match[1]}: другое`;
  match = /^kpi(\d+)_(.+)$/.exec(name);
  const LINK = { label: 'показатель', unit: 'единица', baseline: 'до сценария', current: 'сейчас', significant: 'больше колебаний',
    baseline_known: 'база есть', baseline_text: 'база', current_source: 'откуда «сейчас»', current_kind: 'откуда «сейчас»', current_forecast: '«сейчас» — прогноз', forecast: '«сейчас» — прогноз' };
  if (match) return `Звено ${match[1]}: ${LINK[match[2]] ?? 'служебное'}`;
  // A name the page does not know is not shown as it is: it is said in words.
  return unknownLabel(name);
}

const SPEC = {
  mill_design_tph: 'Проектная подача мельницы',
  causes: 'Причины и ожидаемая длительность',
  licence_tph: 'Производительность по лицензии',
  feed_bin_t: 'Ёмкость приёмного склада',
  flowsheet: 'Схема переработки',
  recovery_basis: 'Откуда извлечение',
  recovery_check_pct: 'Извлечение для сверки',
  slime_recovery_pct: 'Извлечение из шламов',
  sand_recovery_ref_pct: 'Опорное извлечение из песков',
  slimes_base_share: 'Базовая доля шламов',
  slimes_cu_ratio: 'Содержание Cu в шламах к питанию',
  clay_ref_pct: 'Опорная глинистость питания',
  clay_loss_pct_per_pct: 'Потеря извлечения на 1 % глины',
  clay_plant_design_tph: 'Проектная переработка ОФ-2',
  annual_ore_mt: 'Переработка руды в год',
  trucks: 'Самосвалов',
  loaders: 'Экскаваторов',
  rim_elevation_m: 'Отметка борта',
  floor_elevation_m: 'Отметка дна',
  bench_height_m: 'Высота уступа',
  blast_exclusion_radius_m: 'Радиус опасной зоны взрыва',
  blast_reentry_min: 'Допуск после взрыва',
  blast_times: 'Время взрывов',
  height_m: 'Высота уступа',
  tonnes_initial: 'Исходный тоннаж',
  area_m2: 'Площадь',
  holes_planned: 'Скважин по проекту',
  hole_depth_m: 'Глубина скважины',
  charge_per_hole_kg: 'Заряд на скважину',
  centre_x: 'Центр, X',
  centre_y: 'Центр, Y',
  face_x: 'Забой, X',
  face_y: 'Забой, Y',
  mining_order: 'Порядок отработки',
  length_m: 'Длина',
  rise_m: 'Перепад высот',
  max_grade_pct: 'Наибольший уклон',
  width_m: 'Ширина',
  road_class: 'Класс дороги',
  hole_diameter_mm: 'Диаметр скважины',
  burden_m: 'Линия сопротивления',
  spacing_m: 'Расстояние между скважинами',
  penetration_ref_m_h: 'Паспортная скорость бурения',
  bucket_m3: 'Ковш',
  bucket_t: 'Руды в ковше',
  passes_per_truck: 'Ковшей на самосвал',
  pass_s: 'Цикл черпания',
  power_rated_kw: 'Установленная мощность',
  travel_kmh: 'Скорость переезда',
  model: 'Модель',
  payload_t: 'Грузоподъёмность',
  empty_mass_t: 'Масса без груза',
  engine_kw: 'Мощность двигателя',
  max_speed_kmh: 'Наибольшая скорость',
  tank_l: 'Топливный бак',
  full_power_fuel_lph: 'Расход на полной мощности',
  tyre_temp_limit_c: 'Предел температуры шин',
  pm_interval_h: 'Интервал ТО',
  rated_tph: 'Паспортная производительность',
  hopper_capacity_t: 'Вместимость бункера',
  motor_kw: 'Мощность привода',
  product_p80_mm: 'Крупность продукта, P80',
  lift_m: 'Высота подъёма',
  belt_speed_ms: 'Скорость ленты',
  transport_s: 'Время транспортирования',
  capacity_t: 'Вместимость',
  live_hours: 'Запас при полном складе',
  radius_m: 'Радиус',
  tip_x: 'Точка разгрузки, X',
  tip_y: 'Точка разгрузки, Y',
  reclaim_rate_tph: 'Производительность отгрузки',
  design_tph: 'Проектная подача',
  installed_mw: 'Установленная мощность',
  specific_energy_ref_kwh_t: 'Паспортный удельный расход энергии',
  bwi_ref_kwh_t: 'Расчётная крепость руды',
  recovery_ref_pct: 'Расчётное извлечение',
  conc_grade_pct: 'Содержание Cu в концентрате',
  mill_installed_mw: 'Мощность мельницы',
  pumps: 'Колонок',
  flow_l_min: 'Подача колонки',
  bays: 'Боксов',
  units: 'Агрегатов',
  unit_installed_mw: 'Мощность одного агрегата',
  charge_range_pct: 'Рабочее заполнение барабана, %',
  discharge_x: 'Выпуск хвостов, X',
  discharge_y: 'Выпуск хвостов, Y',
  density_t_m3: 'Плотность хвостов',
  shipment_t: 'Партия отгрузки',
  grade_pct: 'Содержание Cu',
  store_capacity_t: 'Вместимость склада',
  number_from_top: 'Номер сверху',
  working: 'Рабочий уступ',
  role: 'Назначение',
  installed_kw: 'Установленная мощность',
  slot: 'Номер ячейки прогноза',
  default_horizon_h: 'Срок прогноза по умолчанию',
  series_step_h: 'Шаг почасовых рядов',
  module: 'Модуль',
  sag_design_tph_2014: 'Проектная подача МПСИ (2014)',
  annual_mt: 'Переработка в год',
  min_feed_tph: 'Наименьшая подача',
  max_setpoint_tph: 'Наибольшая уставка',
  overload_t: 'Предел перегруза',
  copper_usd_t: 'Цена меди',
  usd_kzt: 'Курс доллара',
  payable_cu_pct: 'Оплачиваемая доля меди',
  tc_usd_t: 'Плата за переработку концентрата',
  rc_usc_lb: 'Плата за рафинирование',
  diesel_kzt_l: 'Цена дизельного топлива',
  power_kzt_kwh: 'Цена электроэнергии',
  diesel_co2_kg_l: 'CO₂ от литра дизеля',
  grid_co2_t_mwh: 'CO₂ энергосистемы',
  stock_kzt_t: 'Работа парка на тонну горной массы',
  rehandle_kzt_t: 'Стоимость перевалки руды',
  ref_grade_pct: 'Средняя руда в оценке запасов',
  cu_value_kzt_t: 'Цена тонны меди в руде',
  stock_note: 'Как оцениваются запасы руды',
  copper_price_source: 'Источник цены меди',
  margin_note: 'Что входит в маржу',
  service: 'Сервис модуля',
  depth_m: 'Глубина',
  rim_length_m: 'Длина по верхней бровке',
  rim_width_m: 'Ширина по верхней бровке',
  benches: 'Уступов',
  face_angle_deg: 'Угол откоса уступа',
  berm_m: 'Ширина бермы',
  final_wall_angle_deg: 'Угол конечного борта',
  depth_basis: 'Откуда глубина',
  ramps: 'Съезды',
  extra_blast_times: 'Дополнительное окно взрывов',
  blast_rule: 'Правило взрывов',
  ramp: 'Съезд',
  stack_height_m: 'Высота штабеля',
  climate_basis: 'Источник климатических норм',
  station: 'Метеостанция',
  heavy_rain_mm_h: 'Порог сильного дождя',
};
export const specLabel = (key) => SPEC[key] ?? unknownLabel(key).replace('Показатель модели', 'Параметр модели');

// Units of nameplate values whose key does not end in a plain unit.
const SPEC_UNIT_KEYS = { usd_kzt: '₸ за $', copper_usd_t: '$/т', tc_usd_t: '$/т', rc_usc_lb: '¢/фунт', diesel_kzt_l: '₸/л',
  power_kzt_kwh: '₸/кВт·ч', diesel_co2_kg_l: 'кг/л', grid_co2_t_mwh: 'т/МВт·ч', stock_kzt_t: '₸/т', rehandle_kzt_t: '₸/т',
  cu_value_kzt_t: '₸/т Cu', annual_mt: 'млн т', heavy_rain_mm_h: 'мм/ч', face_angle_deg: '°', final_wall_angle_deg: '°', sag_design_tph_2014: 'т/ч' };
// A nameplate value as the page writes it: yes or no, a twin by its name, a known word.
export function specValueText(key, value) {
  if (typeof value === 'boolean') return value ? 'да' : 'нет';
  // A scenario's causes: each with its expected duration.
  if (Array.isArray(value) && value.every((item) => item && typeof item === 'object' && 'title' in item)) {
    return value.map((item) => `${item.title}${typeof item.hours === 'number' ? ` — ${formatNumber(item.hours, 1)}${NBSP}ч` : ''}`).join('; ');
  }
  if (typeof value === 'number') {
    const unit = specUnit(key);
    return `${formatNumber(value)}${unit ? `${unit.startsWith('°') ? '' : NBSP}${unit}` : ''}`;
  }
  if (ENUMS[key]?.[value]) return ENUMS[key][value];
  return plainWords(String(value));
}

// A sentence of the model as the page shows it: sums in млн ₸, and a feature
// it names in brackets ("(rehandle_cost_kzt_h)") called by its label.
export function plainWords(text) {
  return moneyWords(text).replace(/\s*\(([a-z][a-z0-9]*(?:_[a-z0-9]+)+)\)/g, (match, name) => {
    const label = featureLabel(name);
    return label.startsWith('Показатель модели') ? '' : ` («${label}»)`;
  });
}

// Unit of a nameplate value, read from the end of its key.
const SPEC_UNITS = [['_deg', '°'], ['_t_m3', 'т/м³'], ['_kwh_t', 'кВт·ч/т'], ['_l_min', 'л/мин'], ['_m_h', 'м/ч'], ['_tph', 'т/ч'], ['_kmh', 'км/ч'],
  ['_lph', 'л/ч'], ['_pct', '%'], ['_min', 'мин'], ['_m2', 'м²'], ['_m3', 'м³'], ['_mm', 'мм'], ['_ms', 'м/с'],
  ['_mw', 'МВт'], ['_kw', 'кВт'], ['_kg', 'кг'], ['_m', 'м'], ['_t', 'т'], ['_s', 'с'], ['_h', 'ч'], ['_l', 'л'],
  ['_c', '°C'], ['_x', 'м'], ['_y', 'м'], ['_hours', 'ч']];
export function specUnit(key) {
  if (SPEC_UNIT_KEYS[key]) return SPEC_UNIT_KEYS[key];
  const found = SPEC_UNITS.find(([suffix]) => key.endsWith(suffix));
  return found ? found[1] : '';
}

const RELATIONS = {
  _parents: 'Входит в',
  pit: 'Карьер',
  mine: 'Рудник',
  crusher: 'Дробилка',
  mill: 'Мельница',
  substation: 'Подстанция',
  weather: 'Погода',
  journal: 'Журнал',
  modules: 'Модули',
  scenarios: 'Сценарии',
  benches: 'Уступы',
  loaders: 'Экскаваторы',
  drill_rigs: 'Буровые станки',
  blocks: 'Блоки',
  bench: 'Уступ',
  access_node: 'Подъезд (узел дорог)',
  segments: 'Примыкающие участки',
  serves: 'Обслуживает',
  from: 'Откуда',
  to: 'Куда',
  feeds: 'Подаёт на',
  fed_by: 'Получает от',
  power: 'Электропитание',
  supplies: 'Питает',
  home_node: 'Место стоянки',
  home_loader: 'Закреплён за экскаватором',
  fuel_station: 'Заправка',
  workshop: 'Мастерская',
  affects: 'Влияет на',
  controls: 'Управляет',
  targets: 'Возможные объекты',
  default_target: 'Объект по умолчанию',
  valid_targets: 'Можно запустить на',
  // relations kept in features
  assigned_loader: 'Экскаватор рейса',
  assigned_dump: 'Пункт разгрузки',
  segment: 'Сейчас на участке',
  block: 'Работает в блоке',
  truck: 'Грузит самосвал',
  blast_block: 'Взрываемый блок',
  target: 'Объект сценария',
  thing: 'Объект события',
  route: 'Маршрут',
  affected: 'Затронуто сценарием',
  subject: 'Предмет прогноза',
  requested_by: 'Кто запросил',
  closed_by: 'Кем закрыт',
  destination: 'Куда везти',
  units_at_risk: 'В зоне риска',
  forecasts: 'Прогнозы',
  option1_forecast: 'Прогноз варианта 1',
  option2_forecast: 'Прогноз варианта 2',
  option3_forecast: 'Прогноз варианта 3',
  option4_forecast: 'Прогноз варианта 4',
  effect_forecast: 'Прогноз для замера эффекта',
  concentrate: 'Концентрат',
  tailings: 'Хвосты',
  circuit: 'Цепь измельчения',
  advisor: 'Советник',
  module: 'Модуль',
  units: 'Агрегаты',
  clay_plant: 'Фабрика глинистых руд ОФ-2',
  clay_stockpile: 'Склад глинистой руды',
};
export const relationLabel = (name) => RELATIONS[name] ?? 'Связь';

const UNITS = {
  t: 'т', 't/h': 'т/ч', min: 'мин', h: 'ч', m: 'м', mm: 'мм', 'km/h': 'км/ч', 'm/h': 'м/ч', l: 'л', 'l/h': 'л/ч',
  'l/t': 'л/т', kW: 'кВт', MW: 'МВт', MWh: 'МВт·ч', 'kWh/t': 'кВт·ч/т', deg: '°', x: '×', 't/t': 'т/т',
  'kg/m3': 'кг/м³', 'mm/h': 'мм/ч', '% Cu': '% Cu', '%': '%', '°C': '°C', s: 'с', kg: 'кг',
  'млн тг': 'млн ₸', 'тг': '₸', 'тг/ч': '₸/ч', 'MWh/h': 'МВт·ч/ч', KZT: '₸', kzt: '₸', '₸': '₸', 'KZT/h': '₸/ч', 'kzt/h': '₸/ч', '₸/h': '₸/ч', '₸/ч': '₸/ч',
  tCO2: 'т CO₂', 't CO2': 'т CO₂', 't CO2e': 'т CO₂', 'tCO2/h': 'т CO₂/ч', 't CO2/h': 'т CO₂/ч', 't CO2e/h': 'т CO₂/ч',
};
export const unitText = (unit) => (unit ? UNITS[unit] ?? unit : '');
const isMoneyUnit = (unit) => typeof unit === 'string' && /kzt|₸|tenge|тг|тенге/i.test(unit);

const NBSP = '\u00a0';

const THIN = ' ';                  // narrow no-break space between groups of thousands

// Russian grouping uses a no-break space; a narrow one reads better in figures.
const thin = (text) => text.replace(/ (?=\d)/g, THIN);

export function formatNumber(value, digits = 3) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return String(value ?? '—');
  return thin(value.toLocaleString('ru-RU', { maximumFractionDigits: digits }));
}

// Always the same number of decimals, for values that are read side by side.
export function formatFixed(value, digits = 0) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—';
  return thin(value.toLocaleString('ru-RU', { minimumFractionDigits: digits, maximumFractionDigits: digits }));
}

const MINUS = '−';

// A figure with its sign: +12,4 / −3 / 0 (a value that rounds to zero has none).
function withSign(value, digits) {
  const text = formatFixed(Math.abs(value), digits);
  if (text === formatFixed(0, digits)) return text;
  return `${value > 0 ? '+' : MINUS}${text}`;
}

export function formatDelta(value, digits = 1) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—';
  return withSign(value, digits);
}


// Every sum of money on the page is written by these two: millions of tenge
// (or billions from a thousand millions), with narrow spaces between groups of
// thousands: 0,22 млн ₸, 12,4 млн ₸, 1 069 млн ₸, 1,23 млрд ₸. `sign` writes + before gains.
export function formatTenge(value, { sign = false } = {}) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—';
  if (Math.abs(value) >= 1e9) {
    const billions = value / 1e9;
    const text = sign ? withSign(billions, 2) : `${billions < 0 ? MINUS : ''}${formatFixed(Math.abs(billions), 2)}`;
    return `${text}${NBSP}млрд${NBSP}₸`;
  }
  return `${formatMillions(value, { sign })}${NBSP}млн${NBSP}₸`;
}

// Millions of tenge as a bare figure, for places headed "млн ₸": 1 069, 12,4, 0,22.
export function formatMillions(value, { sign = false, digits } = {}) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—';
  const millions = value / 1e6;
  const size = Math.abs(millions);
  const places = digits ?? (size >= 100 ? 0 : size >= 1 ? 1 : 2);
  const text = formatFixed(size, places);
  if (text === formatFixed(0, places)) return '0';
  if (sign) return withSign(millions, places);
  return millions < 0 ? `${MINUS}${text}` : text;
}

// A sum with its spread, both in the same unit: +19,7 ± 4,1 млн ₸.
export function formatTengeSpread(value, spread, { sign = false } = {}) {
  if (typeof spread !== 'number' || !Number.isFinite(spread) || spread <= 0) return formatTenge(value, { sign });
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—';
  if (Math.abs(value) >= 1e9 || spread >= 1e9) return `${formatTenge(value, { sign })} ± ${formatTenge(spread)}`;
  // Each figure keeps the precision of its own size, as everywhere on the page: +146 ± 4,6 млн ₸.
  return `${formatMillions(value, { sign })}${NBSP}±${NBSP}${formatMillions(spread)}${NBSP}млн${NBSP}₸`;
}

// Text of the model as the page shows it: sums in млн ₸, and a unit kept on
// the line of its figure ("4,0 ч" never breaks between the two).
const GLUED = /(\d) (?=(ч|т|мин|км|м|с|т\/ч|км\/ч|%|°C|МВт|кВт|л|₸)(?![а-яёa-z]))/g;
export function pageText(text) {
  if (typeof text !== 'string') return text;
  return moneyWords(text).replace(GLUED, `$1${NBSP}`);
}

// Sums of money in the model's own sentences ("маржа 1069,4 млн тг", "897 тг/т")
// written the way the page writes them: млн ₸ with narrow spaces between groups,
// a large sum in plain tenge turned into millions.
const MONEY_WORDS = /([+−-]?)(\d(?:[\d   ]*\d)?)(?:,(\d+))?[\s  ]*(млрд|млн)?\.?[\s  ]*(?:тг|тенге|KZT)(?![а-яёa-z])(\/)?/gi;
export function moneyWords(text) {
  if (typeof text !== 'string' || !/тг|тенге|KZT/i.test(text)) return text;
  return text.replace(MONEY_WORDS, (match, sign, whole, fraction, scale, per) => {
    const digits = whole.replace(/\D/g, '');
    const minus = sign === '-' || sign === MINUS;
    const grouped = thin(Number(digits).toLocaleString('ru-RU'));
    const figure = `${minus ? MINUS : sign}${grouped}${fraction ? `,${fraction}` : ''}`;
    if (scale) return `${figure}${NBSP}${scale.toLowerCase()}${NBSP}₸${per ?? ''}`;
    const value = Number(`${digits}.${fraction ?? 0}`) * (minus ? -1 : 1);
    if (!per && Math.abs(value) >= 1e5) return formatTenge(value, { sign: sign === '+' });
    return `${figure}${NBSP}₸${per ?? ''}`;
  });
}

// A number with as many decimals as its size deserves: 3 689, 16,3, 0,405.
export function formatSmart(value) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return String(value ?? '—');
  const size = Math.abs(value);
  return formatNumber(value, size >= 1000 ? 0 : size >= 100 ? 1 : size >= 10 ? 1 : size >= 1 ? 2 : 3);
}

// "2026-10-26T10:14:20" -> "26.10.2026 10:14:20"
export function formatSimTime(iso) {
  const match = /^(\d{4})-(\d\d)-(\d\d)T(\d\d:\d\d:\d\d)/.exec(iso ?? '');
  return match ? `${match[3]}.${match[2]}.${match[1]} ${match[4]}` : '—';
}
export function formatSimClock(iso) {
  const match = /T(\d\d:\d\d)(:\d\d)?/.exec(iso ?? '');
  return match ? match[1] : '—';
}
export function formatSimDate(iso) {
  const match = /^(\d{4})-(\d\d)-(\d\d)/.exec(iso ?? '');
  return match ? `${match[3]}.${match[2]}.${match[1]}` : '';
}
// Minutes between two simulated moments; undefined when either is missing.
export function simMinutesBetween(fromIso, toIso) {
  const from = Date.parse(`${fromIso}Z`);
  const to = Date.parse(`${toIso}Z`);
  return Number.isFinite(from) && Number.isFinite(to) ? (to - from) / 60000 : undefined;
}

export function formatDuration(minutes) {
  if (typeof minutes !== 'number' || !Number.isFinite(minutes)) return '—';
  const whole = Math.max(0, Math.round(minutes));
  if (whole < 60) return `${whole}${NBSP}мин`;
  return `${Math.floor(whole / 60)}${NBSP}ч ${String(whole % 60).padStart(2, '0')}${NBSP}мин`;
}

// Text of a feature value for people. Values that name another twin are
// resolved by the caller; this covers numbers, states and plain strings.
export function valueText(name, value, unit) {
  if (value === undefined || value === null || value === '') return '—';
  if (typeof value === 'boolean') return value ? 'да' : 'нет';
  if (typeof value === 'number') {
    if (isMoneyUnit(unit) || (!unit && /_kzt(_h)?$/.test(name))) {
      // A unit of millions (млн тг) carries its scale; the sum is written in millions all the same.
      const scale = /млрд/.test(unit ?? '') ? 1e9 : /млн/.test(unit ?? '') ? 1e6 : 1;
      return `${formatTenge(value * scale)}${/\/\s*(h|ч)$|_h$/.test(unit || name) ? '/ч' : ''}`;
    }
    const text = formatNumber(value);
    const suffix = unitText(unit);
    if (!suffix) return text;
    if (suffix === '×') return `×${text}`;
    if (suffix === '°') return `${text}°`;
    return `${text}${NBSP}${suffix}`;
  }
  if (name.endsWith('sim_time')) return formatSimTime(value);
  if (name.includes('_series_')) {
    const values = parseSeries(value);
    if (values.length) return `по часам: ${values.map((v) => formatNumber(v, 1)).join('; ')}`;
  }
  if (ENUMS[name]?.[value]) return ENUMS[name][value];
  if (STATE_FEATURES.has(name)) return stateText(value);
  if (name === 'requested_by' || name === 'request_id') return requestText(String(value));
  if (/(^|_)unit$/.test(name)) return unitText(value);
  return plainWords(String(value));
}

// Who asked for a forecast, in words: the page of a visitor, a module, the advisor.
function requestText(value) {
  const number = /-(\d+)$/.exec(value)?.[1];
  const who = /^page-/.test(value) ? 'страница' : /advisor/.test(value) ? 'советник' : /^module-|^mine-/.test(value) ? 'модуль' : '';
  if (!who) return value;
  return number ? `${who}, запрос ${number}` : who;
}

// Messages of a forecast branch or an advisor's option ("module-maint
// set-enabled (enabled=False); …") in words; `nameOf` turns a twin's name
// into its title.
const SUBJECT_WORDS = {
  activate: 'запустить', cancel: 'отменить', release: 'вернуть в работу', 'set-reclaim': 'задать подачу со склада',
  'set-feed-rate': 'задать подачу на мельницу', 'set-destination': 'задать место разгрузки', 'move-to-block': 'перевести на блок',
  'set-speed': 'задать скорость модели', 'schedule-service': 'назначить обслуживание', 'cancel-service': 'отменить обслуживание',
  'expedite-repair': 'ускорить ремонт (вторая бригада)', 'prioritise-repair': 'ремонт вне очереди', hold: 'придержать',
  assign: 'назначить', pause: 'поставить на паузу', resume: 'продолжить', apply: 'применить вариант', forecast: 'прогноз',
};
export function actionsText(text, nameOf = (name) => name) {
  if (typeof text !== 'string' || !text.trim()) return '—';
  return text.split(/;\s*/).filter(Boolean).map((part) => {
    const match = /^\s*([\w.:-]+)\s+([\w-]+)\s*(?:\((.*)\))?\s*$/.exec(part);
    if (!match) return part;
    const [, thing, subject, args = ''] = match;
    const pairs = Object.fromEntries(args.split(/,\s*/).filter(Boolean).map((pair) => pair.split('=').map((x) => x.trim())));
    let verb = SUBJECT_WORDS[subject] ?? 'другая команда';
    if (subject === 'set-enabled') verb = /^(false|0|no)$/i.test(pairs.enabled ?? '') ? 'выключить' : 'включить';
    const extra = Object.entries(pairs).filter(([key]) => key !== 'enabled')
      .map(([, value]) => (/^-?\d+(\.\d+)?$/.test(value) ? formatNumber(Number(value)) : nameOf(value)))
      .filter((value) => value && !/^[a-z_]+$/i.test(value));
    return `${nameOf(thing)}: ${verb}${extra.length ? ` (${extra.join(', ')})` : ''}`;
  }).join('; ');
}

// "812.5,790,..." -> [812.5, 790, ...]; values that are not numbers are left out.
export function parseSeries(text) {
  if (typeof text !== 'string' || !text.trim()) return [];
  return text.split(/[,;\s]+/).filter(Boolean).map(Number).filter(Number.isFinite);
}

export function percentChange(before, after) {
  if (typeof before !== 'number' || typeof after !== 'number' || !before) return '';
  const change = Math.round((100 * (after - before)) / Math.abs(before));
  if (!change) return '0 %';
  return `${change > 0 ? '+' : '−'}${Math.abs(change)}${NBSP}%`;
}

// "Самосвал №7" -> "№7"; the short captions used on the plan.
export function shortTitle(kind, title) {
  const number = /№\s*(\d+)/.exec(title);
  switch (kind) {
    case 'truck': return number ? `№${number[1]}` : title;
    case 'loader': return number ? `Экс. №${number[1]}` : title;
    case 'drill-rig': return number ? `Бур. №${number[1]}` : title;
    case 'rom-pad': {
      if (number) return `Склад №${number[1]}`;
      if (/богат/i.test(title)) return 'Склад богатой руды';
      if (/бедн/i.test(title)) return 'Склад бедной руды';
      return title;
    }
    case 'block': return title.replace(/^Блок\s+/, '');
    case 'bench': {
      // «Уступ 300 м (разноска юго-восточного борта)», «Дно карьера, горизонт 120 м»: the elevation.
      const level = /(\d+)\s*м(?![а-яё])/i.exec(title);
      return level ? `${level[1]} м` : title.replace(/^Уступ\s+/, '');
    }
    case 'crusher': return 'Дробилка';
    case 'stockpile': return 'Склад руды';
    case 'mill': return 'Мельница';
    case 'sag-mill': return 'МПСИ';
    case 'ball-mill': return 'Шаровые мельницы';
    case 'concentrate': return 'Концентрат';
    case 'tailings': return 'Хвосты';
    case 'substation': return 'Подстанция';
    case 'fuel-station': return 'Заправка';
    case 'workshop': return 'Мастерская';
    case 'waste-dump': return 'Отвал';
    case 'conveyor': return 'Конвейер';
    default: return title;
  }
}
