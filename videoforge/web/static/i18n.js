/* VisionForge 多语言：9 种界面语言，源语言为简体中文。
   做法：词库以中文原文为 key（gettext 风格），渲染后统一替换文本节点与
   placeholder / title / aria-label，动态文案在 app.js 里用 t('…', {vars}) 取。 */

(function () {
  const LANGS = [
    { id: 'zh-CN', name: '简体中文', html: 'zh-CN' },
    { id: 'zh-TW', name: '繁體中文', html: 'zh-Hant' },
    { id: 'en', name: 'English', html: 'en' },
    { id: 'fr', name: 'Français', html: 'fr' },
    { id: 'de', name: 'Deutsch', html: 'de' },
    { id: 'ja', name: '日本語', html: 'ja' },
    { id: 'ko', name: '한국어', html: 'ko' },
    { id: 'es', name: 'Español', html: 'es' },
    { id: 'ar', name: 'العربية', html: 'ar' },
  ];

  const VF = window.VF_I18N = { lang: 'zh-CN', dict: {}, langs: LANGS };

  function pickLang() {
    // ?lang=en 可临时切语言（截图 / 分享 / 排查用），不写回本地
    const q = new URLSearchParams(location.search).get('lang');
    if (q && LANGS.some(l => l.id === q)) return q;
    const saved = localStorage.getItem('vf.lang');
    if (saved && LANGS.some(l => l.id === saved)) return saved;
    const nav = (navigator.languages && navigator.languages[0]) || navigator.language || 'zh-CN';
    const low = String(nav).toLowerCase();
    const exact = LANGS.find(l => l.id.toLowerCase() === low);
    if (exact) return exact.id;
    if (low.startsWith('zh')) {
      return /tw|hk|mo|hant/.test(low) ? 'zh-TW' : 'zh-CN';
    }
    const base = low.split('-')[0];
    const hit = LANGS.find(l => l.id === base);
    return hit ? hit.id : 'zh-CN';
  }

  async function loadDict(lang) {
    if (lang === 'zh-CN') return {};
    try {
      const res = await fetch(`/static/i18n/${lang}.json`, { cache: 'no-cache' });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      return await res.json();
    } catch (e) {
      console.warn('[i18n] 词库加载失败，回退中文：', e);
      return {};
    }
  }

  /** 取翻译：t('保存设置') / t('{n} 条镜头', {n: 3}) */
  window.t = function (src, vars) {
    if (src === undefined || src === null) return '';
    const table = VF.dict || {};
    let out = Object.prototype.hasOwnProperty.call(table, src) ? table[src] : src;
    if (vars) {
      out = String(out).replace(/\{(\w+)\}/g, (m, k) =>
        (vars[k] === undefined || vars[k] === null) ? m : String(vars[k]));
    }
    return out;
  };

  function translated(key) {
    const table = VF.dict || {};
    return Object.prototype.hasOwnProperty.call(table, key) ? table[key] : null;
  }

  const SKIP_TAGS = new Set(['SCRIPT', 'STYLE', 'TEXTAREA', 'CODE', 'PRE']);

  /** 把已渲染 DOM 里的中文原文替换成当前语言 */
  function applyI18n(root) {
    if (!VF.dict || VF.lang === 'zh-CN') return;
    const scope = root || document.body;
    if (!scope) return;

    const walker = document.createTreeWalker(scope, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        const tag = node.parentNode && node.parentNode.nodeName;
        if (SKIP_TAGS.has(tag)) return NodeFilter.FILTER_REJECT;
        if (node.parentNode && node.parentNode.closest && node.parentNode.closest('[data-no-i18n]')) {
          return NodeFilter.FILTER_REJECT;
        }
        return node.nodeValue && node.nodeValue.trim() ? NodeFilter.FILTER_ACCEPT
          : NodeFilter.FILTER_REJECT;
      },
    });
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    for (const node of nodes) {
      const raw = node.nodeValue;
      const key = raw.trim();
      const hit = translated(key);
      if (hit !== null) {
        node.nodeValue = raw.replace(key, hit);
      }
    }

    const ATTRS = ['placeholder', 'title', 'aria-label'];
    scope.querySelectorAll('[placeholder],[title],[aria-label]').forEach(el => {
      for (const attr of ATTRS) {
        const val = el.getAttribute(attr);
        if (!val) continue;
        const hit = translated(val.trim());
        if (hit !== null) el.setAttribute(attr, hit);
      }
    });
  }

  function syncChrome() {
    const meta = LANGS.find(l => l.id === VF.lang) || LANGS[0];
    document.documentElement.lang = meta.html;
    document.documentElement.dir = VF.lang === 'ar' ? 'rtl' : 'ltr';
    const title = translated('VisionForge · AI 视频编导台');
    if (title) document.title = title;
  }

  function buildSwitcher() {
    const slot = document.getElementById('langSlot');
    if (!slot || slot.dataset.ready) return;
    slot.dataset.ready = '1';
    const sel = document.createElement('select');
    sel.id = 'langSelect';
    sel.className = 'lang-select';
    sel.setAttribute('aria-label', 'Language');
    sel.innerHTML = LANGS.map(l =>
      `<option value="${l.id}" ${l.id === VF.lang ? 'selected' : ''}>${l.name}</option>`).join('');
    sel.addEventListener('change', () => {
      localStorage.setItem('vf.lang', sel.value);
      location.reload();
    });
    slot.appendChild(sel);
  }

  window.setLang = function (lang) {
    if (!LANGS.some(l => l.id === lang)) return;
    localStorage.setItem('vf.lang', lang);
    location.reload();
  };

  VF.lang = pickLang();
  syncChrome();

  window.__i18nReady = (async () => {
    VF.dict = await loadDict(VF.lang);
    syncChrome();
    buildSwitcher();
    applyI18n(document.body);
    return VF.lang;
  })();

  // 首次渲染后、以及任何动态插入的 DOM 都可以再调一次
  window.applyI18n = applyI18n;
})();
