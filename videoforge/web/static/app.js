/* VisionForge 桌面端前端：原生 JS，零构建，零外网依赖。 */

window.addEventListener('error', e => {
  const box = document.createElement('div');
  box.style.cssText = 'position:fixed;left:260px;bottom:18px;z-index:99;color:#ffb3c0;background:#2a0d16;' +
    'border:1px solid #ff6b81;padding:10px 12px;border-radius:8px;font:12px monospace;max-width:62vw;white-space:pre-wrap';
  box.textContent = 'JS Error: ' + e.message + ' @ ' + String(e.filename || '').split('/').pop() + ':' + e.lineno;
  document.body.appendChild(box);
});
window.addEventListener('unhandledrejection', e => {
  const box = document.createElement('div');
  box.style.cssText = 'position:fixed;left:260px;bottom:18px;z-index:99;color:#ffd79a;background:#2a1f0d;' +
    'border:1px solid #ffc266;padding:10px 12px;border-radius:8px;font:12px monospace;max-width:62vw;white-space:pre-wrap';
  box.textContent = 'Promise: ' + (e.reason?.message || e.reason);
  document.body.appendChild(box);
});

const S = {
  boot: null, project: null, settings: null, view: 'dashboard',
  selected: null, job: null, jobSince: 0, poll: null, doctor: null,
  workflows: [], doctorLoading: false,
  filter: 'all', exporting: false,
};

/* ------------------------------------------------------------------ */
/* 基础工具                                                            */
/* ------------------------------------------------------------------ */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function icon(name, cls = '') {
  return `<svg class="${cls}"><use href="#i-${name}"></use></svg>`;
}
function fmtDur(sec) {
  sec = Math.max(0, Number(sec) || 0);
  const m = Math.floor(sec / 60), s = Math.round(sec % 60);
  return m ? `${m}m${String(s).padStart(2, '0')}s` : `${s}s`;
}
function statusPill(status, clip) {
  const map = { rendered: '已出片', voiced: '已配音', keyframe: '有关键帧', failed: '失败', pending: '待生成', skipped: '已跳过' };
  return `<span class="pill ${status}">${map[status] || status}</span>`;
}

function authHeaders(extra = {}) {
  return window.VF_TOKEN ? { ...extra, 'X-VF-Token': window.VF_TOKEN } : extra;
}

async function api(path, opts = {}) {
  const init = { method: opts.method || 'GET', headers: authHeaders(opts.headers || {}) };
  if (opts.raw) {
    init.body = opts.body;
  } else if (opts.body !== undefined) {
    init.headers = authHeaders({ 'Content-Type': 'application/json' });
    init.body = JSON.stringify(opts.body);
  }
  const res = await fetch(path, init);
  if (!res.ok) {
    let msg = res.statusText;
    try { const j = await res.json(); msg = j.detail || msg; } catch (e) {}
    throw new Error(msg);
  }
  const ct = res.headers.get('content-type') || '';
  return ct.includes('json') ? res.json() : res.text();
}

function toast(msg, kind = 'ok', ms = 3200) {
  const el = document.createElement('div');
  el.className = `toast ${kind}`;
  el.innerHTML = `${icon(kind === 'err' ? 'alert' : kind === 'warn' ? 'alert' : 'check')}<span>${esc(msg)}</span>`;
  $('#toasts').appendChild(el);
  setTimeout(() => { el.style.opacity = '0'; el.style.transform = 'translateX(10px)'; }, ms - 220);
  setTimeout(() => el.remove(), ms);
}

/* ---------------- 撤销 / 重做 / 自动保存 ---------------- */
const HIST = { undo: [], redo: [], limit: 40 };
const AUTO = { timer: 0, dirty: false, sid: '' };

function jobRunning() { return !!(S.job && S.job.status === 'running'); }
function cloneShots() { return JSON.parse(JSON.stringify(S.project?.shots || [])); }

/** 改动前留快照；任务运行中不动历史，避免把中间状态写进撤销栈 */
function snapshotShots() {
  if (jobRunning() || !S.project) return false;
  HIST.undo.push(cloneShots());
  if (HIST.undo.length > HIST.limit) HIST.undo.shift();
  HIST.redo.length = 0;
  return true;
}

async function restoreShots(shots, msg) {
  const r = await api(`/api/projects/${S.project.slug}/shots/restore`,
    { method: 'POST', body: { shots } });
  S.project = r;
  AUTO.dirty = false; AUTO.sid = '';
  render();
  if (msg) toast(msg, 'ok', 1800);
}

async function undoShots() {
  if (jobRunning()) return toast(t('任务运行中，先等它结束'), 'warn');
  if (!HIST.undo.length) return toast(t('没有可撤销的操作'), 'warn');
  const prev = HIST.undo.pop();
  HIST.redo.push(cloneShots());
  await restoreShots(prev, t('已撤销'));
}

async function redoShots() {
  if (jobRunning()) return toast(t('任务运行中，先等它结束'), 'warn');
  if (!HIST.redo.length) return toast(t('没有可撤销的操作'), 'warn');
  const next = HIST.redo.pop();
  HIST.undo.push(cloneShots());
  await restoreShots(next, t('已重做'));
}

const FAIL_KINDS = {
  auth: ['鉴权失败', '检查 API Key 是否有效、是否有该模型权限'],
  rate: ['被限流', '等几分钟再试，或换一个模型/账号'],
  quota: ['余额或预算不足', '充值，或调高项目预算 VF_BUDGET'],
  vram: ['显存不足', '降分辨率到 512×288，并把 VF_WORKERS 设为 1'],
  safety: ['内容审核拦截', '改台词或提示词，避开敏感描述'],
  model: ['模型不存在', '检查模型名与账号权限'],
  timeout: ['任务超时', '本地模型请调大 MINIMAX_VIDEO_TIMEOUT'],
  empty: ['没有产出文件', '看日志确认工作流输出节点是否正确'],
  other: ['其他错误', '展开任务日志看原始报错'],
};

function failurePanel(p) {
  const f = p.failures;
  if (!f || !f.total) return '';
  const rows = (f.kinds || []).map(k => {
    const meta = FAIL_KINDS[k.kind] || FAIL_KINDS.other;
    return `<div class="insp-row"><label>${esc(t(meta[0]))}</label>
      <span>${k.count} · ${esc(t(meta[1]))}</span></div>`;
  }).join('');
  return `<div class="panel" style="margin-bottom:14px;border-color:rgba(226,75,74,.35)">
    <div class="panel-head">${icon('alert')}<h3>${t('失败原因')}</h3>
      <span class="sub">${t('失败 {n} 个', { n: f.total })}</span>
      <div class="spacer"></div>
      <button class="btn sm" data-act="retry-failed">${icon('refresh')}${t('重试失败镜头')}</button>
    </div>
    ${rows}
  </div>`;
}

function shotFieldsFromForm() {
  return {
    duration: Number($('#dDur').value),
    trim_start: Number($('#dTrim').value),
    subtitle: $('#dSub').value,
    dialogue: $('#dDlg').value,
    transition: $('#dTrans').value,
    transition_duration: Number($('#dTransDur')?.value || 0),
    enabled: $('#dEnabled').checked,
    speed: Number($('#dSpeed')?.value || 1),
    mirror: $('#dMirror')?.checked || false,
    reverse: $('#dReverse')?.checked || false,
    volume: Number($('#dVol')?.value ?? 1),
    filter_preset: $('#dFilter')?.value || 'none',
    brightness: Number($('#dBright')?.value || 0),
    contrast: Number($('#dContrast')?.value || 1),
    saturation: Number($('#dSat')?.value || 1),
    temperature: Number($('#dTemp')?.value || 0),
    title_text: $('#dTitle')?.value || '',
    title_position: $('#dTitlePos')?.value || 'bottom',
    title_size: Number($('#dTitleSize')?.value || 0),
    video_prompt: $('#dVp')?.value || '',
    image_prompt: $('#dIp')?.value || '',
  };
}

async function saveShotFields(sid, silent = false) {
  const r = await api(`/api/projects/${S.project.slug}/shots/${sid}`,
    { method: 'PATCH', body: shotFieldsFromForm() });
  const i = S.project.shots.findIndex(s => s.id === sid);
  if (i >= 0 && r.shot) Object.assign(S.project.shots[i], r.shot);
  if (!silent) render();
  return r;
}

function paintAutosave(text, bad) {
  const el = $('#autoSaveHint');
  if (!el) return;
  el.textContent = t(text);
  el.style.color = bad ? 'var(--danger)' : 'var(--dim)';
}

/** 检查器里的改动 800ms 防抖自动保存；任务运行中不抢写 */
function bindInspectorAutosave() {
  const box = $('#inspector');
  if (!box || box.dataset.auto) return;
  box.dataset.auto = '1';
  const kick = () => {
    const sid = box.dataset.shot;
    if (!sid || jobRunning()) return;
    if (AUTO.sid !== sid || !AUTO.dirty) {
      snapshotShots();
      AUTO.sid = sid;
      AUTO.dirty = true;
    }
    paintAutosave('自动保存中…');
    clearTimeout(AUTO.timer);
    AUTO.timer = setTimeout(async () => {
      try {
        await saveShotFields(sid, true);
        AUTO.dirty = false;
        paintAutosave('已自动保存');
      } catch (err) {
        paintAutosave('自动保存失败', true);
      }
    }, 800);
  };
  box.addEventListener('input', kick);
  box.addEventListener('change', kick);
}

// Ctrl/Cmd+Z 撤销、Ctrl+Shift+Z 或 Ctrl+Y 重做；输入框里保留原生行为
document.addEventListener('keydown', e => {
  const tag = (e.target?.tagName || '').toLowerCase();
  if (tag === 'input' || tag === 'textarea' || tag === 'select' || e.target?.isContentEditable) return;
  if (!(e.ctrlKey || e.metaKey)) return;
  const key = (e.key || '').toLowerCase();
  if (key === 'z') {
    e.preventDefault();
    if (e.shiftKey) redoShots(); else undoShots();
  } else if (key === 'y') {
    e.preventDefault();
    redoShots();
  }
});

