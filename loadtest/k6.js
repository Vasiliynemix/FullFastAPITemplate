// ============================================================
// Нагрузочный / дымовой тест API на k6.
// Запуск — через Makefile (`make loadtest ...`) или напрямую:
//   docker run --rm --network fastapi_default \
//     -e BASE_URL=https://nginx -v $PWD/loadtest:/scripts:ro grafana/k6 run /scripts/k6.js
//
// Параметры (через -e ИМЯ=значение):
//   BASE_URL    базовый URL (по умолч. https://nginx — через nginx внутри сети)
//   API_PREFIX  префикс API (по умолч. /api/v1)
//   MODE        all | smoke | traffic | ratelimit (по умолч. all)
//   VUS         число виртуальных пользователей в traffic (по умолч. 50)
//   DURATION    длительность traffic В СЕКУНДАХ, число (по умолч. 30)
//   SPREAD_IPS  true => уникальный X-Forwarded-For на VU (обходим app-лимит); false => общий
//   API_KEY     X-API-Key, если включён GLOBAL_API_KEY_ENABLED (иначе пусто)
// ============================================================

import http from 'k6/http';
import { check, sleep, group } from 'k6';
import { Counter } from 'k6/metrics';

// ---------- конфигурация из окружения ----------
const BASE = (__ENV.BASE_URL || 'https://nginx').replace(/\/$/, '');
const PREFIX = __ENV.API_PREFIX || '/api/v1';
const API = `${BASE}${PREFIX}`;
const MODE = __ENV.MODE || 'all';
const VUS = parseInt(__ENV.VUS || '50', 10);
const DUR = parseInt(__ENV.DURATION || '30', 10); // секунды
const SPREAD_IPS = (__ENV.SPREAD_IPS || 'true') === 'true';
const API_KEY = __ENV.API_KEY || '';

// ---------- кастомные метрики ----------
const appRateLimited = new Counter('app_rate_limited_429'); // app-лимитер (X-Forwarded-For)
const nginxThrottled = new Counter('nginx_throttled_503'); // глобальный лимит nginx (200 r/s)

// ============================================================
// helpers
// ============================================================
function clientIp() {
  // Уникальный «клиент» на VU => свой бакет app-лимитера. В ratelimit намеренно общий.
  if (!SPREAD_IPS) return '198.51.100.50';
  return `10.${(__VU >> 8) & 255}.${__VU & 255}.${((__VU * 7) & 255) || 1}`;
}

function headers(token) {
  const h = {
    'Content-Type': 'application/json',
    // Подменяем «реальный» IP клиента для app-лимитера (nginx допишет свой следом,
    // приложение берёт ПЕРВЫЙ адрес — наш). Для throughput это разводит бакеты.
    'X-Forwarded-For': clientIp(),
  };
  if (token) h['Authorization'] = `Bearer ${token}`;
  if (API_KEY) h['X-API-Key'] = API_KEY;
  return { headers: h };
}

function track(res) {
  if (res.status === 429) appRateLimited.add(1);
  if (res.status === 503) nginxThrottled.add(1);
  return res;
}

function uniqEmail(tag) {
  return `load_${tag}_${__VU}_${__ITER}_${Date.now()}_${Math.floor(Math.random() * 1e6)}@example.com`;
}

function phone() {
  // E.164: +1 + 10 цифр
  let d = '';
  for (let i = 0; i < 10; i++) d += Math.floor(Math.random() * 10);
  return `+1${d}`;
}

// Регистрация + логин => access_token. role: 'user' | 'admin' | ...
function registerAndLogin(role) {
  const email = uniqEmail(role || 'user');
  const password = 'password123';
  const reg = track(
    http.post(
      `${API}/auth/register`,
      JSON.stringify({ email, password, full_name: 'Load Test', role: role || 'user' }),
      headers(),
    ),
  );
  if (reg.status !== 200 && reg.status !== 201) return null;
  const log = track(
    http.post(`${API}/auth/login`, JSON.stringify({ email, password }), headers()),
  );
  if (log.status !== 200) return null;
  const body = log.json();
  return { token: body && body.data ? body.data.access_token : null, refresh: body && body.data ? body.data.refresh_token : null };
}

