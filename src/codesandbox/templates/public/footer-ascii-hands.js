/* Compact adaptation of footer_ascii_rebuild/footer-ascii.js for a thin
   footer strip. Ported unmodified: POOLS, the seeded rand(), image->ASCII
   conversion (imageToAscii), and the hover glitch cells (setupHover) — same
   character pools, same seeded-random stability, same orange/black glitch
   swap. Adapted for our use: the reveal is a one-shot IntersectionObserver
   slide-in (CSS transition + .is-visible class) instead of the original's
   document-scroll-percentage-driven giant name reveal, and parallax is
   scoped + scaled down to the hovered bar itself instead of the whole
   page, since this sits in a 48px-tall footer divider, not a full-viewport
   section. */
(function () {
  'use strict';

  var POOLS = [
    ' ',
    '·.,',
    ':;`-~^',
    '=+<>?!:;',
    '|/\\()[]{}«»',
    '÷×±≈≠≤≥∞∑∏√∫',
    '¤†‡§¶©®™°¬',
    '%&#$@¥€£¢'
  ];

  var seed = 42;
  function rand() {
    seed = (seed * 16807 + 0) % 2147483647;
    return seed / 2147483647;
  }

  function esc(ch) {
    if (ch === '<') return '&lt;';
    if (ch === '>') return '&gt;';
    if (ch === '&') return '&amp;';
    return ch;
  }

  // The source PNGs have large transparent padding around the hand
  // artwork; rendering the full frame leaves tall blank rows of ASCII
  // space above/below the hand. Crop to the opaque content's bounding
  // box first so the rendered art has no dead space.
  function getContentBBox(img) {
    var full = document.createElement('canvas');
    full.width = img.width;
    full.height = img.height;
    var fctx = full.getContext('2d', { willReadFrequently: true });
    fctx.drawImage(img, 0, 0);
    var data = fctx.getImageData(0, 0, img.width, img.height).data;

    var minX = img.width, minY = img.height, maxX = 0, maxY = 0;
    var found = false;
    for (var y = 0; y < img.height; y++) {
      for (var x = 0; x < img.width; x++) {
        var a = data[(y * img.width + x) * 4 + 3];
        if (a >= 15) {
          found = true;
          if (x < minX) minX = x;
          if (x > maxX) maxX = x;
          if (y < minY) minY = y;
          if (y > maxY) maxY = y;
        }
      }
    }

    if (!found) return { x: 0, y: 0, width: img.width, height: img.height };
    return { x: minX, y: minY, width: maxX - minX + 1, height: maxY - minY + 1 };
  }

  function imageToAscii(img, cols) {
    seed = 42;

    var box = getContentBBox(img);
    var c = document.createElement('canvas');
    var ctx = c.getContext('2d', { willReadFrequently: true });
    var aspect = box.height / box.width;
    var rows = Math.max(1, Math.round(cols * aspect));

    c.width = cols;
    c.height = rows;
    ctx.clearRect(0, 0, cols, rows);
    ctx.drawImage(img, box.x, box.y, box.width, box.height, 0, 0, cols, rows);

    var data = ctx.getImageData(0, 0, cols, rows).data;
    var lines = [];
    var poolGrid = [];

    for (var y = 0; y < rows; y++) {
      var line = '';
      var poolRow = [];

      for (var x = 0; x < cols; x++) {
        var i = (y * cols + x) * 4;
        var r = data[i];
        var g = data[i + 1];
        var b = data[i + 2];
        var a = data[i + 3];

        if (a < 15) {
          line += ' ';
          poolRow.push(-1);
          continue;
        }

        var brightness = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
        brightness *= (a / 255);

        var pi = Math.floor(brightness * (POOLS.length - 1) * 0.8);
        pi = Math.min(pi, POOLS.length - 1);

        var pool = POOLS[pi];
        line += pool[Math.floor(rand() * pool.length)];
        poolRow.push(pi);
      }

      lines.push(line);
      poolGrid.push(poolRow);
    }

    return { text: lines.join('\n'), poolGrid: poolGrid };
  }

  function setupHover(preEl, poolGrid) {
    var origGrid = null;
    var radius = 2.2;
    var cols = poolGrid[0] ? poolGrid[0].length : 1;
    var rows = poolGrid.length;
    var noise = [];
    var hitTime = [];
    var cellDuration = [];
    var animating = false;

    for (var ny = 0; ny < rows; ny++) {
      var nr = [];
      var ht = [];
      var cd = [];
      for (var nx = 0; nx < cols; nx++) {
        var h = (Math.sin(nx * 12.9898 + ny * 78.233) * 43758.5453 % 1 + 1) % 1;
        nr.push(h * 4 - 2);
        ht.push(0);
        cd.push(h > 0.5 ? 180 : 90);
      }
      noise.push(nr);
      hitTime.push(ht);
      cellDuration.push(cd);
    }

    function init() {
      origGrid = preEl.textContent.split('\n').map(function (line) {
        return line.split('');
      });
    }

    function activate(e) {
      if (!origGrid) init();

      var rect = preEl.getBoundingClientRect();
      var charW = rect.width / cols;
      var charH = rect.height / rows;
      if (!charW || !charH) return;

      var mxC = (e.clientX - rect.left) / charW;
      var myC = (e.clientY - rect.top) / charH;

      var now = performance.now();
      var maxR = radius + 2;
      var yMin = Math.max(0, Math.floor(myC - maxR));
      var yMax = Math.min(rows - 1, Math.ceil(myC + maxR));
      var xMin = Math.max(0, Math.floor(mxC - maxR));
      var xMax = Math.min(cols - 1, Math.ceil(mxC + maxR));

      for (var y = yMin; y <= yMax; y++) {
        for (var x = xMin; x <= xMax; x++) {
          var dx = x - mxC;
          var dy = y - myC;
          var nRadius = radius + noise[y][x];
          if (dx * dx + dy * dy < nRadius * nRadius) {
            hitTime[y][x] = now;
          }
        }
      }

      if (!animating) {
        animating = true;
        tick();
      }
    }

    preEl.addEventListener('mousemove', activate);
    preEl.addEventListener('pointermove', activate);

    function tick() {
      var now = performance.now();
      var anyActive = false;
      var html = '';

      for (var y = 0; y < rows; y++) {
        for (var x = 0; x < cols; x++) {
          var pi = poolGrid[y][x];

          if (pi <= 0) {
            html += ' ';
            continue;
          }

          var elapsed = now - hitTime[y][x];
          if (hitTime[y][x] > 0 && elapsed < cellDuration[y][x]) {
            anyActive = true;
            var idx = (POOLS.length - 1) - pi;
            var pool = POOLS[idx];
            var ch = pool[Math.floor(Math.random() * pool.length)];
            html += '<span style="color:#0a0a0a;background:var(--footer-hand-accent,#ff3b14)">' + esc(ch) + '</span>';
          } else {
            html += esc(origGrid[y][x]);
          }
        }
        html += '\n';
      }

      preEl.innerHTML = html;

      if (anyActive) {
        requestAnimationFrame(tick);
      } else {
        animating = false;
        if (origGrid) preEl.textContent = origGrid.map(function (r) { return r.join(''); }).join('\n');
      }
    }
  }

  function loadAndRender(src, el, cols) {
    if (!el) return;
    var img = new Image();
    img.crossOrigin = 'anonymous';
    img.onload = function () {
      var result = imageToAscii(img, cols);
      el.textContent = result.text;
      setupHover(el, result.poolGrid);
    };
    img.onerror = function () {
      console.warn('[footer-ascii-hands] Failed to load image:', src);
    };
    img.src = src;
  }

  function setupReveal(root) {
    if (!('IntersectionObserver' in window)) {
      root.classList.add('is-visible');
      return;
    }
    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          root.classList.add('is-visible');
          observer.disconnect();
        }
      });
    }, { threshold: 0.4 });
    observer.observe(root);
  }

  // Parallax targets the inner <pre> elements, not the wrapper divs — the
  // wrappers own the CSS-class-driven slide-in transform (.is-visible), and
  // an inline style.transform on the same element would win over that
  // class's transform by specificity and freeze the reveal animation.
  function setupParallax(root, leftPre, rightPre) {
    if (!leftPre && !rightPre) return;
    var raf = null;
    var mx = 0, my = 0, sx = 0, sy = 0;

    root.addEventListener('mousemove', function (e) {
      var rect = root.getBoundingClientRect();
      mx = ((e.clientX - rect.left) / rect.width - 0.5) * 2;
      my = ((e.clientY - rect.top) / rect.height - 0.5) * 2;
      if (!raf) raf = requestAnimationFrame(loop);
    });
    root.addEventListener('mouseleave', function () {
      mx = 0; my = 0;
      if (!raf) raf = requestAnimationFrame(loop);
    });

    function loop() {
      raf = null;
      sx += (mx - sx) * 0.18;
      sy += (my - sy) * 0.18;
      var lx = sx * -4;
      var rx = sx * 4;
      var py = sy * -2;

      if (leftPre) leftPre.style.transform = 'translate(' + lx + 'px, ' + py + 'px)';
      if (rightPre) rightPre.style.transform = 'translate(' + rx + 'px, ' + py + 'px)';

      if (Math.abs(mx - sx) > 0.001 || Math.abs(my - sy) > 0.001) {
        raf = requestAnimationFrame(loop);
      }
    }
  }

  function init(root) {
    var leftSrc = root.getAttribute('data-left-src');
    var rightSrc = root.getAttribute('data-right-src');
    var cols = Number(root.getAttribute('data-cols')) || 90;

    var leftWrap = root.querySelector('.footer-ascii-hand-left');
    var rightWrap = root.querySelector('.footer-ascii-hand-right');
    var leftPre = leftWrap && leftWrap.querySelector('pre');
    var rightPre = rightWrap && rightWrap.querySelector('pre');

    if (leftSrc) loadAndRender(leftSrc, leftPre, cols);
    if (rightSrc) loadAndRender(rightSrc, rightPre, cols);

    setupReveal(root);
    setupParallax(root, leftPre, rightPre);
  }

  function initAll() {
    var roots = document.querySelectorAll('[data-footer-ascii-hands]');
    for (var i = 0; i < roots.length; i++) init(roots[i]);
  }

  window.FooterAsciiHands = { init: initAll, imageToAscii: imageToAscii, setupHover: setupHover };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initAll, { once: true });
  } else {
    initAll();
  }
})();