/* ------------------------------------------------------------------ */
/* 星空粒子背景                                                        */
/* ------------------------------------------------------------------ */
function initSky() {
  const cv = $('#sky');
  const ctx = cv.getContext('2d');
  let w = 0, h = 0, dpr = Math.min(2, window.devicePixelRatio || 1);
  let stars = [], nebulas = [], shooting = null, lastShot = 0, mouse = { x: 0, y: 0 };

  function resize() {
    w = cv.clientWidth; h = cv.clientHeight;
    cv.width = Math.floor(w * dpr); cv.height = Math.floor(h * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const count = Math.max(90, Math.min(220, Math.floor(w * h / 12000)));
    stars = Array.from({ length: count }, () => ({
      x: Math.random() * w, y: Math.random() * h,
      z: Math.random() * 0.8 + 0.2,
      r: Math.random() * 1.5 + 0.35,
      tw: Math.random() * Math.PI * 2,
      sp: 0.04 + Math.random() * 0.14,
    }));
    nebulas = [
      { x: w * 0.78, y: h * 0.12, r: Math.min(w, h) * 0.62, c: '77,163,255', a: 0.10 },
      { x: w * 0.12, y: h * 0.86, r: Math.min(w, h) * 0.55, c: '123,105,255', a: 0.09 },
      { x: w * 0.45, y: h * 0.45, r: Math.min(w, h) * 0.7, c: '30,90,180', a: 0.05 },
    ];
  }

  function draw(t) {
    ctx.clearRect(0, 0, w, h);
    // 星云
    for (const n of nebulas) {
      const dx = Math.sin(t / 9000 + n.x) * 22, dy = Math.cos(t / 11000 + n.y) * 16;
      const g = ctx.createRadialGradient(n.x + dx, n.y + dy, 0, n.x + dx, n.y + dy, n.r);
      g.addColorStop(0, `rgba(${n.c},${n.a})`);
      g.addColorStop(0.55, `rgba(${n.c},${n.a * 0.35})`);
      g.addColorStop(1, 'rgba(0,0,0,0)');
      ctx.fillStyle = g;
      ctx.fillRect(0, 0, w, h);
    }
    const px = (mouse.x - w / 2) / w * 18, py = (mouse.y - h / 2) / h * 12;
    for (const s of stars) {
      s.y -= s.sp * s.z;
      if (s.y < -4) { s.y = h + 4; s.x = Math.random() * w; }
      const tw = 0.55 + 0.45 * Math.sin(t / 700 + s.tw);
      const x = s.x + px * s.z, y = s.y + py * s.z;
      const r = s.r * (0.7 + tw * 0.5);
      ctx.beginPath();
      ctx.arc(x, y, r, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(${190 + 50 * s.z | 0},${215 + 30 * s.z | 0},255,${0.22 + tw * 0.6 * s.z})`;
      ctx.fill();
      if (s.z > 0.75 && tw > 1.2) {
        ctx.beginPath();
        ctx.arc(x, y, r * 3.2, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(150,200,255,0.05)`;
        ctx.fill();
      }
    }
    // 偶尔一颗流星
    if (!shooting && t - lastShot > 9000 + Math.random() * 9000) {
      shooting = { x: Math.random() * w * 0.7, y: Math.random() * h * 0.35, vx: 5.5 + Math.random() * 2, vy: 2.2 + Math.random(), life: 1 };
      lastShot = t;
    }
    if (shooting) {
      const s = shooting;
      const g = ctx.createLinearGradient(s.x, s.y, s.x - s.vx * 20, s.y - s.vy * 20);
      g.addColorStop(0, `rgba(190,230,255,${0.85 * s.life})`);
      g.addColorStop(1, 'rgba(120,180,255,0)');
      ctx.strokeStyle = g; ctx.lineWidth = 1.8; ctx.lineCap = 'round';
      ctx.beginPath(); ctx.moveTo(s.x, s.y); ctx.lineTo(s.x - s.vx * 20, s.y - s.vy * 20); ctx.stroke();
      s.x += s.vx; s.y += s.vy; s.life -= 0.012;
      if (s.life <= 0 || s.x > w || s.y > h) shooting = null;
    }
    requestAnimationFrame(draw);
  }

  window.addEventListener('resize', resize);
  window.addEventListener('mousemove', e => { mouse = { x: e.clientX, y: e.clientY }; });
  resize();
  requestAnimationFrame(draw);
}

/* ------------------------------------------------------------------ */
/* 启动                                                                */
/* ------------------------------------------------------------------ */
async function boot() {
  S.boot = await api('/api/bootstrap');
  paintEngine();
  paintProviders();
  paintProjectSelect();
  paintNavBadge();
  const slug = S.project?.slug || S.boot.projects[0]?.slug;
  if (slug) await openProject(slug, true);
  else render();
}

function paintEngine() {
  const p = S.boot?.providers || {};
  const llm = p.llm || p.deepseek || {};
  const dok = S.doctor;
  const dot = on => `<span class="dot ${on ? 'on' : 'off'}"></span>`;
  const comfyOk = dok?.comfyui?.ok;
  $('#engineCard').innerHTML = `
    <div>${dot(llm.enabled)}${esc(llm.label || 'LLM')} <b>${llm.enabled ? esc(llm.model) : '未配置'}</b></div>
    <div>${dot(p.minimax?.enabled)}MiniMax H3 <b>${p.minimax?.enabled ? esc(p.minimax.resolution) : '未配置'}</b></div>
    <div>${dot(comfyOk === true)}ComfyUI <b>${comfyOk === true ? '在线' : (p.comfyui?.enabled ? '未检测' : '关闭')}</b></div>`;
  $('#workspacePath').textContent = S.boot?.workspace || '';
  applyI18n($('#engineCard'));
}

function paintProviders() {
  const p = S.boot?.providers || {};
  const llm = p.llm || p.deepseek || {};
  const chips = [
    `<span class="chip ${llm.enabled ? 'ok' : 'mute'}">${icon('wand')}${llm.enabled
      ? esc(llm.label || 'LLM') + ' ' + t('编剧')
      : t('内置示例剧本')}</span>`,
    `<span class="chip ${p.minimax?.enabled ? 'ok' : 'mute'}">${icon('video')}${p.minimax?.enabled ? 'MiniMax H3' : t('本地占位片')}</span>`,
    `<span class="chip ${p.comfyui?.enabled ? '' : 'mute'}">${icon('image')}ComfyUI${p.comfyui?.lora ? ' · LoRA' : ''}</span>`,
  ];
  if (p.mock) chips.push(`<span class="chip warn">${icon('alert')}MOCK</span>`);
  $('#providerChips').innerHTML = chips.join('');
  applyI18n($('#providerChips'));
}

function paintProjectSelect() {
  const sel = $('#projectSelect');
  const list = S.boot?.projects || [];
  if (!list.length) { sel.innerHTML = '<option>暂无项目</option>'; sel.disabled = true; return; }
  sel.disabled = false;
  sel.innerHTML = list.map(p =>
    `<option value="${p.slug}" ${S.project?.slug === p.slug ? 'selected' : ''}>${esc(p.title)} · ${t('{n} 镜', { n: p.shots })} ${p.percent}%</option>`
  ).join('');
}

function paintNavBadge() {
  $('#navShots').textContent = S.project ? S.project.shots.length : 0;
}

async function openProject(slug, rerender = true) {
  S.project = await api(`/api/projects/${encodeURIComponent(slug)}`);
  S.selected = S.selected && S.project.shots.some(s => s.id === S.selected) ? S.selected : (S.project.shots[0]?.id || null);
  paintProjectSelect();
  paintNavBadge();
  if (rerender) render();
}

async function refreshProject(silent = true) {
  if (!S.project) return;
  try {
    await openProject(S.project.slug, false);
    if (!silent) render();
  } catch (e) { /* 项目可能被删了 */ }
}

/* ------------------------------------------------------------------ */
/* 视图路由                                                            */
/* ------------------------------------------------------------------ */
const VIEWS = {
  dashboard: renderDashboard, script: renderScript, storyboard: renderStoryboard,
  timeline: renderTimeline, export: renderExport, settings: renderSettings,
};

function render() {
  $$('.nav-item').forEach(n => n.classList.toggle('on', n.dataset.view === S.view));
  const view = $('#view');
  const p = S.project;
  if (!p && S.view !== 'settings') { view.innerHTML = emptyWorkspace(); applyI18n(view); return; }
  view.innerHTML = (VIEWS[S.view] || renderDashboard)();
  if (S.view === 'timeline') bindTimeline();
  if (S.view === 'export') bindExport();
  if (S.view === 'settings') {
    bindSettings();
    if (!S.doctor) loadDoctor();
  }
  AUTO.dirty = false;
  AUTO.sid = '';
  bindInspectorAutosave();
  applyI18n(view);
}

async function loadDoctor() {
  if (S.doctorLoading) return;
  S.doctorLoading = true;
  try {
    S.doctor = await api('/api/system/doctor');
    if (S.view === 'settings') render();
  } catch (e) {
    toast(e.message, 'err');
  } finally {
    S.doctorLoading = false;
  }
}

function emptyWorkspace() {
  return `<div class="panel" style="margin-top:40px">
    <div class="empty-state">
      ${icon('film')}
      <b>还没有项目</b>
      <p>写一句需求，DeepSeek 会写剧本、拆分镜，MiniMax H3 出镜头，最后自动剪成片。</p>
      <button class="btn primary" data-act="new-project">${icon('plus')}新建第一个项目</button>
    </div></div>`;
}

/* ------------------------------------------------------------------ */
/* 工作台                                                              */
/* ------------------------------------------------------------------ */
function renderDashboard() {
  const p = S.project;
  const pr = p.progress;
  const total = p.shots.length || 1;
  const kf = p.shots.filter(s => s.keyframe).length;
  const voice = p.shots.filter(s => s.audio_url).length;
  const hasOut = !!p.output_url;
  const cover = p.cover_url || '';
  const steps = [
    { icon: 'book', name: '剧本与分镜', desc: t('{n} 条镜头 · 目标 {sec}s', { n: p.shots.length, sec: p.script.target_duration }), done: p.shots.length > 0, act: 'script' },
    { icon: 'image', name: '关键帧渲染', desc: `ComfyUI + LoRA · ${kf}/${total}`, done: kf >= total, act: 'keyframes' },
    { icon: 'video', name: '镜头生成', desc: `MiniMax H3 · ${pr.rendered}/${total}`, done: pr.rendered >= total, act: 'clips' },
    { icon: 'mic', name: '配音合成', desc: `speech T2A · ${voice}/${total}`, done: voice >= total, act: 'voice' },
    { icon: 'film', name: '剪辑出片', desc: hasOut ? t('成片 {dur}', { dur: fmtDur(p.meta.final_duration || pr.duration) }) : '字幕 / BGM / 混音', done: hasOut, act: 'assemble' },
  ];
  const recents = (S.boot?.projects || []).slice(0, 6);

  return `
  <div class="hero">
    <div>
      <div class="chips">
        <span class="chip ok">${icon('check')}${p.mock ? 'MOCK 预览工程' : '生产工程'}</span>
        <span class="chip">${esc(p.aspect)} · ${esc(p.resolution)}</span>
        <span class="chip">${icon('clock')}${t('{n}s 素材', { n: pr.duration })}</span>
      </div>
      <h1>${esc(p.title)}</h1>
      <div class="logline">${esc(p.script.logline || p.brief || '')}</div>
      <div class="meta">
        <span class="chip">${icon('layers')}${t('{n} 镜', { n: p.shots.length })}</span>
        <span class="chip">${icon('check')}${t('已出片 {n}', { n: pr.rendered })}</span>
        <span class="chip">${icon('mic')}${t('已配音 {n}', { n: voice })}</span>
        ${hasOut ? `<span class="chip ok">${icon('film')}成片就绪</span>` : `<span class="chip warn">${icon('alert')}尚未合成</span>`}
        ${p.failures?.total ? `<span class="chip warn" data-act="retry-failed" style="cursor:pointer">${icon('refresh')}${t('重试 {n} 个失败', { n: p.failures.total })}</span>` : ''}
      </div>
      <div class="row">
        <button class="btn primary" data-act="run-all">${icon('sparkle')}${hasOut ? '重新出片' : '一键成片'}</button>
        <button class="btn" data-act="goto" data-view="timeline">${icon('scissors')}手动剪辑</button>
        <button class="btn ghost" data-act="goto" data-view="script">${icon('book')}看剧本</button>
      </div>
    </div>
    <div class="hero-preview">
      ${cover ? `<img src="${cover}" alt="cover">` : `<div class="empty-state" style="padding:0">${icon('image')}<p style="margin-top:10px">还没有关键帧</p></div>`}
      ${hasOut ? `<div class="play" data-act="play-output"><span>${icon('play')}</span></div>` : ''}
    </div>
  </div>

  <div class="kpis">
    <div class="kpi"><div class="k">镜头总数</div><div class="v">${p.shots.length}<small>条</small></div></div>
    <div class="kpi"><div class="k">已出片</div><div class="v">${pr.rendered}<small>/ ${p.shots.length}</small></div>
      <div class="bar"><i style="width:${Math.round(pr.rendered / total * 100)}%"></i></div></div>
    <div class="kpi"><div class="k">已配音</div><div class="v">${voice}<small>/ ${p.shots.length}</small></div>
      <div class="bar"><i style="width:${Math.round(voice / total * 100)}%"></i></div></div>
    <div class="kpi"><div class="k">素材时长</div><div class="v">${pr.duration}<small>秒</small></div></div>
    <div class="kpi"><div class="k">成片</div><div class="v">${hasOut ? fmtDur(p.meta.final_duration || 0) : '—'}</div>
      <div class="bar"><i style="width:${hasOut ? 100 : 0}%"></i></div></div>
  </div>

  <div class="grid-2">
    <div class="panel">
      <div class="panel-head">${icon('cpu')}<h3>生产流水线</h3><span class="sub">点任意环节可单独执行</span></div>
      <div class="steps">
        ${steps.map((s, i) => `
          <div class="step ${s.done ? 'done' : ''}" data-act="step" data-step="${s.act}">
            <div class="ico">${icon(s.done ? 'check' : s.icon)}</div>
            <div><b>${i + 1}. ${t(s.name)}</b><p>${s.desc}</p></div>
            <button class="btn sm ghost">${s.done ? '重跑' : '执行'}</button>
          </div>`).join('')}
      </div>
    </div>
    <div class="panel">
      <div class="panel-head">${icon('folder')}<h3>最近项目</h3><span class="sub">${t('{n} 个', { n: (S.boot?.projects || []).length })}</span></div>
      <div class="proj-list">
        ${recents.map(r => `
          <div class="proj" data-act="open-project" data-slug="${r.slug}">
            <div class="thumb">${r.cover_url ? `<img src="${r.cover_url}">` : ''}</div>
            <div><b>${esc(r.title)}</b><span>${t('{n} 镜 · {sec}s · {time}', {
              n: r.shots, sec: r.duration, time: r.updated_at.slice(5, 16).replace('T', ' ')})}</span></div>
            <div class="pct">${r.percent}%</div>
          </div>`).join('') || `<div class="hint">暂无其他项目</div>`}
      </div>
    </div>
  </div>`;
}

/* ------------------------------------------------------------------ */
/* 剧本                                                                */
/* ------------------------------------------------------------------ */
function renderScript() {
  const p = S.project, sc = p.script;
  return `
  <div class="script-grid">
    <div class="panel">
      <div class="panel-head">${icon('book')}<h3>剧本</h3><span class="sub">${esc(sc.genre || '')} · ${esc(sc.tone || '')}</span></div>
      <div class="field"><label>片名</label>
        <input class="input" id="scTitle" value="${esc(p.title)}"></div>
      <div class="field"><label>一句话故事</label>
        <input class="input" id="scLogline" value="${esc(sc.logline)}"></div>
      <div class="field"><label>故事梗概</label>
        <textarea class="textarea" id="scSynopsis" rows="5">${esc(sc.synopsis)}</textarea></div>
      <div class="field"><label>影像风格（英文提示词，会拼进每条镜头）</label>
        <textarea class="textarea mono" id="scStyle" rows="2">${esc(sc.style_prompt)}</textarea></div>
      <div class="row" style="margin-top:6px">
        <button class="btn primary" data-act="save-script">${icon('save')}保存剧本</button>
        <button class="btn ghost" data-act="goto" data-view="storyboard">${icon('layers')}去分镜</button>
        <span class="hint">改完记得保存，重新合成才会生效</span>
      </div>
    </div>
    <div>
      <div class="panel" style="margin-bottom:14px">
        <div class="panel-head">${icon('list')}<h3>节拍</h3><span class="sub">${t('{n} 段', { n: (sc.beats || []).length })}</span></div>
        ${(sc.beats || []).map(b => `
          <div class="beat">
            <span class="t">${esc(b.act || '段')}</span>
            <div><b>${esc(b.summary || '')}</b><p>${esc(b.emotion || '')}${b.duration ? ` · ${b.duration}s` : ''}</p></div>
          </div>`).join('') || '<div class="hint">没有节拍数据</div>'}
      </div>
      <div class="panel">
        <div class="panel-head">${icon('eye')}<h3>人物</h3><span class="sub">${t('{n} 位', { n: (sc.characters || []).length })}</span></div>
        ${(sc.characters || []).map(c => `
          <div class="char">
            <div class="av">${esc((c.name || '?').slice(0, 1))}</div>
            <div><b>${esc(c.name)}</b><p>${esc(c.look || '')}</p>
              ${c.look_en ? `<div class="hint mono" style="margin-top:4px">${esc(c.look_en)}</div>` : ''}</div>
          </div>`).join('') || '<div class="hint">无角色（纯空镜片）</div>'}
        <div class="divider"></div>
        <div class="hint">${t('配乐气质：{v}', { v: sc.music_mood || t('未指定') })}<br>${t('旁白音色：{v}', { v: sc.narrator || t('默认') })}</div>
      </div>
    </div>
  </div>`;
}

/* ------------------------------------------------------------------ */
/* 分镜                                                                */
/* ------------------------------------------------------------------ */
function renderStoryboard() {
  const p = S.project;
  if (!p.shots.length) return emptyShots();
  return `
  <div class="toolbar">
    <button class="btn sm" data-act="do" data-action="keyframes">${icon('image')}出关键帧</button>
    <button class="btn sm" data-act="do" data-action="clips">${icon('video')}生成镜头</button>
    <button class="btn sm" data-act="do" data-action="voice">${icon('mic')}生成配音</button>
    <button class="btn sm" data-act="do" data-action="render">${icon('sparkle')}全量渲染</button>
    ${p.failures?.total ? `<button class="btn sm" data-act="retry-failed">${icon('refresh')}${t('重试 {n} 个失败', { n: p.failures.total })}</button>` : ''}
    <button class="btn sm ghost" data-act="undo" title="${t('撤销')}">${icon('undo')}${t('撤销')}</button>
    <button class="btn sm ghost" data-act="redo" title="${t('重做')}">${icon('redo')}${t('重做')}</button>
    <div class="spacer"></div>
    <span class="hint">${t('共 {total} 条 · {rendered} 已出片 · 失败 {failed}', {
      total: p.shots.length, rendered: p.progress.rendered, failed: p.progress.failed })}</span>
    <button class="btn sm ghost" data-act="goto" data-view="timeline">${icon('scissors')}手动剪辑</button>
  </div>
  ${failurePanel(p)}
  <div class="shot-grid">
    ${p.shots.map(s => `
      <div class="shot" data-shot="${s.id}">
        <div class="media">
          ${s.keyframe_url ? `<img src="${s.keyframe_url}" loading="lazy">` : `<div class="empty">${icon('image')}</div>`}
          <span class="badge-id">${s.id}${s.enabled === false ? ' · ' + t('停用') : ''}</span>
          <span class="badge-dur">${s.duration}s</span>
        </div>
        <div class="body">
          <div class="row" style="justify-content:space-between">
            ${statusPill(s.status)}
            <span class="hint">${esc(s.camera || '')}</span>
          </div>
          <b>${esc(s.scene || s.description || '未命名镜头')}</b>
          <p>${esc(s.dialogue || '（无台词）')}</p>
          ${s.error ? `<p style="color:var(--danger)">${esc(s.error.slice(0, 90))}</p>` : ''}
        </div>
        <div class="foot">
          <button class="btn sm ghost" data-act="preview-shot" data-shot="${s.id}" ${s.clip_url ? '' : 'disabled'}>${icon('play')}预览</button>
          <button class="btn sm ghost" data-act="edit-shot" data-shot="${s.id}">${icon('edit')}编辑</button>
          <div class="spacer"></div>
          <button class="btn sm ghost" data-act="revise" data-shot="${s.id}" title="按意见重拍">${icon('wand')}重拍</button>
        </div>
      </div>`).join('')}
  </div>`;
}

function emptyShots() {
  return `<div class="panel"><div class="empty-state">${icon('layers')}
    <b>还没有分镜</b><p>剧本没拆出镜头，回剧本页检查一下，或重新生成项目。</p>
    <button class="btn" data-act="goto" data-view="script">${icon('book')}看剧本</button></div></div>`;
}

/* ------------------------------------------------------------------ */
/* 时间线                                                              */
/* ------------------------------------------------------------------ */
function renderTimeline() {
  const p = S.project;
  if (!p.shots.length) return emptyShots();
  const total = p.shots.filter(s => s.enabled !== false).reduce((a, s) => a + s.duration, 0) || 1;
  const sel = p.shots.find(s => s.id === S.selected) || p.shots[0];
  S.selected = sel?.id;
  return `
  <div class="tl-wrap">
    <div class="panel tight">
      <div class="panel-head">${icon('timeline')}<h3>时间线</h3>
        <span class="sub">${t('拖动排序 · 点选编辑 · 总时长 {sec}s', { sec: total })}</span>
        <div class="spacer"></div>
        <button class="btn sm ghost" data-act="undo" title="${t('撤销')}">${icon('undo')}</button>
        <button class="btn sm ghost" data-act="redo" title="${t('重做')}">${icon('redo')}</button>
        <button class="btn sm ghost" data-act="import-clip">${icon('upload')}导入素材</button>
      </div>
      <div class="row" style="margin-bottom:11px">
        <input class="input" id="aiCmd" placeholder="一句话改剪辑：全部调成黑白 · 第2镜1.2倍速 · 加叠化转场">
        <button class="btn sm primary" data-act="ai-edit">${icon('sparkle')}执行</button>
      </div>
      <div class="tl-scroll">
        <div class="tl-track" id="tlTrack">
          ${p.shots.map((s, i) => `
            <div class="tl-block ${s.enabled === false ? 'off' : ''} ${s.id === sel.id ? 'sel' : ''}"
                 data-id="${s.id}" data-idx="${i}" draggable="true"
                 style="flex:${Math.max(1, s.duration)} 0 0; min-width:96px" title="${esc(s.scene || s.id)}">
              ${s.keyframe_url ? `<img src="${s.keyframe_url}" loading="lazy">` : `<div class="none">${icon('image')}</div>`}
              <div class="cap"><b>${s.id} · ${s.duration}s${Math.abs(Number(s.speed || 1) - 1) > 0.01
                ? ' · ' + Number(s.speed).toFixed(2) + 'x' : ''}${s.transition && s.transition !== 'cut'
                ? ' · ' + s.transition : ''}</b><span>${esc((s.dialogue || s.scene || '').slice(0, 14))}</span></div>
            </div>`).join('')}
        </div>
        <div class="tl-scale"><span>0s</span><span>${Math.round(total / 2)}s</span><span>${total}s</span></div>
      </div>
      <div class="divider"></div>
      <div class="row">
        <button class="btn primary" data-act="do" data-action="assemble">${icon('film')}用当前剪辑合成</button>
        <button class="btn ghost" data-act="do" data-action="render">${icon('sparkle')}补渲缺失镜头</button>
        <span class="hint">停用的镜头不会进成片；trim 只影响成片，不改源文件</span>
      </div>
    </div>
    <div class="panel tight inspector" id="inspector" data-shot="${sel.id}">${inspectorHtml(sel, p)}</div>
  </div>`;
}

function inspectorHtml(s, p) {
  if (!s) return '<div class="hint">选一个镜头</div>';
  const meta = S.boot?.editing || {};
  const trans = meta.transitions?.length ? meta.transitions
    : [{ key: 'cut', label: '硬切' }, { key: 'fade', label: '淡入淡出' }, { key: 'dissolve', label: '叠化' }];
  const filters = meta.filters?.length ? meta.filters : [{ key: 'none', label: '原片' }];
  const positions = meta.title_positions?.length ? meta.title_positions
    : [{ key: 'top', label: '顶部' }, { key: 'center', label: '居中' }, { key: 'bottom', label: '底部' }];
  const maxTrim = Math.max(0, (s.clip_duration || s.duration) - s.duration);
  const sp = Number(s.speed || 1);
  const td = Number(s.transition_duration || 0);
  const slider = (id, label, min, max, step, val, suffix, dec) => `
    <div class="insp-row"><label>${label}</label>
      <div class="row">
        <input class="slider" type="range" id="${id}" min="${min}" max="${max}" step="${step}"
               value="${val}" data-out="${id}Val" data-suffix="${suffix}" data-dec="${dec || 0}">
        <span class="mono" id="${id}Val" style="width:46px;text-align:right">${
          Number(val).toFixed(dec || 0)}${suffix}</span>
      </div></div>`;

  return `
    <div class="panel-head">${icon('edit')}<h3>${s.id}</h3>${statusPill(s.status)}
      <div class="spacer"></div><span class="hint" id="autoSaveHint">${t('自动保存')}</span></div>
    <div class="shot-preview">
      ${s.clip_url ? `<video src="${s.clip_url}" muted preload="metadata" controls></video>`
        : s.keyframe_url ? `<img src="${s.keyframe_url}">` : `<div class="none" style="aspect-ratio:16/9;display:grid;place-items:center;color:#26405f">${icon('image')}</div>`}
    </div>

    <div class="sec-title">片段</div>
    ${slider('dDur', '时长', 1, 15, 1, s.duration, 's', 0)}
    <div class="insp-row"><label>入点</label>
      <div class="row">
        <input class="slider" type="range" id="dTrim" min="0" max="${maxTrim.toFixed(1)}" step="0.1"
               value="${s.trim_start || 0}" data-out="dTrimVal" data-suffix="s" data-dec="1">
        <span class="mono" id="dTrimVal" style="width:46px;text-align:right">${(s.trim_start || 0).toFixed(1)}s</span>
      </div></div>
    <div class="insp-row"><label>分割</label>
      <div class="row">
        <input class="input" id="dSplitAt" type="number" min="1" max="${Math.max(1, s.duration - 1)}"
               value="${Math.max(1, Math.floor(s.duration / 2))}" style="width:74px;padding:5px 8px">
        <button class="btn sm ghost" data-act="split-shot" data-shot="${s.id}">${icon('scissors')}在第 N 秒切开</button>
      </div></div>

    <div class="sec-title">变速</div>
    ${slider('dSpeed', '速度', 0.25, 4, 0.05, sp, 'x', 2)}
    <div class="preset-row" style="margin:-4px 0 8px">
      ${[0.5, 0.75, 1, 1.25, 1.5, 2].map(v =>
        `<span class="preset ${Math.abs(sp - v) < 0.01 ? 'on' : ''}" data-act="pick-speed" data-v="${v}">${v}x</span>`).join('')}
    </div>
    <div class="col-2">
      <label class="chk"><input type="checkbox" id="dMirror" ${s.mirror ? 'checked' : ''}> 镜像</label>
      <label class="chk"><input type="checkbox" id="dReverse" ${s.reverse ? 'checked' : ''}> 倒放</label>
    </div>

    <div class="sec-title">转场（与上一镜）</div>
    <div class="col-2">
      <div class="field"><label>类型</label><select class="select" id="dTrans">${trans.map(t =>
        `<option value="${t.key}" ${(s.transition || 'cut') === t.key ? 'selected' : ''}>${t.label}</option>`).join('')}
      </select></div>
      <div class="field"><label>时长</label>
        <input class="input" id="dTransDur" type="number" min="0.1" max="1" step="0.1"
               value="${td || 0.4}" ${s.transition === 'cut' ? 'disabled' : ''}></div>
    </div>

    <div class="sec-title">调色</div>
    <div class="field"><label>滤镜</label><select class="select" id="dFilter">${filters.map(f =>
      `<option value="${f.key}" ${(s.filter_preset || 'none') === f.key ? 'selected' : ''}>${f.label}</option>`).join('')}
    </select></div>
    ${slider('dBright', '亮度', -0.5, 0.5, 0.01, Number(s.brightness || 0), '', 2)}
    ${slider('dContrast', '对比度', 0.5, 2, 0.05, Number(s.contrast || 1), '', 2)}
    ${slider('dSat', '饱和度', 0, 2, 0.05, Number(s.saturation || 1), '', 2)}
    ${slider('dTemp', '色温', -1, 1, 0.05, Number(s.temperature || 0), '', 2)}

    <div class="sec-title">文字层</div>
    <div class="field"><input class="input" id="dTitle" value="${esc(s.title_text || '')}" placeholder="画面上叠加的文字，留空=不显示"></div>
    <div class="col-2">
      <div class="field"><label>位置</label><select class="select" id="dTitlePos">${positions.map(x =>
        `<option value="${x.key}" ${(s.title_position || 'bottom') === x.key ? 'selected' : ''}>${x.label}</option>`).join('')}
      </select></div>
      <div class="field"><label>字号（0=自动）</label><input class="input" id="dTitleSize" type="number" min="0" max="160" value="${s.title_size || 0}"></div>
    </div>

    <div class="sec-title">声音与字幕</div>
    ${slider('dVol', '音量', 0, 2, 0.05, Number(s.volume ?? 1), '', 2)}
    <div class="field"><label>字幕</label><textarea class="textarea" id="dSub" rows="2">${esc(s.subtitle || '')}</textarea></div>
    <div class="field"><label>台词 / 旁白</label><textarea class="textarea" id="dDlg" rows="2">${esc(s.dialogue || '')}</textarea></div>

    <label class="chk" style="margin:6px 0 10px"><input type="checkbox" id="dEnabled" ${s.enabled !== false ? 'checked' : ''}> 进成片</label>

    <details style="margin-bottom:10px">
      <summary class="hint" style="cursor:pointer">视频提示词 / 关键帧提示词</summary>
      <div class="field" style="margin-top:8px"><textarea class="textarea mono" id="dVp" rows="3">${esc(s.video_prompt || '')}</textarea></div>
      <div class="field"><textarea class="textarea mono" id="dIp" rows="3">${esc(s.image_prompt || '')}</textarea></div>
    </details>

    <div class="row" style="flex-wrap:wrap">
      <button class="btn sm primary" data-act="save-shot" data-shot="${s.id}">${icon('save')}保存</button>
      <button class="btn sm ghost" data-act="revise" data-shot="${s.id}">${icon('wand')}重拍</button>
      <button class="btn sm ghost" data-act="replace-clip" data-shot="${s.id}">${icon('upload')}换素材</button>
      <button class="btn sm ghost" data-act="move-shot" data-shot="${s.id}" data-dir="up">${icon('up')}</button>
      <button class="btn sm ghost" data-act="move-shot" data-shot="${s.id}" data-dir="down">${icon('down')}</button>
      <button class="btn sm ghost" data-act="dup-shot" data-shot="${s.id}">${icon('copy')}</button>
      <button class="btn sm danger" data-act="del-shot" data-shot="${s.id}">${icon('trash')}</button>
    </div>`;
}

function bindTimeline() {
  const track = $('#tlTrack');
  if (!track) return;
  let dragId = null;
  track.addEventListener('dragstart', e => {
    const b = e.target.closest('.tl-block'); if (!b) return;
    dragId = b.dataset.id; b.classList.add('dragging');
    e.dataTransfer.effectAllowed = 'move';
  });
  track.addEventListener('dragend', e => {
    dragId = null;
    $$('.tl-block').forEach(b => b.classList.remove('dragging', 'over'));
  });
  track.addEventListener('dragover', e => {
    e.preventDefault();
    const b = e.target.closest('.tl-block');
    $$('.tl-block').forEach(x => x.classList.toggle('over', x === b && x.dataset.id !== dragId));
  });
  track.addEventListener('drop', async e => {
    e.preventDefault();
    const target = e.target.closest('.tl-block');
    if (!target || !dragId || target.dataset.id === dragId) return;
    const ids = S.project.shots.map(s => s.id);
    const from = ids.indexOf(dragId), to = ids.indexOf(target.dataset.id);
    ids.splice(to, 0, ids.splice(from, 1)[0]);
    snapshotShots();
    try {
      await api(`/api/projects/${S.project.slug}/reorder`, { method: 'POST', body: { order: ids } });
      await openProject(S.project.slug, false);
      render();
      toast(t('顺序已更新'));
    } catch (err) { toast(err.message, 'err'); }
  });
  // 点选
  track.addEventListener('click', e => {
    const b = e.target.closest('.tl-block'); if (!b) return;
    S.selected = b.dataset.id;
    $$('.tl-block').forEach(x => x.classList.toggle('sel', x === b));
    const s = S.project.shots.find(x => x.id === S.selected);
    const box = $('#inspector');
    box.dataset.shot = S.selected;
    box.innerHTML = inspectorHtml(s, S.project);
    AUTO.dirty = false;
    AUTO.sid = S.selected;
    bindInspector();
  });
  bindInspector();
}

function bindInspector() {
  // 所有滑杆：拖动即时回显，后缀/小数位由 data-* 决定
  $$('#inspector input[type=range]').forEach(el => {
    const out = document.getElementById(el.dataset.out || '');
    if (!out) return;
    el.addEventListener('input', () => {
      const dec = Number(el.dataset.dec || 0);
      out.textContent = Number(el.value).toFixed(dec) + (el.dataset.suffix || '');
    });
  });
  // 硬切时转场时长没意义，置灰
  const trans = $('#dTrans'), td = $('#dTransDur');
  if (trans && td) {
    const sync = () => { td.disabled = trans.value === 'cut'; };
    trans.addEventListener('change', sync); sync();
  }
}

/* ------------------------------------------------------------------ */
/* 成片                                                                */
/* ------------------------------------------------------------------ */
function renderExport() {
  const p = S.project;
  const out = p.output_url;
  const dur = p.meta.final_duration || 0;
  const costs = p.costs || { total: 0, entries: [] };
  const budget = S.settings?.pipeline?.budget || 0;
  return `
  <div class="export-grid">
    <div class="panel">
      <div class="panel-head">${icon('film')}<h3>成片</h3>
        <span class="sub">${out ? `${fmtDur(dur)} · ${p.meta.final_size_mb || 0}MB` : '还没有合成'}</span>
        <div class="spacer"></div>
        <button class="btn sm primary" data-act="do" data-action="assemble">${icon('sparkle')}${out ? '重新合成' : '合成成片'}</button>
      </div>
      <div class="player-box">
        ${out ? `<video id="player" src="${out}" controls poster="${p.cover_url || ''}"></video>`
          : `<div class="player-empty"><div class="empty-state" style="padding:0">${icon('film')}<b>尚未合成</b><p>把镜头拼起来，烧字幕，混 BGM。</p></div></div>`}
      </div>
      <div class="dl-row">
        ${out ? `<a class="btn sm" href="${out}" download="${p.slug}.mp4">${icon('download')}下载 MP4</a>` : ''}
        ${p.srt_url ? `<a class="btn sm ghost" href="${p.srt_url}" download="subtitles.srt">${icon('download')}字幕 SRT</a>` : ''}
        <button class="btn sm ghost" data-act="goto" data-view="timeline">${icon('scissors')}回去改剪辑</button>
      </div>
      <div class="log-box" id="exportLog">${(S.jobLogs || []).slice(-12).map(l => esc(l)).join('\n') || '等待任务…'}</div>
    </div>
    <div>
      <div class="panel" style="margin-bottom:14px">
        <div class="panel-head">${icon('list')}<h3>交付信息</h3></div>
        <div class="insp-row"><label>项目</label><span>${esc(p.title)}</span></div>
        <div class="insp-row"><label>镜头</label><span>${t('{n} 条（含 {m} 条停用）', {
          n: p.shots.length, m: p.shots.filter(s => s.enabled === false).length })}</span></div>
        <div class="insp-row"><label>素材时长</label><span>${p.progress.duration}s</span></div>
        <div class="insp-row"><label>成片时长</label><span>${dur ? fmtDur(dur) : '—'}</span></div>
        <div class="insp-row"><label>分辨率</label><span>${esc(p.resolution)} @ ${p.fps}fps</span></div>
        <div class="insp-row"><label>字幕</label><span>${S.boot?.render?.subtitle ? '已烧录 + SRT' : '关闭'}</span></div>
        <div class="insp-row"><label>BGM</label><span>${p.bgm ? '已配置' : '未配置'}</span></div>
        <div class="divider"></div>
        <div class="field"><label>BGM 音频（可选）</label>
          <label class="btn sm ghost" style="width:100%">${icon('upload')}上传 BGM
            <input type="file" accept="audio/*" id="bgmFile" hidden></label>
          <div class="hint" style="margin-top:6px">${p.bgm ? esc(p.bgm.split(/[\\/]/).pop()) : t('支持 mp3 / wav / m4a')}</div>
        </div>
      </div>
      <div class="panel" style="margin-bottom:14px">
        <div class="panel-head">${icon('list')}<h3>成本小结</h3>
          <span class="sub">估算，非账单</span>
          <div class="spacer"></div>
          <button class="btn sm ghost" data-act="export-costs">${icon('download')}导出</button>
        </div>
        <div class="insp-row"><label>已发生</label><span>¥${Number(costs.total || 0).toFixed(2)}${budget ? ` / ${t('上限')} ¥${Number(budget).toFixed(2)}` : ''}</span></div>
        <div class="insp-row"><label>调用次数</label><span>${(costs.entries || []).length}</span></div>
        <div class="hint">${(costs.entries || []).slice(-4).map(e => `${esc(e.ts || '')} · ${esc(e.provider || '')} · ¥${Number(e.est || 0).toFixed(2)}`).join('<br>') || t('还没有付费调用；本地/mock 通道记 0 元')}</div>
      </div>
      <div class="panel">
        <div class="panel-head">${icon('terminal')}<h3>生成参数</h3></div>
        <div class="hint">
          ${t('剧本：')}${esc(S.boot?.providers?.llm?.label || S.boot?.providers?.deepseek?.label || '-')} · ${esc(S.boot?.providers?.llm?.model || S.boot?.providers?.deepseek?.model || '-')}<br>
          ${t('镜头：')}${esc(S.boot?.providers?.minimax?.model || '-')} · ${esc(S.boot?.providers?.minimax?.resolution || '-')}<br>
          ${t('关键帧：')}ComfyUI ${esc(S.boot?.providers?.comfyui?.workflow || '-')}<br>
          ${t('LoRA：')}${esc(S.boot?.providers?.comfyui?.lora || t('工作流默认'))}
        </div>
      </div>
    </div>
  </div>`;
}

function bindExport() {
  const f = $('#bgmFile');
  if (f) f.addEventListener('change', async () => {
    if (!f.files[0]) return;
    try {
      await fetch(`/api/projects/${S.project.slug}/bgm?filename=${encodeURIComponent(f.files[0].name)}`,
        { method: 'POST', body: f.files[0], headers: authHeaders() });
      toast(t('BGM 已上传'));
      await openProject(S.project.slug);
    } catch (e) { toast(e.message, 'err'); }
  });
}

/* ------------------------------------------------------------------ */
/* 设置                                                                */
/* ------------------------------------------------------------------ */
function renderSettings() {
  const st = S.settings;
  if (!st) return `<div class="panel"><div class="skeleton" style="height:220px"></div></div>`;
  const d = S.doctor || {};
  const sys = d.system || {};
  const sw = sys.webview2 || {};
  const ff = sys.ffmpeg || {};
  const hwChips = [
    ['系统', `${sys.os?.system || '-'} ${sys.os?.release || ''}`],
    ['CPU', `${sys.cpu?.cores || '-'} ${t('核')}`],
    ['内存', sys.ram_gb ? `${sys.ram_gb}G` : '-'],
    ['显卡', sys.gpu?.name ? `${sys.gpu.name} ${sys.gpu.vram_gb}G` : '无独显/未检测'],
    ['编码器', ff.usable?.join(' / ') || st.render.encoder || '-'],
    ['WebView2', sw.ok ? '就绪' : '缺失（用浏览器）'],
    ['中文字体', (sys.fonts?.cjk || []).slice(0, 2).join(' / ') + ((sys.fonts?.cjk || []).length > 2 ? ' 等' : '') || '未检测'],
    ['ComfyUI', sys.comfyui?.ok ? '在线' : '未连接'],
  ];
  const wfOptions = (S.workflows || []).map(f =>
    `<option value="${esc(f)}" ${st.minimax.video_workflow === f ? 'selected' : ''}>${esc(f)}</option>`).join('');
  const modes = [['auto', '自动（有 Key 走云，没有走本地）'], ['cloud', '云端 API'], ['local', '本地 / 兼容网关'], ['comfyui', '本地 ComfyUI 工作流'], ['mock', '占位片（不出真视频）']];

  const llm = st.llm || st.deepseek || {};
  const provs = llm.providers || [];
  const curProv = provs.find(p => p.id === llm.provider) || {};
  const provOpts = provs.map(p =>
    `<option value="${p.id}" ${llm.provider === p.id ? 'selected' : ''}>${p.label}</option>`).join('');
  const modelList = (curProv.models || llm.models || []).map(m =>
    `<option value="${esc(m)}"></option>`).join('');

  return `
  <div class="set-grid">
    <div class="panel">
      <div class="panel-head">${icon('key')}<h3>模型与接口</h3><span class="sub">密钥只写本机用户目录，不上传</span></div>

      <div class="prov">
        <div class="ico">${icon('robot')}</div>
        <div style="flex:1"><b>剧本大脑 · 多厂商</b>
          <p>Claude / ChatGPT / Gemini / Grok / GLM / Kimi / DeepSeek / 本地 Ollama</p></div>
        <div class="st"><span class="chip ${llm.enabled ? 'ok' : 'warn'}">${llm.enabled ? '已就绪' : '未配置'}</span></div>
      </div>
      <div class="field"><label>厂商</label>
        <select class="select" id="llmProvider">${provOpts}</select>
        <div class="hint" style="margin-top:5px">${esc(t(curProv.note || ''))} · ${t('协议')} <span class="mono">${esc(llm.protocol || '')}</span>
          ${curProv.docs ? ` · <a href="${esc(curProv.docs)}" target="_blank" rel="noreferrer" style="color:var(--accent-2)">${t('拿 Key')}</a>` : ''}</div>
      </div>
      <div class="col-2">
        <div class="field"><label>Base URL</label><input class="input" id="llmBase" value="${esc(llm.base_url || '')}"></div>
        <div class="field"><label>模型</label>
          <input class="input" id="llmModel" list="llmModels" value="${esc(llm.model || '')}">
          <datalist id="llmModels">${modelList}</datalist>
        </div>
      </div>
      <div class="field"><label>API Key（${esc(curProv.key_env || 'LLM_API_KEY')}）</label>
        <input class="input" id="llmKey" type="password" placeholder="留空=不修改${llm.api_key_set ? '（已存 ' + esc(llm.key_hint || '') + '）' : ''}"></div>
      <div class="row" style="margin-bottom:12px">
        <button class="btn sm ghost" data-act="llm-check">${icon('refresh')}测通道</button>
        <span id="llmCheck" class="hint"></span>
      </div>
      <div class="field"><label>来源</label>
        <select class="select" id="llmMode">
          <option value="auto" ${llm.mode === 'auto' ? 'selected' : ''}>自动</option>
          <option value="cloud" ${llm.mode === 'cloud' ? 'selected' : ''}>云端 API</option>
          <option value="local" ${llm.mode === 'local' ? 'selected' : ''}>本地 OpenAI 兼容</option>
        </select></div>
      <div class="hint">${t('本地示例：Ollama 选')} <span class="mono">Ollama · Local</span>${t('，模型填')} <span class="mono">qwen3:14b</span>${t('；vLLM / LM Studio 选「自定义」。')}</div>

      <div class="sec-title">视频生成</div>
      <div class="prov">
        <div class="ico">${icon('video')}</div>
        <div style="flex:1"><b>镜头来源</b>
          <p>${esc(st.minimax.mode)} · ${esc(st.minimax.base_url)}</p></div>
        <button class="btn sm ghost" data-act="video-check">${icon('refresh')}测通道</button>
      </div>
      <div class="field"><label>模式</label>
        <select class="select" id="mmMode">${modes.map(([v, n]) =>
          `<option value="${v}" ${st.minimax.mode === v ? 'selected' : ''}>${n}</option>`).join('')}</select></div>
      <div class="col-2">
        <div class="field"><label>Base URL</label><input class="input" id="mmBase" value="${esc(st.minimax.base_url)}" placeholder="http://127.0.0.1:8000"></div>
        <div class="field"><label>模型名</label><input class="input" id="mmModel" value="${esc(st.minimax.model)}"></div>
      </div>
      <div class="col-2">
        <div class="field"><label>分辨率</label>
          <select class="select" id="mmRes">
            <option value="768P" ${st.minimax.resolution === '768P' ? 'selected' : ''}>768P（快）</option>
            <option value="2K" ${st.minimax.resolution === '2K' ? 'selected' : ''}>2K（慢）</option>
          </select></div>
        <div class="field"><label>API Key（本地可不填）</label><input class="input" id="mmKey" type="password" placeholder="留空=不修改"></div>
      </div>
      <div class="field"><label>配音音色 voice_id</label><input class="input" id="mmVoice" value="${esc(st.minimax.voice_id)}"></div>
      <details style="margin:6px 0 10px">
        <summary class="hint" style="cursor:pointer">本地网关协议（默认按 MiniMax v2 对齐）</summary>
        <div class="col-2" style="margin-top:8px">
          <div class="field"><label>提交路径</label><input class="input mono" id="mmSubmit" value="${esc(st.minimax.submit_path)}"></div>
          <div class="field"><label>查询路径</label><input class="input mono" id="mmQuery" value="${esc(st.minimax.query_path)}"></div>
        </div>
        <div class="hint">${t('本地服务只要实现这两个接口即可：POST 提交返回 task_id，GET 查询返回 status + content.url。免 Key 也能跑。')}</div>
      </details>
      <div class="field"><label>ComfyUI 视频工作流（本地出片第二条路）</label>
        <select class="select" id="mmWorkflow">
          <option value="">（不使用）</option>
          ${wfOptions}
        </select>
        <div class="hint" style="margin-top:5px">${t('工作流放 workflows/ 目录；节点用标题标记 POSITIVE / NEGATIVE / LOAD_IMAGE，含 width/height 与 length 的节点会自动改分辨率与帧数。')}</div>
      </div>
      <div id="videoCheck" class="hint"></div>

      <div class="sec-title">关键帧（ComfyUI + LoRA）</div>
      <div class="prov">
        <div class="ico">${icon('image')}</div>
        <div style="flex:1"><b>图像工作流</b><p>${esc(st.comfy.url)} → ${esc(st.comfy.workflow)}</p></div>
        <button class="btn sm ghost" data-act="doctor">${icon('refresh')}检测</button>
      </div>
      <div class="col-2">
        <div class="field"><label>服务地址</label><input class="input" id="cfUrl" value="${esc(st.comfy.url)}"></div>
        <div class="field"><label>工作流文件</label><input class="input" id="cfWorkflow" value="${esc(st.comfy.workflow)}"></div>
      </div>
      <div class="col-2">
        <div class="field"><label>默认 LoRA</label><input class="input" id="cfLora" value="${esc(st.comfy.lora)}" placeholder="style_v3.safetensors"></div>
        <div class="field"><label>LoRA 权重</label><input class="input" id="cfLoraStr" type="number" step="0.05" min="0" max="2" value="${st.comfy.lora_strength}"></div>
      </div>
      ${d?.comfyui?.ok ? `<div class="hint">可用 LoRA（点选填入）：<br>${(d.comfyui.loras || []).slice(0, 18).map(l =>
        `<span class="tag" data-act="pick-lora" data-lora="${esc(l)}" style="cursor:pointer">${esc(l)}</span>`).join('') || '（目录为空）'}
        <br>Checkpoint：${(d.comfyui.checkpoints || []).slice(0, 6).map(esc).join(' / ') || '（未读到）'}</div>`
      : `<div class="hint">${d?.comfyui?.error ? t('检测失败：') + esc(d.comfyui.error) : t('点「检测」读取 ComfyUI 的模型与 LoRA 列表。')}</div>`}
    </div>

    <div>
      <div class="panel" style="margin-bottom:14px">
        <div class="panel-head">${icon('cpu')}<h3>系统自检</h3>
          <span class="sub">${d.first_run ? '尚未自动配置' : '已自动配置'}</span>
          <div class="spacer"></div>
          <button class="btn sm primary" data-act="autoconfig">${icon('sparkle')}${t('重新检测并应用')}</button>
        </div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:7px">
          ${hwChips.map(([k, v]) => `<div class="hint" style="display:flex;gap:7px;align-items:baseline;border-bottom:1px dashed var(--line);padding-bottom:5px;min-width:0">
            <span style="color:var(--dim);flex:0 0 58px">${k}</span>
            <span class="mono" style="color:var(--text);overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(v)}">${esc(v)}</span></div>`).join('')}
        </div>
        <div class="hint" style="margin-top:10px">首次启动会自动探测硬件、选编码器、定分辨率与并发；换机器后可重新跑一次。</div>
      </div>

      <div class="panel" style="margin-bottom:14px">
        <div class="panel-head">${icon('film')}<h3>成片规格</h3></div>
        <div class="col-2">
          <div class="field"><label>宽</label><input class="input" id="rdW" type="number" value="${st.render.width}"></div>
          <div class="field"><label>高</label><input class="input" id="rdH" type="number" value="${st.render.height}"></div>
        </div>
        <div class="col-2">
          <div class="field"><label>帧率</label><input class="input" id="rdFps" type="number" value="${st.render.fps}"></div>
          <div class="field"><label>编码器</label>
            <select class="select" id="rdEncoder">
              ${['auto', 'libx264', 'h264_nvenc', 'h264_qsv', 'h264_amf'].map(e =>
                `<option value="${e}" ${st.render.encoder === e ? 'selected' : ''}>${e}</option>`).join('')}
            </select></div>
        </div>
        <div class="col-2">
          <div class="field"><label>字幕字号</label><input class="input" id="rdSubSize" type="number" value="${st.render.subtitle_size}"></div>
          <div class="field"><label>中文字体</label><input class="input" id="rdFont" value="${esc(st.render.subtitle_font)}"></div>
        </div>
        <label class="chk"><input type="checkbox" id="rdSub" ${st.render.subtitle ? 'checked' : ''}> 烧录字幕</label>
        <div class="divider"></div>
        <label class="chk"><input type="checkbox" id="rdMock" ${st.pipeline.mock ? 'checked' : ''}> MOCK 模式（全部走占位素材）</label>
        <div class="field" style="margin-top:8px"><label>单项目预算上限（元，0=不限）</label>
          <input class="input" id="plBudget" type="number" min="0" step="0.01" value="${Number(st.pipeline.budget || 0)}">
          <div class="hint" style="margin-top:5px">超出后云端剧本 / 镜头 / 配音会被阻断，本地与 MOCK 通道记 0 元。</div>
        </div>
      </div>

      <div class="panel" style="margin-bottom:14px">
        <div class="panel-head">${icon('cloud')}<h3>运行环境</h3></div>
        <div class="hint">
          工作目录：<span class="mono">${esc(S.boot?.workspace || '')}</span><br>
          API Key 保存到：<span class="mono">${esc(st.security?.env_file || '')}</span><br>
          本地 API 防护：<span class="mono">${st.security?.api_guard ? 'token + Host 校验已开启' : '调试模式已关闭防护'}</span><br>
          ffmpeg：<span class="mono">${esc(ff.path || '检测中…')}</span><br>
          ${t('版本：')}v${esc(S.boot?.version || '')}
        </div>
        ${(d.recent_warnings || []).length ? `<details style="margin-top:8px"><summary class="hint" style="cursor:pointer">最近 ${d.recent_warnings.length} 条警告</summary><div class="log-box" style="margin-top:8px;max-height:150px">${d.recent_warnings.map(esc).join('\n')}</div></details>` : ''}
      </div>

      <div class="row">
        <button class="btn primary" data-act="save-settings">${icon('save')}保存设置</button>
        <button class="btn ghost" data-act="doctor">${icon('refresh')}重新检测</button>
      </div>
    </div>
  </div>`;
}

function bindSettings() {
  const sel = $('#llmProvider');
  if (!sel || sel.dataset.bound) return;
  sel.dataset.bound = '1';
  // 换厂商：Base URL / 模型 / 模型候选 跟着切，Key 不用重填（各家单独存）
  sel.addEventListener('change', () => {
    const llm = S.settings?.llm || {};
    const prov = (llm.providers || []).find(p => p.id === sel.value);
    if (!prov) return;
    if ($('#llmBase')) $('#llmBase').value = prov.base_url || '';
    if ($('#llmModel')) $('#llmModel').value = (prov.models || [])[0] || '';
    const dl = $('#llmModels');
    if (dl) dl.innerHTML = (prov.models || []).map(m => `<option value="${esc(m)}"></option>`).join('');
    const key = $('#llmKey');
    if (key) key.placeholder = `留空=不修改（${prov.key_env || 'LLM_API_KEY'}）`;
  });
}

/* ------------------------------------------------------------------ */
/* 弹窗                                                                */
/* ------------------------------------------------------------------ */
function openModal(html, cls = '') {
  const box = $('#modalBox');
  box.className = 'modal ' + cls;
  box.innerHTML = html;
  applyI18n(box);
  $('#modal').classList.add('on');
  const first = box.querySelector('input, textarea, select');
  if (first) setTimeout(() => first.focus(), 60);
}
function closeModal() { $('#modal').classList.remove('on'); }

function newProjectModal() {
  const llm = S.boot?.providers?.llm || S.boot?.providers?.deepseek || {};
  const mock = S.boot?.providers?.mock || !llm.enabled;
  openModal(`
    <div class="modal-head">${icon('sparkle')}<h2>新建 AI 影片</h2>
      <button class="icon-btn close" data-act="close-modal">${icon('close')}</button></div>
    <div class="field"><label>你想要一支什么样的片子？</label>
      <textarea class="textarea" id="npBrief" rows="4" placeholder="例：为高端咖啡品牌做一支 60 秒品牌片，雨夜城市，主角是加班后独自回家的女生，基调克制温暖，结尾落一句 slogan。">${''}</textarea>
      <div class="preset-row" style="margin-top:8px">
        ${['雨夜城市微叙事', '新品发布广告', '国风山水概念片', '科技产品演示', '人物纪录短片'].map(t =>
          `<span class="preset" data-act="preset-brief" data-text="${t}">${t}</span>`).join('')}
      </div>
    </div>
    <div class="col-2">
      <div class="field"><label>片名（可留空，让 AI 起）</label><input class="input" id="npTitle" placeholder="自动生成"></div>
      <div class="field"><label>时长</label>
        <div class="preset-row" id="npDurRow">
          ${[30, 60, 90, 120].map(d => `<span class="preset ${d === 60 ? 'on' : ''}" data-act="pick-dur" data-v="${d}">${d}s</span>`).join('')}
        </div>
      </div>
    </div>
    <div class="col-2">
      <div class="field"><label>画幅</label>
        <div class="preset-row" id="npAspectRow">
          ${[['16:9', '横屏'], ['9:16', '竖屏'], ['1:1', '方形']].map(([v, n], i) =>
            `<span class="preset ${i === 0 ? 'on' : ''}" data-act="pick-aspect" data-v="${v}">${n} ${v}</span>`).join('')}
        </div>
      </div>
      <div class="field"><label>影像风格</label>
        <div class="preset-row" id="npStyleRow">
          ${['电影感 35mm', '品牌广告质感', '纪录片手持', '赛博霓虹', '国风水墨'].map((s, i) =>
            `<span class="preset ${i === 0 ? 'on' : ''}" data-act="pick-style" data-v="${s}">${s}</span>`).join('')}
        </div>
      </div>
    </div>
    <div class="field"><label>镜头数提示（留空让 AI 按节奏决定）</label>
      <input class="input" id="npShots" type="number" min="3" max="24" placeholder="例：10"></div>
    <label class="chk" style="margin-top:4px"><input type="checkbox" id="npMock" ${mock ? 'checked' : ''}>
      MOCK 模式：不调用付费接口，用占位素材跑通全流程</label>
    <div class="hint" style="margin-top:8px">
      ${llm.enabled ? t('{name} 将撰写剧本与分镜。', { name: llm.label || llm.model })
        : t('未配置剧本模型，会使用内置示例分镜。')}
      ${S.boot?.providers?.minimax?.enabled ? '' : ' ' + t('未配置 MiniMax，镜头将用本地占位片。')}
    </div>
    <div class="modal-foot">
      <button class="btn ghost" data-act="close-modal">取消</button>
      <button class="btn primary" data-act="create-project">${icon('sparkle')}开始创作</button>
    </div>`, '');
}

function reviseModal(sid) {
  openModal(`
    <div class="modal-head">${icon('wand')}<h2>重拍 ${sid}</h2>
      <button class="icon-btn close" data-act="close-modal">${icon('close')}</button></div>
    <div class="field"><label>哪里不满意？</label>
      <textarea class="textarea" id="revNote" rows="3" placeholder="例：雨不够大，人物表情太笑，镜头再慢一点推近。"></textarea></div>
    <div class="hint">DeepSeek 会改写中英文提示词，再用新的 seed 重跑关键帧与镜头。</div>
    <div class="modal-foot">
      <button class="btn ghost" data-act="close-modal">取消</button>
      <button class="btn primary" data-act="do-revise" data-shot="${sid}">${icon('refresh')}重拍这一条</button>
    </div>`, 'sm');
}

function previewModal(sid) {
  const s = S.project.shots.find(x => x.id === sid);
  if (!s) return;
  openModal(`
    <div class="modal-head">${icon('play')}<h2>${s.id} · ${esc(s.scene || '')}</h2>
      <button class="icon-btn close" data-act="close-modal">${icon('close')}</button></div>
    ${s.clip_url ? `<video src="${s.clip_url}" controls autoplay style="width:100%;border-radius:12px;background:#000"></video>`
      : `<div class="hint">这条还没有视频。</div>`}
    <div class="hint" style="margin-top:10px">${esc(s.video_prompt || '')}</div>
    <div class="modal-foot"><button class="btn ghost" data-act="close-modal">关闭</button>
      <button class="btn" data-act="edit-shot" data-shot="${s.id}">${icon('edit')}去编辑</button></div>`, 'sm');
}

/* ------------------------------------------------------------------ */
/* 任务轮询                                                            */
/* ------------------------------------------------------------------ */
const JOB_LABEL = {
  plan: 'DeepSeek 正在写剧本与分镜', render: '全量渲染', all: '一键成片',
  voice: '生成配音', keyframes: '渲染关键帧', clips: '生成镜头', assemble: '剪辑合成', revise: '重拍镜头',
};

function showJob(jobId, title) {
  S.job = { id: jobId, logs: [], status: 'running' };
  S.jobLogs = [];
  S.jobSince = 0;
  $('#jobdock').classList.remove('hide');
  $('#jobTitle').textContent = title || t('任务执行中');
  $('#jobBar').style.width = '4%';
  $('#jobLogs').textContent = t('正在启动…');
  pollJob(jobId);
}

async function pollJob(jobId) {
  clearTimeout(S.poll);
  try {
    const j = await api(`/api/jobs/${jobId}?since=${S.jobSince}`);
    S.jobSince = j.log_total;
    S.job = j;
    const label = t(JOB_LABEL[j.kind] || j.kind);
    $('#jobTitle').textContent = `${label}${j.status === 'running' ? '…' : ''}`;
    $('#jobElapsed').textContent = fmtDur(j.elapsed);
    let pct = j.progress?.percent ?? 0;
    if (['assemble', 'plan', 'revise'].includes(j.kind)) {
      pct = j.status === 'done' ? 100 : Math.min(92, 5 + j.elapsed * 1.2);
    }
    $('#jobBar').style.width = `${j.status === 'done' ? 100 : Math.max(4, pct)}%`;
    if (j.logs?.length) {
      S.jobLogs = [...(S.jobLogs || []), ...j.logs].slice(-220);
      $('#jobLogs').textContent = S.jobLogs.slice(-3).join('\n');
      const box = $('#exportLog');
      if (box) { box.textContent = S.jobLogs.slice(-14).join('\n'); box.scrollTop = box.scrollHeight; }
    }
    if (j.status === 'done' || j.status === 'failed') {
      $('#jobBar').style.width = j.status === 'done' ? '100%' : '100%';
      $('#jobBar').style.background = j.status === 'failed'
        ? 'linear-gradient(90deg,#ff6b81,#ff9aa8)' : 'linear-gradient(90deg,#3ddc97,#6ee7ff)';
      if (j.status === 'done') {
        toast(t('{label}完成', { label }));
        if (j.kind === 'plan' && j.result?.slug) {
          await boot();
          await openProject(j.result.slug);
          S.view = 'storyboard'; render();
        } else {
          await refreshProject();
          render();
        }
        setTimeout(() => { if (S.job?.status === 'done') $('#jobdock').classList.add('hide'); }, 2600);
      } else {
        toast(j.error || t('{label}失败', { label }), 'err', 5200);
      }
      return;
    }
    S.poll = setTimeout(() => pollJob(jobId), 1400);
  } catch (e) {
    S.poll = setTimeout(() => pollJob(jobId), 2500);
  }
}

async function submitJob(url, body, title) {
  try {
    const r = await api(url, { method: 'POST', body });
    showJob(r.job, title);
  } catch (e) {
    toast(e.message, 'err');
  }
}

/* ------------------------------------------------------------------ */
/* 动作分发                                                            */
/* ------------------------------------------------------------------ */
async function doAction(action, force = false) {
  if (!S.project) return toast(t('先建个项目'), 'warn');
  const label = JOB_LABEL[action] || action;
  await submitJob(`/api/projects/${S.project.slug}/actions`, { action, force }, label);
}

document.addEventListener('click', async e => {
  const nav = e.target.closest('.nav-item');
  if (nav) { S.view = nav.dataset.view; render(); return; }

  const el = e.target.closest('[data-act]');
  if (!el) return;
  const act = el.dataset.act;

  if (act === 'goto') { S.view = el.dataset.view; render(); return; }
  if (act === 'refresh') { await boot(); toast(t('已刷新')); return; }
  if (act === 'new-project') { newProjectModal(); return; }
  if (act === 'close-modal') { closeModal(); return; }
  if (act === 'hide-job') { $('#jobdock').classList.add('hide'); return; }
  if (act === 'open-project') { await openProject(el.dataset.slug); return; }

  /* ---- 新建项目向导 ---- */
  if (act === 'preset-brief') {
    const ta = $('#npBrief');
    ta.value = (ta.value ? ta.value + '，' : '') + el.dataset.text;
    ta.focus(); return;
  }
  if (act === 'pick-dur' || act === 'pick-aspect' || act === 'pick-style') {
    const row = el.parentElement;
    $$('.preset', row).forEach(x => x.classList.remove('on'));
    el.classList.add('on'); return;
  }
  if (act === 'create-project') {
    const brief = $('#npBrief').value.trim();
    if (brief.length < 4) return toast(t('需求至少写一句完整的话'), 'warn');
    const dur = Number($('#npDurRow .preset.on')?.dataset.v || 60);
    const aspect = $('#npAspectRow .preset.on')?.dataset.v || '16:9';
    const style = $('#npStyleRow .preset.on')?.dataset.v || '';
    const payload = {
      brief, duration: dur, aspect, style,
      title: $('#npTitle').value.trim(),
      shot_hint: Number($('#npShots').value || 0),
      mock: $('#npMock').checked,
    };
    closeModal();
    await submitJob('/api/projects', payload, 'DeepSeek 正在写剧本');
    return;
  }

  /* ---- 生产 ---- */
  if (act === 'export-costs') {
    if (!S.project) return toast(t('先打开一个项目'), 'warn');
    try {
      const r = await api(`/api/projects/${S.project.slug}/costs`);
      const blob = new Blob([r.summary || ''], { type: 'text/plain;charset=utf-8' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = t('{slug}-成本小结.txt', { slug: S.project.slug });
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 1000);
      toast(t('成本小结已导出'));
    } catch (err) { toast(err.message, 'err'); }
    return;
  }
  if (act === 'run-all') { await doAction('all'); return; }
  if (act === 'do') { await doAction(el.dataset.action, false); return; }
  if (act === 'step') {
    const step = el.dataset.step;
    if (step === 'script') { S.view = 'script'; render(); return; }
    await doAction(step, false); return;
  }
  if (act === 'preview-shot') { previewModal(el.dataset.shot); return; }
  if (act === 'edit-shot') {
    S.selected = el.dataset.shot; closeModal(); S.view = 'timeline'; render(); return;
  }
  if (act === 'revise') { reviseModal(el.dataset.shot); return; }
  if (act === 'do-revise') {
    const note = $('#revNote').value.trim();
    const sid = el.dataset.shot;
    closeModal();
    await submitJob(`/api/projects/${S.project.slug}/shots/${sid}/revise`, { note }, `重拍 ${sid}`);
    return;
  }
  if (act === 'play-output') {
    if (!S.project.output_url) return toast('还没有成片', 'warn');
    openModal(`<div class="modal-head">${icon('play')}<h2>${esc(S.project.title)}</h2>
      <button class="icon-btn close" data-act="close-modal">${icon('close')}</button></div>
      <video src="${S.project.output_url}" controls autoplay style="width:100%;border-radius:12px;background:#000"></video>`, '');
    return;
  }

  /* ---- 手动剪辑 ---- */
  if (act === 'pick-speed') {
    const v = Number(el.dataset.v);
    const rng = $('#dSpeed');
    if (rng) { rng.value = v; rng.dispatchEvent(new Event('input')); }
    $$('.preset[data-act="pick-speed"]').forEach(x =>
      x.classList.toggle('on', Math.abs(Number(x.dataset.v) - v) < 0.01));
    return;
  }
  if (act === 'split-shot') {
    const at = Number($('#dSplitAt')?.value || 0);
    snapshotShots();
    try {
      const r = await api(`/api/projects/${S.project.slug}/shots/${el.dataset.shot}/split`,
        { method: 'POST', body: { at } });
      await openProject(S.project.slug, false);
      if (r.tail) S.selected = r.tail.id;
      render();
      toast(t('已切成 {head} + {tail}', { head: r.head.id, tail: r.tail.id }), 'ok');
    } catch (err) { toast(err.message, 'err'); }
    return;
  }
  if (act === 'ai-edit') {
    const text = $('#aiCmd')?.value.trim();
    if (!text) return toast(t('先写一句指令，例：全部调成黑白，第2镜1.2倍速'), 'warn');
    snapshotShots();
    el.disabled = true;
    el.innerHTML = `${icon('refresh', 'spin')}解析中`;
    try {
      const r = await api(`/api/projects/${S.project.slug}/edit-command`,
        { method: 'POST', body: { text } });
      await openProject(S.project.slug, false);
      render();
      toast(r.reply || (r.ok ? t('已应用') : t('没听懂')), r.ok ? 'ok' : 'warn', 4200);
    } catch (err) { toast(err.message, 'err'); }
    el.disabled = false;
    return;
  }
  if (act === 'undo') { await undoShots(); return; }
  if (act === 'redo') { await redoShots(); return; }
  if (act === 'retry-failed') {
    if (jobRunning()) return toast(t('任务运行中，先等它结束'), 'warn');
    snapshotShots();
    try {
      const r = await api(`/api/projects/${S.project.slug}/actions`,
        { method: 'POST', body: { action: 'render', only: 'failed', force: true } });
      showJob(r.job, t('重试失败镜头'));
    } catch (err) { toast(err.message, 'err'); }
    return;
  }
  if (act === 'save-shot') {
    const sid = el.dataset.shot;
    if (!AUTO.dirty) snapshotShots();
    try {
      await saveShotFields(sid);
      toast(t('镜头已保存，重新合成生效'));
    } catch (err) { toast(err.message, 'err'); }
    return;
  }
  if (act === 'move-shot') {
    snapshotShots();
    try {
      await api(`/api/projects/${S.project.slug}/shots/${el.dataset.shot}/move`,
        { method: 'POST', body: { direction: el.dataset.dir } });
      await openProject(S.project.slug, false); render();
    } catch (err) { toast(err.message, 'err'); }
    return;
  }
  if (act === 'dup-shot') {
    snapshotShots();
    try {
      await api(`/api/projects/${S.project.slug}/shots/${el.dataset.shot}/duplicate`, { method: 'POST' });
      await openProject(S.project.slug, false); render(); toast(t('已复制镜头'));
    } catch (err) { toast(err.message, 'err'); }
    return;
  }
  if (act === 'del-shot') {
    if (!confirm(t('删掉这条镜头？文件会留着，列表里不再出现。'))) return;
    snapshotShots();
    try {
      await api(`/api/projects/${S.project.slug}/shots/${el.dataset.shot}`, { method: 'DELETE' });
      S.selected = null;
      await openProject(S.project.slug, false); render(); toast(t('已删除'));
    } catch (err) { toast(err.message, 'err'); }
    return;
  }
  if (act === 'replace-clip' || act === 'import-clip') {
    const input = document.createElement('input');
    input.type = 'file'; input.accept = 'video/*';
    input.onchange = async () => {
      if (!input.files[0]) return;
      snapshotShots();
      const f = input.files[0];
      const url = act === 'import-clip'
        ? `/api/projects/${S.project.slug}/shots/import?filename=${encodeURIComponent(f.name)}`
        : `/api/projects/${S.project.slug}/shots/${el.dataset.shot}/replace?filename=${encodeURIComponent(f.name)}`;
      toast(t('正在上传素材…'), 'warn', 2000);
      const res = await fetch(url, { method: 'POST', body: f, headers: authHeaders() });
      if (!res.ok) return toast(t('上传失败'), 'err');
      await openProject(S.project.slug, false); render(); toast(t('素材已导入'));
    };
    input.click(); return;
  }

  /* ---- 剧本 ---- */
  if (act === 'save-script') {
    try {
      await api(`/api/projects/${S.project.slug}`, { method: 'PATCH', body: {
        title: $('#scTitle').value,
        script: {
          logline: $('#scLogline').value,
          synopsis: $('#scSynopsis').value,
          style_prompt: $('#scStyle').value,
        },
      }});
      await openProject(S.project.slug, false);
      toast(t('剧本已保存'));
    } catch (err) { toast(err.message, 'err'); }
    return;
  }

  /* ---- 设置 ---- */
  if (act === 'autoconfig') {
    el.disabled = true; el.innerHTML = `${icon('refresh', 'spin')}检测中`;
    try {
      const r = await api('/api/system/autoconfig', { method: 'POST', body: { force: true } });
      S.doctor = await api('/api/system/doctor');
      S.settings = await api('/api/settings');
      await boot();
      S.view = 'settings'; render();
      const enc = r.applied?.render?.encoder || 'libx264';
      toast(t('已按本机配置：{enc} · {w}x{h}', { enc, w: r.applied?.render?.width || 1920, h: r.applied?.render?.height || 1080 }), 'ok', 4200);
    } catch (err) { toast(err.message, 'err'); }
    return;
  }
  if (act === 'llm-check') {
    el.disabled = true;
    el.innerHTML = `${icon('refresh', 'spin')}${t('检测中…')}`;
    const box = $('#llmCheck');
    if (box) box.textContent = '';
    try {
      const r = await api('/api/system/llm-check', { method: 'POST', body: {
        provider: $('#llmProvider')?.value || '',
        model: $('#llmModel')?.value || '',
        base_url: $('#llmBase')?.value || '',
        api_key: $('#llmKey')?.value || '',
      }});
      if (box) {
        box.innerHTML = r.ok
          ? `${esc(r.label)} · ${esc(r.model)} · ${r.latency_ms}ms · "${esc(r.sample || '')}"`
          : `<span style="color:var(--danger)">${esc(r.error || '失败')}</span>`;
      }
      toast(r.ok ? t('通道正常') : t('通道不通'), r.ok ? 'ok' : 'err', 4200);
    } catch (err) {
      if (box) box.innerHTML = `<span style="color:var(--danger)">${esc(err.message)}</span>`;
      toast(err.message, 'err');
    }
    el.disabled = false;
    el.innerHTML = `${icon('refresh')}${t('测通道')}`;
    return;
  }
  if (act === 'video-check') {
    el.disabled = true;
    try {
      const r = await api('/api/system/video-check', { method: 'POST' });
      const box = $('#videoCheck');
      if (box) {
        const m = r.probe?.minimax;
        const cv = r.probe?.comfyui_video;
        box.innerHTML = `通道：${esc((r.chain || []).join(' → '))}` +
          (m ? `<br>网关 ${m.ok ? '在线' : '不可用'} ${esc(m.error || m.status || '')}` : '') +
          (cv ? `<br>工作流 ${cv.ok ? 'ComfyUI 在线' : 'ComfyUI 未连'} / ${cv.workflow_exists ? '文件存在' : '文件缺失'}` : '');
      }
      toast(t('通道探测完成'), 'ok');
    } catch (err) { toast(err.message, 'err'); }
    el.disabled = false;
    return;
  }
  if (act === 'doctor') {
    el.disabled = true;
    toast(t('正在探测 ComfyUI…'), 'warn', 2000);
    try {
      S.doctor = await api('/api/system/doctor');
      paintEngine();
      render();
      toast(S.doctor.comfyui?.ok ? t('ComfyUI 在线，{n} 个 LoRA', { n: S.doctor.comfyui.loras.length }) : t('ComfyUI 未连接'),
        S.doctor.comfyui?.ok ? 'ok' : 'warn');
    } catch (err) { toast(err.message, 'err'); }
    el.disabled = false;
    return;
  }
  if (act === 'pick-lora') { $('#cfLora').value = el.dataset.lora; toast(t('已填入 LoRA')); return; }
  if (act === 'save-settings') {
    const body = {
      llm: { provider: $('#llmProvider')?.value || 'deepseek',
             api_key: $('#llmKey')?.value || '',
             model: $('#llmModel')?.value || '',
             base_url: $('#llmBase')?.value || '',
             mode: $('#llmMode')?.value || 'auto' },
      minimax: { api_key: $('#mmKey').value, model: $('#mmModel').value,
                 resolution: $('#mmRes').value, voice_id: $('#mmVoice').value,
                 mode: $('#mmMode').value, base_url: $('#mmBase').value,
                 submit_path: $('#mmSubmit')?.value || '/v2/video_generation',
                 query_path: $('#mmQuery')?.value || '/v2/query/video_generation/{task_id}',
                 video_workflow: $('#mmWorkflow')?.value || '' },
      comfy: { url: $('#cfUrl').value, workflow: $('#cfWorkflow').value,
               lora: $('#cfLora').value, lora_strength: Number($('#cfLoraStr').value),
               width: Number($('#rdW').value) * 0.7 | 0, height: Number($('#rdH').value) * 0.7 | 0 },
      render: { width: Number($('#rdW').value), height: Number($('#rdH').value),
                fps: Number($('#rdFps').value), subtitle: $('#rdSub').checked,
                subtitle_font: $('#rdFont').value, subtitle_size: Number($('#rdSubSize').value),
                encoder: $('#rdEncoder').value },
      pipeline: { mock: $('#rdMock').checked, budget: Number($('#plBudget')?.value || 0) },
    };
    try {
      S.settings = (await api('/api/settings', { method: 'POST', body })).settings;
      await boot();
      S.view = 'settings'; render();
      toast(t('设置已保存'));
    } catch (err) { toast(err.message, 'err'); }
    return;
  }
});

document.addEventListener('change', async e => {
  if (e.target.id === 'projectSelect') {
    await openProject(e.target.value);
  }
});

$('#modal').addEventListener('click', e => {
  if (e.target.id === 'modal') closeModal();
});
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') closeModal();
});

/* ------------------------------------------------------------------ */
/* 入口                                                                */
/* ------------------------------------------------------------------ */
(async function main() {
  if (window.__i18nReady) { try { await window.__i18nReady; } catch (e) { /* 词库失败就用中文 */ } }
  initSky();
  try {
    await boot();
    S.settings = await api('/api/settings');
    S.workflows = (await api('/api/workflows')).files || [];
    // 支持 #settings / #timeline 这类深链，方便截图与排错
    const hash = (location.hash || '').replace('#', '');
    if (VIEWS[hash]) {
      S.view = hash;
      render();
    } else if (S.view === 'settings') {
      render();
    }
  } catch (e) {
    document.body.insertAdjacentHTML('beforeend',
      `<div style="position:fixed;inset:0;display:grid;place-items:center;z-index:99;color:#ffb3c0;font-size:14px">
        启动失败：${esc(e.message)}</div>`);
  }
})();