// ============================================================
// scenario: smoke — по разу КАЖДУЮ ручку, всё должно быть зелёным
// ============================================================
export function smoke() {
  group('health', () => {
    check(track(http.get(`${API}/health/live`, headers())), { 'live 200': (r) => r.status === 200 });
    check(track(http.get(`${API}/health/ready`, headers())), { 'ready 200': (r) => r.status === 200 });
  });

  let admin = null;
  group('auth', () => {
    const email = uniqEmail('admin');
    const password = 'password123';
    const reg = track(http.post(`${API}/auth/register`, JSON.stringify({ email, password, full_name: 'Admin', role: 'admin' }), headers()));
    check(reg, { 'register 2xx': (r) => r.status === 200 || r.status === 201 });
    const log = track(http.post(`${API}/auth/login`, JSON.stringify({ email, password }), headers()));
    check(log, { 'login 200': (r) => r.status === 200 });
    admin = log.status === 200 ? log.json().data : null;

    if (admin) {
      check(track(http.get(`${API}/auth/me`, headers(admin.access_token))), { 'me 200': (r) => r.status === 200 });
      check(track(http.get(`${API}/auth/sessions`, headers(admin.access_token))), { 'sessions 200': (r) => r.status === 200 });
      const rf = track(http.post(`${API}/auth/refresh`, JSON.stringify({ refresh_token: admin.refresh_token }), headers()));
      check(rf, { 'refresh 200': (r) => r.status === 200 });
      if (rf.status === 200) admin.access_token = rf.json().data.access_token;
    }
  });

  group('users', () => {
    const create = track(http.post(`${API}/users`, JSON.stringify({ email: uniqEmail('u'), full_name: 'User One' }), headers(admin ? admin.access_token : null)));
    check(create, { 'create 201': (r) => r.status === 201 });
    const id = create.status === 201 ? create.json().data.id : null;

    check(track(http.get(`${API}/users?page=1&per_page=20`, headers())), { 'list 200': (r) => r.status === 200 });

    if (id && admin) {
      check(track(http.get(`${API}/users/${id}`, headers(admin.access_token))), { 'get 200': (r) => r.status === 200 });
      check(track(http.patch(`${API}/users/${id}`, JSON.stringify({ full_name: 'Renamed' }), headers(admin.access_token))), { 'patch 200': (r) => r.status === 200 });
    }
    check(track(http.get(`${API}/users/stream/all`, headers())), { 'stream 200': (r) => r.status === 200 });
    if (id && admin) {
      check(track(http.del(`${API}/users/${id}`, null, headers(admin.access_token))), { 'delete 2xx': (r) => r.status === 200 || r.status === 204 });
    }
  });

  group('notifications', () => {
    check(track(http.post(`${API}/notifications`, JSON.stringify({ recipient_phone: phone(), text: 'load test', markdown: false }), headers())), { 'queue 202': (r) => r.status === 202 });
  });

  if (admin) {
    group('logout', () => {
      check(track(http.post(`${API}/auth/logout`, null, headers(admin.access_token))), { 'logout 200': (r) => r.status === 200 });
    });
  }
}

// ============================================================
// scenario: traffic — объёмная смешанная нагрузка
// токен берём один раз на VU (модульная область видимости = per-VU в k6)
// ============================================================
let vuAuth = null;
let knownId = null;

export function traffic() {
  if (!vuAuth) {
    vuAuth = registerAndLogin('user') || { token: null };
  }
  const token = vuAuth.token;

  const dice = Math.random();
  if (dice < 0.4) {
    track(http.get(`${API}/users?page=${1 + Math.floor(Math.random() * 5)}&per_page=50`, headers()));
  } else if (dice < 0.6) {
    const r = track(http.post(`${API}/users`, JSON.stringify({ email: uniqEmail('t'), full_name: 'Traffic User' }), headers(token)));
    if (r.status === 201) knownId = r.json().data.id;
  } else if (dice < 0.75 && knownId) {
    track(http.get(`${API}/users/${knownId}`, headers(token)));
  } else if (dice < 0.85 && knownId) {
    track(http.patch(`${API}/users/${knownId}`, JSON.stringify({ full_name: 'Upd' }), headers(token)));
  } else if (dice < 0.95) {
    track(http.post(`${API}/notifications`, JSON.stringify({ recipient_phone: phone(), text: 'hi' }), headers()));
  } else {
    track(http.get(`${API}/auth/me`, headers(token)));
  }
  sleep(Math.random() * 0.3);
}

