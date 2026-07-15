/*
  Plain HTML/CSS/JS port of the uploaded sample's page transition.

  This keeps the sample's actual curtain algorithm:
  - same character set
  - same canvas sizing formula
  - same per-cell noise offsets
  - same 12-level glyph atlas
  - same cover/reveal timing
  - same reduced-motion fallback fade
*/

(() => {
  'use strict';

  const CHARS = '01<>[]{}()/\\|=+*#%&$@!?;:.~01ABCDEF0123456789';
  const DURATION = 720;
  const REDUCED_DURATION = 180;
  const FALLBACK_TIMEOUT = 6000;
  const PENDING_REVEAL_KEY = '__exact_ascii_transition_reveal__';

  const clamp01 = (value) => (value < 0 ? 0 : value > 1 ? 1 : value);

  const motionQuery = window.matchMedia('(prefers-reduced-motion: reduce)');
  let reducedMotion = motionQuery.matches;
  motionQuery.addEventListener?.('change', (event) => {
    reducedMotion = event.matches;
  });

  let canvas = null;
  let ctx = null;
  let metrics = null;
  let rafId = null;
  let phase = 'idle';
  let activeAnimation = null;
  let transitioning = false;

  function smoothstep(t) {
    return t * t * (3 - 2 * t);
  }

  function lerp(a, b, t) {
    return a + (b - a) * t;
  }

  function buildOffsets(cols, rows) {
    const base = new Float32Array(35);
    for (let i = 0; i < base.length; i += 1) base[i] = Math.random();

    const raw = new Float32Array(cols * rows);
    let min = Infinity;
    let max = -Infinity;

    for (let row = 0; row < rows; row += 1) {
      for (let col = 0; col < cols; col += 1) {
        const x = (col / cols) * 6;
        const y = (row / rows) * 4;
        const ix = Math.floor(x);
        const iy = Math.floor(y);
        const fx = smoothstep(x - ix);
        const fy = smoothstep(y - iy);
        const baseIndex = 7 * iy + ix;

        const top = lerp(base[baseIndex] ?? 0, base[baseIndex + 1] ?? 0, fx);
        const bottom = lerp(base[baseIndex + 7] ?? 0, base[baseIndex + 8] ?? 0, fx);
        const value = lerp(top, bottom, fy) + (Math.random() - 0.5) * 0.08;
        const index = row * cols + col;

        raw[index] = value;
        min = Math.min(min, value);
        max = Math.max(max, value);
      }
    }

    const range = max - min || 1;
    const offsets = new Float32Array(cols * rows);
    for (let i = 0; i < raw.length; i += 1) {
      offsets[i] = ((raw[i] - min) / range) * 0.88;
    }

    return offsets;
  }

  function createGlyphAtlas(color, cellW, cellH, fontSize, dpr) {
    const sourceCellW = Math.ceil(cellW * dpr);
    const sourceCellH = Math.ceil(cellH * dpr);
    const atlasCanvas = document.createElement('canvas');
    atlasCanvas.width = sourceCellW * CHARS.length;
    atlasCanvas.height = 12 * sourceCellH;

    const atlasCtx = atlasCanvas.getContext('2d');
    if (!atlasCtx) return null;

    atlasCtx.font = `${Math.round(fontSize * dpr)}px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace`;
    atlasCtx.textAlign = 'center';
    atlasCtx.textBaseline = 'middle';
    atlasCtx.fillStyle = color;

    for (let alpha = 0; alpha < 12; alpha += 1) {
      atlasCtx.globalAlpha = (alpha + 1) / 12;
      const y = alpha * sourceCellH + sourceCellH / 2;
      for (let charIndex = 0; charIndex < CHARS.length; charIndex += 1) {
        atlasCtx.fillText(CHARS[charIndex] ?? '0', charIndex * sourceCellW + sourceCellW / 2, y);
      }
    }

    atlasCtx.globalAlpha = 1;
    return { canvas: atlasCanvas, cellW: sourceCellW, cellH: sourceCellH };
  }

  function ensureCanvas() {
    if (canvas && ctx) return;

    canvas = document.querySelector('[data-ascii-curtain]');
    if (!canvas) {
      canvas = document.createElement('canvas');
      canvas.setAttribute('data-ascii-curtain', 'idle');
      canvas.setAttribute('aria-hidden', 'true');
      document.body.appendChild(canvas);
    }

    ctx = canvas.getContext('2d');
    metrics = resizeCanvas();

    window.addEventListener('resize', () => {
      if (!canvas || !ctx) return;
      metrics = resizeCanvas();
    });
  }

  function resizeCanvas() {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const width = window.innerWidth;
    const height = window.innerHeight;

    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    const cols = Math.max(1, Math.round(width / 12));
    const rows = Math.max(1, Math.round(height / 17));
    const cellW = width / cols;
    const cellH = height / rows;

    const styles = getComputedStyle(canvas);
    const bg = styles.getPropertyValue('--ascii-transition-bg').trim() || '#000000';
    const color = styles.getPropertyValue('--ascii-transition-color').trim() || '#ffffff';

    const count = cols * rows;
    const seeds = new Uint16Array(count);
    const flicker = new Float32Array(count);
    for (let i = 0; i < count; i += 1) {
      seeds[i] = Math.floor(65536 * Math.random());
      flicker[i] = 70 + 120 * Math.random();
    }

    return {
      cols,
      rows,
      cellW,
      cellH,
      width,
      height,
      bg,
      coverOffsets: buildOffsets(cols, rows),
      revealOffsets: buildOffsets(cols, rows),
      seeds,
      flicker,
      atlas: createGlyphAtlas(color, cellW, cellH, Math.round(0.86 * cellH), dpr),
    };
  }

  function drawReducedMotion(currentPhase, progress) {
    const alpha = currentPhase === 'cover' ? progress : 1 - progress;
    ctx.clearRect(0, 0, metrics.width, metrics.height);
    ctx.globalAlpha = alpha;
    ctx.fillStyle = metrics.bg;
    ctx.fillRect(0, 0, metrics.width, metrics.height);
    ctx.globalAlpha = 1;
  }

  function drawAscii(currentPhase, progress, now) {
    const {
      cols,
      rows,
      cellW,
      cellH,
      width,
      height,
      bg,
      coverOffsets,
      revealOffsets,
      seeds,
      flicker,
      atlas,
    } = metrics;

    const offsets = currentPhase === 'cover' ? coverOffsets : revealOffsets;

    ctx.clearRect(0, 0, width, height);
    ctx.globalAlpha = 1;
    ctx.fillStyle = bg;

    if (currentPhase === 'cover' && progress >= 1) {
      ctx.fillRect(0, 0, width, height);
    } else {
      ctx.beginPath();
      for (let row = 0; row < rows; row += 1) {
        for (let col = 0; col < cols; col += 1) {
          const index = row * cols + col;
          const cellProgress = clamp01((progress - (offsets[index] ?? 0)) * 8.333333333333334);
          const visible = currentPhase === 'cover' ? cellProgress : 1 - cellProgress;
          if (visible >= 0.35) {
            ctx.rect(
              Math.floor(col * cellW),
              Math.floor(row * cellH),
              Math.ceil(cellW) + 1,
              Math.ceil(cellH) + 1,
            );
          }
        }
      }
      ctx.fill();
    }

    if (!atlas) return;

    const sourceCellW = atlas.cellW;
    const sourceCellH = atlas.cellH;
    const charCount = CHARS.length;

    for (let row = 0; row < rows; row += 1) {
      for (let col = 0; col < cols; col += 1) {
        const index = row * cols + col;
        const cellProgress = clamp01((progress - (offsets[index] ?? 0)) * 8.333333333333334);
        const density = currentPhase === 'cover' ? cellProgress : 1 - cellProgress;
        if (density <= 0.02) continue;

        const seed = seeds[index] ?? 0;
        const tick = Math.floor((now + seed) / (flicker[index] || 100));
        const flash = (seed + tick) % 19 === 0;
        const wave = 0.35 + 0.5 * (0.5 + 0.5 * Math.sin(0.004 * now + seed));
        let alphaLevel = Math.floor(
          clamp01((cellProgress > 0 && cellProgress < 1) || flash ? 1 : wave) *
          clamp01(1.3 * density) *
          12,
        );

        if (alphaLevel <= 0) continue;
        if (alphaLevel >= 12) alphaLevel = 11;

        const charIndex = (seed + tick) % charCount;
        ctx.drawImage(
          atlas.canvas,
          charIndex * sourceCellW,
          alphaLevel * sourceCellH,
          sourceCellW,
          sourceCellH,
          col * cellW,
          row * cellH,
          cellW,
          cellH,
        );
      }
    }
  }

  function setPhase(nextPhase) {
    phase = nextPhase;
    ensureCanvas();

    if (nextPhase === 'idle') {
      canvas.setAttribute('data-ascii-curtain', 'idle');
      document.documentElement.removeAttribute('data-vt-loading');
      if (ctx && metrics) ctx.clearRect(0, 0, metrics.width, metrics.height);
      return;
    }

    canvas.setAttribute('data-ascii-curtain', nextPhase);
    document.documentElement.toggleAttribute('data-vt-loading', nextPhase === 'cover');
  }

  function animate(nextPhase) {
    ensureCanvas();

    if (activeAnimation) {
      cancelAnimationFrame(rafId);
      activeAnimation.resolve?.();
      activeAnimation = null;
    }

    setPhase(nextPhase);

    return new Promise((resolve) => {
      const startedAt = performance.now();
      let fired = false;
      const timeout = window.setTimeout(() => {
        if (fired) return;
        fired = true;
        resolve();
      }, FALLBACK_TIMEOUT);

      activeAnimation = { resolve };

      const frame = (now) => {
        if (!metrics) metrics = resizeCanvas();
        const duration = reducedMotion ? REDUCED_DURATION : DURATION;
        const progress = clamp01((now - startedAt) / duration);

        if (reducedMotion) drawReducedMotion(nextPhase, progress);
        else drawAscii(nextPhase, progress, now);

        if (progress >= 1) {
          window.clearTimeout(timeout);
          activeAnimation = null;
          if (!fired) {
            fired = true;
            resolve();
          }
          return;
        }

        if (phase !== 'idle') rafId = requestAnimationFrame(frame);
      };

      rafId = requestAnimationFrame(frame);
    });
  }

  function isExternalOrSpecial(url) {
    return url.origin !== window.location.origin || !/^https?:$/.test(url.protocol);
  }

  function shouldIgnoreClick(event, link) {
    if (!link) return true;
    if (event.defaultPrevented) return true;
    if (event.button !== 0) return true;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return true;
    if (link.target && link.target !== '_self') return true;
    if (link.hasAttribute('download')) return true;
    if (link.dataset.noTransition === 'true') return true;
    return false;
  }

  function classifyHref(href) {
    if (!href) return 'external';
    if (href.startsWith('#')) return 'anchor';

    let url;
    try {
      url = new URL(href, window.location.href);
    } catch {
      return 'external';
    }

    if (isExternalOrSpecial(url)) return 'external';

    const samePath = url.pathname === window.location.pathname;
    const sameSearch = url.search === window.location.search;

    if (samePath) {
      if (url.hash) return 'anchor';
      if (!sameSearch) return 'param';
      return 'reload';
    }

    if (url.searchParams.has('modal')) return 'param';
    return 'internal';
  }

  function findSwapTarget(documentObject) {
    return (
      documentObject.querySelector('#app') ||
      documentObject.querySelector('[data-page-root]') ||
      documentObject.querySelector('main') ||
      documentObject.body
    );
  }

  async function fetchAndSwap(url, pushHistory = true) {
    const response = await fetch(url.href, {
      method: 'GET',
      credentials: 'same-origin',
      headers: { 'X-Requested-With': 'ascii-page-transition' },
    });

    if (!response.ok) throw new Error(`Navigation failed: ${response.status}`);

    const html = await response.text();
    const nextDoc = new DOMParser().parseFromString(html, 'text/html');
    const currentTarget = findSwapTarget(document);
    const nextTarget = findSwapTarget(nextDoc);

    if (!currentTarget || !nextTarget) throw new Error('No swappable page target found.');

    document.title = nextDoc.title || document.title;

    if (document.body && nextDoc.body) {
      document.body.className = nextDoc.body.className;
      for (const attr of [...document.body.attributes]) {
        if (attr.name !== 'class') document.body.removeAttribute(attr.name);
      }
      for (const attr of [...nextDoc.body.attributes]) {
        if (attr.name !== 'class') document.body.setAttribute(attr.name, attr.value);
      }
    }

    currentTarget.replaceWith(nextTarget.cloneNode(true));

    if (pushHistory) history.pushState({ asciiTransition: true }, '', url.href);
    window.scrollTo({ top: 0, left: 0, behavior: 'instant' });
    window.dispatchEvent(new CustomEvent('ascii-page-transition:swap', { detail: { url: url.href } }));
  }

  async function goTo(href, options = {}) {
    if (transitioning) return;

    const url = new URL(href, window.location.href);
    transitioning = true;

    try {
      await animate('cover');
      await fetchAndSwap(url, options.pushHistory !== false);
      await animate('reveal');
      setPhase('idle');
    } catch (error) {
      sessionStorage.setItem(PENDING_REVEAL_KEY, '1');
      window.location.href = url.href;
    } finally {
      transitioning = false;
    }
  }

  function bindNavigation() {
    document.addEventListener('click', (event) => {
      const link = event.target.closest?.('a[href]');
      if (shouldIgnoreClick(event, link)) return;

      const href = link.getAttribute('href');
      const kind = classifyHref(href);

      if (kind === 'external') return;

      if (kind === 'reload') {
        event.preventDefault();
        window.scrollTo({ top: 0, behavior: 'smooth' });
        return;
      }

      if (kind === 'anchor' || kind === 'param') return;

      event.preventDefault();
      goTo(link.href);
    });

    window.addEventListener('popstate', () => {
      goTo(window.location.href, { pushHistory: false });
    });
  }

  async function revealAfterHardReload() {
    if (sessionStorage.getItem(PENDING_REVEAL_KEY) !== '1') return;
    sessionStorage.removeItem(PENDING_REVEAL_KEY);
    ensureCanvas();
    await animate('reveal');
    setPhase('idle');
  }

  function init() {
    ensureCanvas();
    bindNavigation();
    revealAfterHardReload();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init, { once: true });
  } else {
    init();
  }

  window.asciiPageTransition = {
    cover: () => animate('cover'),
    reveal: () => animate('reveal').then(() => setPhase('idle')),
    go: goTo,
  };
})();