// ============================================================
// scenario: read — чистая ёмкость бэкенда без argon2
// (публичный GET /users, spread IP => без rate-limit). Меряем HTTP+DB путь.
// ============================================================
export function read() {
  // Уникальный IP на КАЖДЫЙ запрос => app-лимитер не накапливает burst, меряем сырой потолок
  const ip = `100.${(Math.random() * 255) | 0}.${(Math.random() * 255) | 0}.${((Math.random() * 254) | 0) + 1}`;
  const h = { headers: { 'X-Forwarded-For': ip, 'Content-Type': 'application/json' } };
  track(http.get(`${API}/users?page=${1 + Math.floor(Math.random() * 10)}&per_page=20`, h));
}

// ============================================================
// scenario: ratelimit — долбим из ОДНОГО IP, ловим app-429
// (общий X-Forwarded-For принудительно, независимо от SPREAD_IPS)
// ============================================================
export function hammer() {
  const h = { headers: { 'X-Forwarded-For': '203.0.113.99', 'Content-Type': 'application/json' } };
  if (API_KEY) h.headers['X-API-Key'] = API_KEY;
  // users (list) — публично, но проходит через rate-limit (health исключён из лимитера)
  track(http.get(`${API}/users?per_page=1`, h));
}

// ============================================================
// сборка сценариев под выбранный MODE
// ============================================================
function buildScenarios() {
  const s = {};
  if (MODE === 'all' || MODE === 'smoke') {
    s.smoke = { executor: 'shared-iterations', vus: 1, iterations: 1, maxDuration: '30s', exec: 'smoke', startTime: '0s' };
  }
  if (MODE === 'all' || MODE === 'traffic') {
    s.traffic = {
      executor: 'ramping-vus',
      exec: 'traffic',
      startTime: MODE === 'all' ? '3s' : '0s',
      startVUs: 0,
      stages: [
        { duration: `${Math.max(2, Math.floor(DUR * 0.3))}s`, target: VUS }, // разгон
        { duration: `${Math.max(2, Math.floor(DUR * 0.5))}s`, target: VUS }, // плато
        { duration: `${Math.max(2, Math.floor(DUR * 0.2))}s`, target: 0 }, // спад
      ],
    };
  }
  if (MODE === 'read') {
    s.read = {
      executor: 'ramping-vus',
      exec: 'read',
      startVUs: 0,
      stages: [
        { duration: `${Math.max(2, Math.floor(DUR * 0.3))}s`, target: VUS },
        { duration: `${Math.max(2, Math.floor(DUR * 0.5))}s`, target: VUS },
        { duration: `${Math.max(2, Math.floor(DUR * 0.2))}s`, target: 0 },
      ],
    };
  }
  if (MODE === 'all' || MODE === 'ratelimit') {
    s.ratelimit = {
      executor: 'constant-arrival-rate',
      exec: 'hammer',
      rate: 80, // 80 запросов/сек из одного IP — заведомо выше burst 20/с
      timeUnit: '1s',
      duration: '8s',
      preAllocatedVUs: 30,
      maxVUs: 60,
      startTime: MODE === 'all' ? `${3 + DUR + 2}s` : '0s', // после traffic, чтобы не делить лимит nginx
    };
  }
  return s;
}

function buildThresholds() {
  const t = {};
  // smoke обязан быть полностью зелёным — все ручки отвечают корректно
  if (MODE === 'all' || MODE === 'smoke') t['checks{scenario:smoke}'] = ['rate>0.99'];
  // в traffic следим за латентностью (429/503 — ожидаемы, по ним не валим прогон)
  if (MODE === 'all' || MODE === 'traffic') t['http_req_duration{scenario:traffic}'] = ['p(95)<1500'];
  return t;
}

export const options = {
  insecureSkipTLSVerify: true, // self-signed cert в localhost-проде
  scenarios: buildScenarios(),
  thresholds: buildThresholds(),
};
