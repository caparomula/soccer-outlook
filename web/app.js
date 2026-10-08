(function () {
  'use strict';
  var DAY_START = 4;                 // a day changes at 4 am local time, not midnight, so a late game stays with its evening (README)
  var WINDOW_DAYS = 3;               // the page shows today and the three days after it (story.py's WINDOW_DAYS)
  var FOCUS_MS = 24 * 60 * 60000;
  var LIVE_MS = 125 * 60000;
  var LS = { mode: 'ssg2-mode', have: 'ssg3-have', leagues: 'ssg5-leagues', compOff: 'ssg2-comp-off', paused: 'ssg1-league-paused', priority: 'ssg4-league-order', services: 'ssg4-service-order' };
  var app = document.getElementById('app');
  var body = document.getElementById('outlook-body');
  var rows = Array.prototype.slice.call(document.querySelectorAll('li.row'));
  if (!rows.length) return;
  var ROW_BY_ID = Object.create(null);
  rows.forEach(function (r) { ROW_BY_ID[r.getAttribute('data-id')] = r; });
  var controls = document.getElementById('controls');
  // A hash such as #at-20261003-2130 pins "now" (viewer-local) so a perspective can be previewed.
  function nowMs() { var m = /^#at-(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})$/.exec(location.hash || ''); return m ? new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]).getTime() : Date.now(); }
  // The Eastern calendar date (YYYYMMDD) of an instant. ESPN files a match under it (a 9 pm Eastern
  // kickoff is 01:00 UTC the next day), and a storyline is written for it, since the build runs on it.
  var fmtEtYmd = new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit' });
  function etYmd(k) { var p = {}; fmtEtYmd.formatToParts(new Date(k)).forEach(function (x) { p[x.type] = x.value; }); return p.year + p.month + p.day; }
  var picksEl = document.getElementById('picks'), missesEl = document.getElementById('misses');
  var SERVICES = { order: [], name: {}, owner: [] };
  try { SERVICES = JSON.parse(document.getElementById('service-meta').textContent) || SERVICES; } catch (e) {}
  var SERVICE_NAMES = SERVICES.name;
  // How a pick score is made (settings.toml, embedded by build.py), and whether an AI model takes part
  // at all: with AI off the page never asks for story.json, so no AI-written text or rating can appear.
  var SCORING = { blend: { ai: 70, outlook: 30, interest: 95, league_priority: 5 }, dots: [35, 45, 55, 68] };
  try { SCORING = JSON.parse(document.getElementById('scoring').textContent) || SCORING; } catch (e) {}
  if (!Array.isArray(SCORING.dots) || SCORING.dots.length !== 4) SCORING.dots = [35, 45, 55, 68];   // a page from before the dots
  var AI_ON = app.getAttribute('data-ai') !== 'off';
  function rankOf(id) { var i = serviceOrder().indexOf(id); return i < 0 ? 99 : i; }
  var SHORT = { cable: 'cable', ota: 'antenna', free: 'free app' };   // buckets where the channel leads
  function escHtml(t) { return String(t).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  // The schedule's sections: matches in progress, one per sports day of the window (WINDOW_DAYS + 1
  // of them: Today and Tomorrow, then the later days by their dates), and today's and yesterday's
  // results, folded.
  var DAYS = ['today', 'tomorrow', 'day2', 'day3'];
  var ORDER = ['live'].concat(DAYS, ['earlier', 'yesterday']);
  var TITLES = { live: 'Live now', today: 'Today', tomorrow: 'Tomorrow', earlier: 'Earlier today', yesterday: 'Yesterday' };
  var FOLDED = { earlier: true, yesterday: true };

  rows.forEach(function (r) {
    r._card = new MatchCard(r);
    r._k = Date.parse(r.getAttribute('data-utc'));
    r._tv = r.getAttribute('data-tv') === '1';
    r._svc = r.getAttribute('data-svc');
    r._lg = r.getAttribute('data-lg');
    r._score = parseInt(r.getAttribute('data-score'), 10) || 0;
    var outlook = parseFloat(r.getAttribute('data-outlook'));
    r._outlook = isFinite(outlook) ? outlook : null;   // the page's own score, from ESPN's data alone
    r._state = r.getAttribute('data-state');
    r._featured = r.getAttribute('data-featured') === '1';
    try { r._o = JSON.parse(r.getAttribute('data-o') || '[]'); } catch (e) { r._o = []; }
    r._unk = r._o.some(function (o) { return o.u; });   // a channel the page doesn't recognize
    try { r._r = r.hasAttribute('data-r') ? JSON.parse(r.getAttribute('data-r')) : null; } catch (e) { r._r = null; }
  });

  function read(key) { try { var v = localStorage.getItem(key); return v ? JSON.parse(v) : null; } catch (e) { return null; } }
  function write(key, v) { try { localStorage.setItem(key, JSON.stringify(v)); } catch (e) {} }

  // Inspect each visible logo once. Transparent padding does not count toward its brightness,
  // and marks with enough bright detail keep their original colors. ESPN's CDN permits CORS.
  var iconObserver = null, observedIcons = new WeakSet(), iconJobs = {}, iconQueue = [], iconActive = 0;
  var iconStyles = document.createElement('style'); document.head.appendChild(iconStyles);
  var linearChannel = Array.from({ length: 256 }, function (_, i) { var c = i / 255; return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); });
  function applyIconContrast(job, key) {
    if (job.boost && !job.styled.has(key)) {
      iconStyles.textContent += '.' + key + '{--icon-filter:var(--dark-icon-filter)}'; job.styled.add(key);
    }
  }
  function loadIconQueue() {
    while (iconActive < 4 && iconQueue.length) {
      (function (job) {
        iconActive++;
        var img = new Image(); img.crossOrigin = 'anonymous'; img.decoding = 'async';
        function done() { img.onload = img.onerror = null; iconActive--; loadIconQueue(); }
        img.onerror = done;
        img.onload = function () {
          try {
            var canvas = document.createElement('canvas'); canvas.width = canvas.height = 48;
            var ctx = canvas.getContext('2d', { willReadFrequently: true });
            ctx.drawImage(img, 0, 0, 48, 48);
            var pixels = ctx.getImageData(0, 0, 48, 48).data, total = 0, light = 0, bright = 0;
            for (var i = 0; i < pixels.length; i += 4) {
              var alpha = pixels[i + 3] / 255; if (alpha < 0.2) continue;
              var luminance = 0.2126 * linearChannel[pixels[i]] + 0.7152 * linearChannel[pixels[i + 1]] + 0.0722 * linearChannel[pixels[i + 2]];
              total += alpha; light += luminance * alpha; if (luminance >= 0.3) bright += alpha;
            }
            job.boost = total > 0 && light / total < 0.16 && bright / total < 0.25;
            job.keys.forEach(function (key) { applyIconContrast(job, key); });
          } catch (e) { /* An unavailable or non-CORS asset keeps its original appearance. */ }
          done();
        };
        img.src = job.url;
      })(iconQueue.shift());
    }
  }
  function inspectIcon(icon) {
    var key = Array.from(icon.classList).find(function (name) { return /^l-[A-Za-z0-9_-]+$/.test(name); });
    var match = /^url\(["']?(.*?)["']?\)$/.exec(getComputedStyle(icon).backgroundImage);
    if (!key || !match) return;
    var url = match[1], job = iconJobs[url];
    if (job) { job.keys.add(key); applyIconContrast(job, key); return; }
    job = { url: url, keys: new Set([key]), styled: new Set(), boost: false };
    iconJobs[url] = job; iconQueue.push(job); loadIconQueue();
  }
  function watchIcons() {
    if (!iconObserver && 'IntersectionObserver' in window) iconObserver = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) { if (entry.isIntersecting) { iconObserver.unobserve(entry.target); inspectIcon(entry.target); } });
    }, { rootMargin: '80px' });
    document.querySelectorAll('.lg, .logo:not(.logo--txt)').forEach(function (icon) {
      if (observedIcons.has(icon)) return;
      observedIcons.add(icon); if (iconObserver) iconObserver.observe(icon); else inspectIcon(icon);
    });
  }

  // ---- lineup and filters ----------------------------------------------------------------------
  // The page shows the matches the viewer's services carry. Until 8 October 2026 an "Everything" view
  // widened the schedule to every match; a choice of it saved then is cleared, not obeyed.
  try { localStorage.removeItem(LS.mode); } catch (e) {}
  var drawerOpen = false;
  var drawer = document.getElementById('drawer'), btnMenu = document.getElementById('btn-menu');
  var storedHave = read(LS.have);
  var HAVE = {};
  (Array.isArray(storedHave) ? storedHave : SERVICES.owner).forEach(function (k) { if (typeof k === 'string') HAVE[k] = true; });
  // Competitions: the viewer's explicit choices (true on, false off) are kept per league, and a league
  // without one follows its default. Saving only the leagues switched off made a default-off league
  // that had no fixtures when the viewer saved come back switched on. The earlier format, a list of
  // switched-off leagues, is read once as explicit offs. Anything malformed is ignored, not fatal.
  var DEFAULT_OFF = {};
  drawer.querySelectorAll('[data-kind="comp"][data-default-off="1"]').forEach(function (b) { DEFAULT_OFF[b.getAttribute('data-key')] = true; });
  var compChoice = {}, compOff = {};
  (function () {
    var saved = read(LS.leagues), old = read(LS.compOff);
    if (saved && typeof saved === 'object' && !Array.isArray(saved)) {
      [['on', true], ['off', false]].forEach(function (pair) {
        (Array.isArray(saved[pair[0]]) ? saved[pair[0]] : []).forEach(function (k) { if (typeof k === 'string') compChoice[k] = pair[1]; });
      });
    } else if (Array.isArray(old)) {
      old.forEach(function (k) { if (typeof k === 'string') compChoice[k] = false; });
    }
  })();
  function hasChoice(id) { return Object.prototype.hasOwnProperty.call(compChoice, id); }
  // A league is followed (enabled in the panel, and in the bar's strip) by the viewer's choice, else
  // by its default. A followed league can also be paused from the strip: hidden for now, still
  // followed, and still paused on the next visit until the viewer taps it again. Its matches are
  // hidden when it isn't followed or is paused. Following or unfollowing a league clears its pause, so
  // a league added back in the panel comes back shown.
  var paused = {};
  (function () {
    var saved = read(LS.paused);
    (Array.isArray(saved) ? saved : []).forEach(function (k) { if (typeof k === 'string') paused[k] = true; });
  })();
  function followed(id) { return hasChoice(id) ? compChoice[id] : !DEFAULT_OFF[id]; }
  function applyCompChoices() {
    compOff = {};
    drawer.querySelectorAll('[data-kind="comp"]').forEach(function (b) {
      var k = b.getAttribute('data-key');
      if (!followed(k) || paused[k]) compOff[k] = true;
    });
  }
  function saveCompChoices() {
    var on = [], off = [];
    Object.keys(compChoice).forEach(function (k) { (compChoice[k] ? on : off).push(k); });
    write(LS.leagues, { on: on, off: off });
    write(LS.paused, Object.keys(paused));
    try { localStorage.removeItem(LS.compOff); } catch (e) {}
  }
  applyCompChoices();
  // A match of a team build.py features (the US national teams) shows while its competition is off
  // by default; a viewer who switches the competition off, or pauses it, hides it too.
  function compHidden(r) { return !!compOff[r._lg] && (!r._featured || hasChoice(r._lg) || !!paused[r._lg]); }
  var storedPriority = read(LS.priority);
  var storedServiceOrder = read(LS.services);
  var leagueNames = SERVICES.leagues || {};
  var filterPills = {};
  ['have', 'comp'].forEach(function (kind) {
    filterPills[kind] = Array.from(drawer.querySelectorAll('[data-kind="' + kind + '"]'));
  });
  function serviceOrder() {
    var order = [];
    (Array.isArray(storedServiceOrder) ? storedServiceOrder : []).concat(SERVICES.order).forEach(function (id) {
      if (SERVICES.order.indexOf(id) >= 0 && order.indexOf(id) < 0) order.push(id);
    });
    return order;
  }
  function leagueOrder() {
    var base = Object.keys(leagueNames), order = [];
    var preferred = Array.isArray(storedPriority) ? storedPriority : (STORY && STORY.s && Array.isArray(STORY.s.league_order) ? STORY.s.league_order : []);
    preferred.concat(base).forEach(function (id) { if (base.indexOf(id) >= 0 && order.indexOf(id) < 0) order.push(id); });
    return order;
  }
  function renderLeagueOrder() {
    renderFilterGroups();
    var list = document.getElementById('league-order'), order = leagueOrder();
    if (list.getAttribute('data-order') === order.join(',')) return;
    list.setAttribute('data-order', order.join(',')); list.innerHTML = '';
    order.forEach(function (id, i) {
      var li = document.createElement('li'); li.setAttribute('data-league', id);
      var name = document.createElement('span'); name.className = 'league-priority__name'; name.textContent = (i + 1) + '. ' + leagueNames[id]; li.appendChild(name);
      [-1, 1].forEach(function (direction) {
        var b = document.createElement('button'); b.type = 'button'; b.className = 'fbtn';
        b.setAttribute('data-move-league', id); b.setAttribute('data-direction', direction);
        b.setAttribute('aria-label', 'Move ' + leagueNames[id] + (direction < 0 ? ' up' : ' down'));
        b.textContent = direction < 0 ? '↑' : '↓'; b.disabled = direction < 0 ? i === 0 : i === order.length - 1; li.appendChild(b);
      });
      list.appendChild(li);
    });
  }
  function filterEnabled(kind, id) { return kind === 'have' ? !!HAVE[id] : followed(id); }
  function setFilterEnabled(kind, id, enabled) {
    if (kind === 'have') { if (enabled) HAVE[id] = true; else delete HAVE[id]; }
    else { compChoice[id] = !!enabled; delete paused[id]; if (enabled) delete compOff[id]; else compOff[id] = true; }
  }
  function persistFilters(kind) {
    if (kind === 'have') { storedHave = Object.keys(HAVE); write(LS.have, storedHave); evaluateAll(); }
    else saveCompChoices();
  }
  function storeFilterOrder(kind, order) {
    if (kind === 'comp') { storedPriority = order; write(LS.priority, order); }
    else { storedServiceOrder = order; write(LS.services, order); }
  }
  function renderFilterGroups() {
    if (filterDrag && filterDrag.ghost) return;
    ['have', 'comp'].forEach(function (kind) {
      var order = kind === 'comp' ? leagueOrder() : serviceOrder(), names = kind === 'comp' ? leagueNames : SERVICE_NAMES;
      [true, false].forEach(function (enabled) {
        var host = document.getElementById(kind + (enabled ? '-enabled' : '-disabled'));
        var pills = filterPills[kind].filter(function (pill) { return filterEnabled(kind, pill.dataset.key) === enabled; });
        pills.sort(function (a, b) {
          return enabled ? order.indexOf(a.dataset.key) - order.indexOf(b.dataset.key) :
            names[a.dataset.key].localeCompare(names[b.dataset.key], 'en', { sensitivity: 'base' });
        });
        var signature = pills.map(function (pill) { return pill.dataset.key; }).join(',');
        if (host.getAttribute('data-order') !== signature) {
          pills.forEach(function (pill) { host.appendChild(pill); });
          host.setAttribute('data-order', signature);
        }
        host.parentNode.querySelector('.filter-area__empty').hidden = pills.length > 0;
      });
    });
  }
  function saveLeagueOrder(order) {
    storedPriority = order; write(LS.priority, order); applyFilterUI(); render(true);
  }
  // Touch starts at the grip so the panel can still scroll. Both groups accept drops even
  // when empty; only enabled groups have a user-defined order.
  var filterDrag = null, suppressFilterClick = false;
  function markFilterDrop(drag) {
    if (drag.target) drag.target.classList.remove('is-drop-target');
    if (drag.area) drag.area.classList.remove('is-drop-area');
    var hit = document.elementFromPoint(drag.x, drag.y);
    drag.area = hit && hit.closest('.filter-area');
    if (drag.area && drag.area.dataset.filterKind !== drag.kind) drag.area = null;
    drag.target = drag.area && hit.closest('.fpill');
    if (drag.area) drag.area.classList.add('is-drop-area');
    if (drag.target && drag.target !== drag.pill && drag.area.dataset.enabled === 'true') {
      drag.target.classList.add('is-drop-target');
      var rect = drag.target.getBoundingClientRect(); drag.after = drag.x >= rect.x + rect.width / 2;
    }
  }
  function scrollFilterDrag() {
    var drag = filterDrag;
    if (!drag || !drag.ghost) return;
    var panel = drawer.getBoundingClientRect(), speed = 0;
    if (drag.x >= panel.left && drag.x <= panel.right) {
      if (drag.y < panel.top + 44 && drag.y >= panel.top - 20) speed = -10;
      else if (drag.y > panel.bottom - 70 && drag.y <= panel.bottom + 20) speed = 10;
    }
    if (speed) { drawer.scrollTop += speed; markFilterDrop(drag); }
    drag.frame = requestAnimationFrame(scrollFilterDrag);
  }
  drawer.addEventListener('pointerdown', function (ev) {
    var pill = ev.target.closest('.filter-area .fpill');
    if (!pill || ev.button !== 0 || (ev.pointerType !== 'mouse' && !ev.target.closest('.fpill__grip'))) return;
    filterDrag = { pill: pill, kind: pill.dataset.kind, id: ev.pointerId, x: ev.clientX, y: ev.clientY, startX: ev.clientX, startY: ev.clientY, ghost: null, target: null, area: null };
  });
  drawer.addEventListener('pointermove', function (ev) {
    var drag = filterDrag;
    if (!drag || ev.pointerId !== drag.id) return;
    drag.x = ev.clientX; drag.y = ev.clientY;
    if (!drag.ghost) {
      if (Math.hypot(drag.x - drag.startX, drag.y - drag.startY) < 6) return;
      drag.pill.setPointerCapture(drag.id);
      var rect = drag.pill.getBoundingClientRect();
      drag.ghost = drag.pill.cloneNode(true); drag.ghost.classList.add('fpill--drag-ghost');
      drag.ghost.removeAttribute('id'); drag.ghost.setAttribute('aria-hidden', 'true'); drag.ghost.tabIndex = -1;
      drag.ghost.style.width = rect.width + 'px'; document.body.appendChild(drag.ghost);
      drag.pill.classList.add('is-dragging');
      drag.frame = requestAnimationFrame(scrollFilterDrag);
    }
    ev.preventDefault();
    drag.ghost.style.left = (ev.clientX - 18) + 'px'; drag.ghost.style.top = (ev.clientY - 16) + 'px';
    markFilterDrop(drag);
  });
  function finishFilterDrag(ev) {
    var drag = filterDrag;
    if (!drag || ev.pointerId !== drag.id) return;
    filterDrag = null;
    if (!drag.ghost) return;
    drag.ghost.remove(); drag.pill.classList.remove('is-dragging');
    cancelAnimationFrame(drag.frame);
    if (drag.target) drag.target.classList.remove('is-drop-target');
    if (drag.area) drag.area.classList.remove('is-drop-area');
    if (drag.pill.hasPointerCapture(drag.id)) drag.pill.releasePointerCapture(drag.id);
    suppressFilterClick = true; setTimeout(function () { suppressFilterClick = false; }, 0);
    if (ev.type === 'pointerup' && drag.area && drag.target !== drag.pill) {
      var enabled = drag.area.dataset.enabled === 'true', id = drag.pill.dataset.key;
      if (enabled) {
        var order = drag.kind === 'comp' ? leagueOrder() : serviceOrder();
        var siblings = Array.from(drag.area.querySelectorAll('.fpill')).filter(function (pill) { return pill !== drag.pill; });
        var target = drag.target || siblings[siblings.length - 1];
        order.splice(order.indexOf(id), 1);
        var index = target ? order.indexOf(target.dataset.key) + (drag.target ? (drag.after ? 1 : 0) : 1) : 0;
        order.splice(index, 0, id); storeFilterOrder(drag.kind, order);
      }
      setFilterEnabled(drag.kind, id, enabled); persistFilters(drag.kind);
      applyFilterUI(); render(true); drag.pill.focus({ preventScroll: true });
    }
  }
  drawer.addEventListener('pointerup', finishFilterDrag);
  drawer.addEventListener('pointercancel', finishFilterDrag);
  drawer.addEventListener('keydown', function (ev) {
    var pill = ev.target.closest('.fpill');
    if (!pill || !ev.altKey || (ev.key !== 'ArrowUp' && ev.key !== 'ArrowDown') || !filterEnabled(pill.dataset.kind, pill.dataset.key)) return;
    ev.preventDefault();
    var siblings = Array.from(pill.parentNode.querySelectorAll('.fpill')), index = siblings.indexOf(pill), direction = ev.key === 'ArrowUp' ? -1 : 1;
    var target = siblings[index + direction]; if (!target) return;
    var order = pill.dataset.kind === 'comp' ? leagueOrder() : serviceOrder();
    order.splice(order.indexOf(pill.dataset.key), 1);
    order.splice(order.indexOf(target.dataset.key) + (direction > 0 ? 1 : 0), 0, pill.dataset.key);
    storeFilterOrder(pill.dataset.kind, order); if (pill.dataset.kind === 'have') evaluateAll();
    applyFilterUI(); render(true); pill.focus({ preventScroll: true });
  });

  function firstHave(via) { var hits = via.filter(function (x) { return HAVE[x]; }); hits.sort(function (a, b) { return rankOf(a) - rankOf(b); }); return hits[0] || ''; }
  function chipFor(r) {
    if (SHORT[r._svc]) return '<span class="chip svc-' + r._svc + '"><i class="dot"></i>' + escHtml(r._outlet) + '<span class="chip__via">' + SHORT[r._svc] + '</span></span>';
    if (r._svc !== 'none') {
      var name = SERVICE_NAMES[r._svc] || r._svc;
      if (r._basis === 'rule') return '<span class="chip chip--rule svc-' + r._svc + '"><i class="dot"></i>' + escHtml(name) + '<span class="chip__via">usually</span></span>';
      var via = r._outlet && r._outlet !== name ? '<span class="chip__via">' + escHtml(r._outlet) + '</span>' : '';
      return '<span class="chip svc-' + r._svc + '"><i class="dot"></i>' + escHtml(name) + via + '</span>';
    }
    if (r._unk) return '<span class="chip chip--no chip--unk">Channel not recognized</span>';
    return r._o.length ? '<span class="chip chip--no">Not in your lineup</span>' : '<span class="chip chip--no chip--unk">Not listed yet</span>';
  }
  // Decide, for this viewer's lineup, which service carries each match, and restyle the row to match.
  function evaluateRow(r) {
    var best = null;
    r._o.forEach(function (o) {
      o.v.forEach(function (sid) {
        if (!HAVE[sid]) return;
        var key = rankOf(sid) * 4 + (o.l === SERVICE_NAMES[sid] ? 0 : 2) + (o.e ? 1 : 0);
        if (!best || key < best.key) best = { key: key, sid: sid, label: o.l };
      });
    });
    if (best) { r._svc = best.sid; r._basis = 'listed'; r._outlet = best.label; }
    else if (r._r && firstHave(r._r.v)) { r._svc = firstHave(r._r.v); r._basis = 'rule'; r._outlet = r._r.l; }
    else { r._svc = 'none'; r._basis = 'none'; r._outlet = ''; }
    r.classList.toggle('row--on', r._svc !== 'none'); r.classList.toggle('row--off', r._svc === 'none'); r.classList.toggle('row--rule', r._basis === 'rule');
    r.setAttribute('data-svc', r._svc); r.setAttribute('data-basis', r._basis); r.setAttribute('data-outlet', r._outlet);
    r.querySelectorAll('.pill[data-i]').forEach(function (p) {
      var o = r._o[+p.getAttribute('data-i')]; if (!o) return;
      var sid = firstHave(o.v);
      p.className = 'pill' + (o.u ? ' pill--unk' : sid ? ' pill--mine svc-' + sid : (o.f ? ' pill--free' : ''));
    });
    var rp = r.querySelector('.pill[data-rule]');
    if (rp && r._r) { var rs = firstHave(r._r.v); rp.className = 'pill pill--rule' + (rs ? ' svc-' + rs : ' pill--rule-off'); }
    var w = r.querySelector('.row__watch'); if (w) w.innerHTML = chipFor(r);
  }
  function evaluateAll() { rows.forEach(evaluateRow); }

  function filterSummary() {
    var n = Object.keys(HAVE).length, c = Object.keys(compOff).length, parts = [];
    parts.push(n + (n === 1 ? ' service' : ' services'));
    if (c) parts.push(c + (c === 1 ? ' competition hidden' : ' competitions hidden'));
    return parts.join(', ');
  }
  function applyFilterUI() {
    renderLeagueOrder();
    drawer.hidden = !drawerOpen; btnMenu.setAttribute('aria-expanded', String(drawerOpen));
    document.getElementById('filter-sum').textContent = filterSummary();
    drawer.querySelectorAll('.fpill').forEach(function (b) {
      var k = b.getAttribute('data-key'), comp = b.getAttribute('data-kind') === 'comp';
      var on = comp ? followed(k) : !!HAVE[k];
      b.setAttribute('aria-pressed', on ? 'true' : 'false');
      if (!comp) return;
      // An enabled league paused from the strip says so, in words for every reader.
      var tag = b.querySelector('.fpill__tag');
      if (!tag) { tag = document.createElement('span'); tag.className = 'fpill__tag'; tag.textContent = 'hidden for now'; b.appendChild(tag); }
      tag.hidden = !(on && paused[k]); b.classList.toggle('fpill--paused', !tag.hidden);
    });
    renderStrip();
  }
  function passes(r) {
    // The schedule promises availability: a match shows when one of the viewer's services carries it
    // (listed, or its usual home). One on other services shows under Elsewhere when a broadcaster is
    // listed; one with unconfirmed coverage doesn't show.
    if (!onSvc(r)) return false;
    if (compHidden(r)) return false;
    return true;
  }
  // The lineup panel: opened from the Lineup button, closed by it, by Done, by Escape or by a tap
  // outside. Opening moves focus into the panel; closing from the keyboard returns it to the button.
  function positionDrawer() {
    if (!drawerOpen) return;
    var rect = btnMenu.getBoundingClientRect(), width = Math.min(600, window.innerWidth - 32);
    var top = Math.max(8, Math.min(rect.bottom + 8, window.innerHeight - 180));
    var right = Math.min(Math.max(16, window.innerWidth - rect.right), window.innerWidth - width - 16);
    drawer.style.setProperty('--drawer-top', top + 'px'); drawer.style.setProperty('--drawer-right', right + 'px');
  }
  function setDrawer(open, refocus) {
    if (!open && filterDrag) finishFilterDrag({ pointerId: filterDrag.id, type: 'pointercancel' });
    drawerOpen = open; applyFilterUI();
    if (open) { positionDrawer(); drawer.scrollTop = 0; drawer.focus({ preventScroll: true }); }
    else if (refocus) btnMenu.focus({ preventScroll: true });
  }
  window.addEventListener('resize', positionDrawer);
  window.addEventListener('scroll', positionDrawer, { passive: true });
  // The controls bar stays at the top of the window. Keyboard focus that lands under it is scrolled
  // clear once the browser has scrolled it into view (WCAG 2.4.11). CSS scroll padding can't do
  // this: browsers count the stuck bar's own controls as hidden behind it too, and focusing one of
  // them far down the page threw the reader hundreds of pixels back up. Only a Tab moves the page:
  // focus put back after a redraw uses preventScroll and stays where the reader left it.
  var bar = document.getElementById('bar'), tabbing = false;
  // The bar is one line where it can be: Lineup, the league strip and the coffee link. The strip
  // needs room for a run of emblems (five, or all of them when they are fewer); to make it, Lineup
  // first drops its name and keeps its icon (tight: the styles say what that drops), and only where
  // even that leaves too little (a narrow phone, a large text size) does the strip take the line
  // below. Measured rather than set by width, since fonts, text size and the number of leagues
  // followed decide it.
  var STRIP_INLINE_PX = 190;
  function fitBar() {
    if (!bar) return;
    // Measured on an invisible copy of the bar, out of the page's flow, and the outcome applied to the
    // bar once: trying the strip on one line and then the other on the bar itself changed its height
    // in between, and with the page scrolled the browser's scroll anchoring followed those changes
    // and left the page shifted (switching the anchoring off while measuring didn't stop it).
    var probe = bar.cloneNode(true), width = bar.getBoundingClientRect().width;
    probe.removeAttribute('id'); probe.querySelectorAll('[id]').forEach(function (el) { el.removeAttribute('id'); });
    probe.setAttribute('aria-hidden', 'true'); probe.setAttribute('inert', '');
    probe.style.cssText = 'position:absolute;visibility:hidden;pointer-events:none;left:0;top:0;width:' + width + 'px';
    bar.parentNode.appendChild(probe);
    var items = [probe.querySelector('.menu-btn'), probe.querySelector('.coffee')], copy = probe.querySelector('.strip');
    var mid = function (el) { var r = el.getBoundingClientRect(); return r.top + r.height / 2; };
    var oneLine = function () { return Math.abs(mid(items[0]) - mid(items[1])) < 4; };
    var roomy = function () {
      if (!copy || copy.hidden) return true;
      var natural = copy.querySelector('.strip__list').scrollWidth;
      return copy.getBoundingClientRect().width + 1 >= Math.min(natural, STRIP_INLINE_PX);
    };
    var fits = function (tight, below) {
      probe.classList.toggle('bar--tight', tight); probe.classList.toggle('bar--strip-below', below);
      return oneLine() && (below || roomy());
    };
    var tight = false, below = false;
    if (!fits(false, false)) {
      tight = true;
      if (!fits(true, false)) { below = !!copy && !copy.hidden; tight = !fits(false, below); }
    }
    probe.remove();
    bar.classList.toggle('bar--tight', tight); bar.classList.toggle('bar--strip-below', below);
    fadeStrip();
  }

  // ---- the league strip ----------------------------------------------------------------------------
  // The leagues the viewer follows, in priority order, as emblems in the bar (a league ESPN has no
  // emblem for shows its short code): a tap pauses one, hiding its matches for now without unfollowing
  // it, and another shows them again, with a note that offers the way back. It is one tab stop, a
  // toolbar the arrow keys move along. Leagues are followed, unfollowed and ordered in the Lineup
  // panel; with none followed there is no strip.
  var strip = document.getElementById('league-strip'), stripList = document.getElementById('league-strip-list');
  var stripFocus = '', stripShape = '';
  function renderStrip() {
    if (!strip) return;
    var pills = {};
    filterPills.comp.forEach(function (pill) { pills[pill.dataset.key] = pill; });
    var ids = leagueOrder().filter(function (id) { return pills[id] && followed(id); });
    if (stripList.getAttribute('data-order') !== ids.join(',')) {
      stripList.setAttribute('data-order', ids.join(',')); stripList.innerHTML = '';
      ids.forEach(function (id) {
        var b = document.createElement('button'); b.type = 'button'; b.className = 'strip__lg';
        b.setAttribute('data-league', id); b.setAttribute('aria-label', leagueNames[id]); b.title = leagueNames[id];
        var mark = document.createElement('span'); mark.className = 'strip__mark'; mark.setAttribute('aria-hidden', 'true');
        var emblem = pills[id].querySelector('.lg');
        if (emblem) mark.appendChild(emblem.cloneNode(true));
        else { mark.classList.add('strip__code'); mark.textContent = pills[id].getAttribute('data-short') || leagueNames[id].slice(0, 3).toUpperCase(); }
        b.appendChild(mark); stripList.appendChild(b);
      });
      watchIcons();
    }
    if (ids.indexOf(stripFocus) < 0) stripFocus = ids[0] || '';
    Array.from(stripList.children).forEach(function (b) {
      var id = b.getAttribute('data-league');
      b.setAttribute('aria-pressed', paused[id] ? 'false' : 'true');
      b.tabIndex = id === stripFocus ? 0 : -1;
    });
    strip.hidden = !ids.length;
    // The bar is refitted when the strip's contents change, not on every redraw.
    var shape = strip.hidden + '|' + ids.join(',');
    if (shape !== stripShape) { stripShape = shape; fitBar(); }
  }
  // A fade at an edge of the row says more emblems lie beyond it.
  function fadeStrip() {
    if (!strip || strip.hidden) return;
    var left = stripList.scrollLeft, room = stripList.scrollWidth - stripList.clientWidth;
    stripList.classList.toggle('fade-l', left > 1);
    stripList.classList.toggle('fade-r', left < room - 1);
  }
  var stripStatus = document.getElementById('strip-status'), note = document.getElementById('strip-note');
  var noteText = document.getElementById('strip-note-txt'), noteTimer = 0, noteUndo = null;
  function hideNote() { clearTimeout(noteTimer); note.hidden = true; noteUndo = null; }
  function setPaused(id, hide) {
    if (hide) paused[id] = true; else delete paused[id];
    applyCompChoices(); saveCompChoices(); applyFilterUI(); render(true);
  }
  if (strip) {
    stripList.addEventListener('scroll', fadeStrip, { passive: true });
    stripList.addEventListener('click', function (ev) {
      var b = ev.target.closest('.strip__lg'); if (!b) return;
      var id = b.getAttribute('data-league'), hide = !paused[id];
      stripFocus = id; setPaused(id, hide);
      var text = leagueNames[id] + (hide ? ' hidden' : ' shown');
      stripStatus.textContent = text; noteText.textContent = text; note.hidden = false;
      noteUndo = { id: id, hide: !hide };
      clearTimeout(noteTimer); noteTimer = setTimeout(hideNote, 5000);
    });
    document.getElementById('strip-undo').addEventListener('click', function () {
      var undo = noteUndo; hideNote();
      if (!undo || !followed(undo.id)) return;
      setPaused(undo.id, undo.hide);
      stripStatus.textContent = leagueNames[undo.id] + (undo.hide ? ' hidden' : ' shown');
      var b = stripList.querySelector('[data-league="' + undo.id + '"]'); if (b) b.focus({ preventScroll: true });
    });
    stripList.addEventListener('keydown', function (ev) {
      var buttons = Array.from(stripList.children), i = buttons.indexOf(document.activeElement);
      var j = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: buttons.length - 1 }[ev.key];
      if (i < 0 || j === undefined) return;
      ev.preventDefault();
      j = Math.max(0, Math.min(buttons.length - 1, j));
      buttons[i].tabIndex = -1; buttons[j].tabIndex = 0; stripFocus = buttons[j].getAttribute('data-league'); buttons[j].focus();
    });
  }
  fitBar();
  window.addEventListener('resize', fitBar);
  if (document.fonts) { document.fonts.addEventListener('loadingdone', fitBar); document.fonts.ready.then(fitBar); }
  document.addEventListener('keydown', function (ev) {
    if (ev.key !== 'Tab') return;
    tabbing = true; setTimeout(function () { tabbing = false; }, 0);
  }, true);
  document.addEventListener('focusin', function (ev) {
    var el = ev.target;
    if (!tabbing || !bar || !el.closest || el.closest('.bar, #drawer, #match-preview, #match-dialog')) return;
    requestAnimationFrame(function () {
      if (document.activeElement !== el) return;
      var below = bar.getBoundingClientRect().bottom, rect = el.getBoundingClientRect();
      if (rect.height && rect.top < below + 4 && rect.bottom > 0) window.scrollBy(0, rect.top - below - 8);
    });
  });
  document.addEventListener('click', function (ev) {
    // The priority list can replace the clicked button before this event reaches document.
    // Its original event path still identifies it as a click inside the panel.
    var path = ev.composedPath();
    if (drawerOpen && path.indexOf(drawer) < 0 && path.indexOf(btnMenu) < 0) setDrawer(false, false);
  });
  document.addEventListener('keydown', function (ev) {
    if (drawerOpen && (ev.key === 'Escape' || ev.key === 'Esc')) { ev.preventDefault(); setDrawer(false, true); }
  });
  app.addEventListener('click', function (ev) {
    var b = ev.target.closest('button'); if (!b || !(b.closest('#controls') || b.closest('#drawer') || b === btnMenu)) return;
    if (b.hasAttribute('data-kind') && suppressFilterClick) { ev.preventDefault(); return; }
    if (b === btnMenu || b.id === 'btn-filters-close') { setDrawer(b === btnMenu ? !drawerOpen : false, b.id === 'btn-filters-close'); return; }
    if (b.id === 'btn-reset') {
      HAVE = {}; SERVICES.owner.forEach(function (k) { HAVE[k] = true; }); storedHave = null;
      compChoice = {}; paused = {}; applyCompChoices();
      storedPriority = null; storedServiceOrder = null;
      try { [LS.leagues, LS.compOff, LS.paused, LS.have, LS.priority, LS.services].forEach(function (k) { localStorage.removeItem(k); }); } catch (e) {}
      evaluateAll();
    }
    else if (b.hasAttribute('data-move-league')) {
      var order = leagueOrder(), index = order.indexOf(b.getAttribute('data-move-league')), direction = Number(b.getAttribute('data-direction'));
      var target = index + direction;
      if (index < 0 || target < 0 || target >= order.length) return;
      order.splice(target, 0, order.splice(index, 1)[0]);
      var focusLeague = b.getAttribute('data-move-league');
      saveLeagueOrder(order);
      // Stay on the button that was pressed, so pressing it again moves the league further; at the
      // end of the list, where it is disabled, take the other one.
      var moved = document.querySelector('#league-order [data-league="' + focusLeague + '"]');
      var same = moved.querySelector('[data-direction="' + direction + '"]');
      (same && !same.disabled ? same : moved.querySelector('button:not(:disabled)')).focus({ preventScroll: true });
      return;
    }
    else if (b.id === 'btn-clear' || b.id === 'btn-select-services') {
      HAVE = {};
      if (b.id === 'btn-select-services') SERVICES.order.forEach(function (k) { HAVE[k] = true; });
      storedHave = Object.keys(HAVE); write(LS.have, storedHave); evaluateAll();
    }
    else if (b.id === 'btn-clear-leagues' || b.id === 'btn-select-leagues') {
      drawer.querySelectorAll('[data-kind="comp"]').forEach(function (x) { compChoice[x.getAttribute('data-key')] = b.id === 'btn-select-leagues'; });
      paused = {}; applyCompChoices(); saveCompChoices();
    }
    else if (b.hasAttribute('data-kind')) { setFilterEnabled(b.dataset.kind, b.dataset.key, !filterEnabled(b.dataset.kind, b.dataset.key)); persistFilters(b.dataset.kind); }
    else return;
    applyFilterUI(); render(true);
    if (b.hasAttribute('data-kind')) b.focus({ preventScroll: true });
  });

  // ---- time helpers --------------------------------------------------------------------------
  var tz = null; try { tz = Intl.DateTimeFormat().resolvedOptions().timeZone; } catch (e) {}
  var showET = tz && tz !== 'America/New_York';
  var fmtTime = new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit' });
  var fmtET = new Intl.DateTimeFormat('en-US', { hour: 'numeric', minute: '2-digit', timeZone: 'America/New_York' });
  var fmtDay = new Intl.DateTimeFormat(undefined, { weekday: 'long', month: 'long', day: 'numeric' });
  var fmtShortDay = new Intl.DateTimeFormat(undefined, { weekday: 'short' });
  var fmtLongDay = new Intl.DateTimeFormat(undefined, { weekday: 'long' });
  var fmtMonthDay = new Intl.DateTimeFormat(undefined, { month: 'long', day: 'numeric' });
  function splitTime(d) {
    var parts = fmtTime.formatToParts(d), h = '', m = '', ap = '';
    parts.forEach(function (p) { if (p.type === 'hour') h = p.value; else if (p.type === 'minute') m = p.value; else if (p.type === 'dayPeriod') ap = p.value.toLowerCase(); });
    return { t: m ? h + ':' + m : h, ap: ap };
  }
  function sportsDayStart(d) { var s = new Date(d); s.setHours(DAY_START, 0, 0, 0); if (d < s) s.setDate(s.getDate() - 1); return s; }
  function dayIndex(k, now) { return Math.round((sportsDayStart(new Date(k)) - sportsDayStart(new Date(now))) / 86400000); }
  function inFocus(r, now) {
    return r._state !== 'post' && r._k < now + FOCUS_MS &&
      (r._k >= now || (r._k >= now - LIVE_MS && (r._state === 'in' || r._tv)) ||
       (r._state === 'in' && Date.now() - (r._seen || 0) < 10 * 60000));
  }
  // A row's section, or null when it is outside the page's window: today and the three days after it
  // (Thursday shows Thursday to Sunday), and yesterday's results.
  function bucketOf(r, now) {
    var idx = dayIndex(r._k, now);
    if (idx > WINDOW_DAYS) return null;
    // A live match can cross midnight or the 4 am sports-day boundary.
    if (inFocus(r, now) && r._tv && r._k <= now) return 'live';
    if (idx < 0) return idx === -1 ? 'yesterday' : null;
    if (idx === 0) {
      if (r._state === 'post') return 'earlier';
      if (r._state === 'in' && (now < r._k + LIVE_MS + 30 * 60000 || Date.now() - (r._seen || 0) < 10 * 60000)) return 'live';
      if (r._tv && now >= r._k && now < r._k + LIVE_MS) return 'live';
      if (r._tv && now >= r._k + LIVE_MS) return 'earlier';
    }
    return DAYS[idx];
  }
  // The start of the sports day `idx` days after now's, for its heading: a 1 am kickoff belongs to the
  // day before its calendar date, so the date comes from the day, not from its first match.
  function dayDate(idx, now) { var d = sportsDayStart(new Date(now)); d.setDate(d.getDate() + idx); return d; }

  // Write local kickoff times into the rows once (static markup carries Eastern time).
  rows.forEach(function (r) {
    if (!r._tv) return;
    var st = splitTime(new Date(r._k));
    var t = r.querySelector('[data-t]'), ap = r.querySelector('[data-ap]'), et = r.querySelector('.row__et');
    if (t) t.textContent = st.t; if (ap) ap.textContent = st.ap;
    if (showET && et) { et.textContent = fmtET.format(new Date(r._k)) + ' ET'; et.hidden = false; }
  });
  var tzName = tz || 'local time';
  try { tzName = new Intl.DateTimeFormat(undefined, { timeZoneName: 'short' }).formatToParts(new Date()).filter(function (p) { return p.type === 'timeZoneName'; })[0].value; } catch (e) {}
  document.getElementById('fresh').textContent = document.getElementById('fresh').textContent.replace('Times shown in Eastern.', showET ? 'Times shown in ' + tzName + ', with Eastern underneath.' : 'Times shown in Eastern.');

  // ---- rendering -----------------------------------------------------------------------------
  // Re-rendering moves rows and rebuilds cards, the overview and the details preview; a focused
  // element that is moved or replaced drops focus to the page, so a keyboard user lost their place
  // every minute. Note the focused element and where it lives, run the update, then put focus back
  // on it, or on its counterpart: same kind of element, label and link, in the rebuilt card for
  // the same match and role, or in the same section.
  function keepFocus(update) {
    var el = document.activeElement;
    if (!el || el === document.body || !el.isConnected) return update();
    var host = el.closest('[data-match-role]'), scope = el.closest('#story, #match-preview, #schedule-summary, #lineup, #misses');
    var key = { role: host && host.dataset.matchRole, id: host && (host.dataset.matchId || host.getAttribute('data-id')),
                scope: !host && scope ? scope.id : '', tag: el.tagName, cls: el.className, href: el.getAttribute('href'), text: el.textContent };
    var result = update();
    if (el.isConnected && el.getClientRects().length) {
      if (document.activeElement !== el) el.focus({ preventScroll: true });
      return result;
    }
    var places = key.role ? Array.from(document.querySelectorAll('[data-match-role="' + key.role + '"]')).filter(function (h) {
      return (h.dataset.matchId || h.getAttribute('data-id')) === key.id;
    }) : key.scope ? [document.getElementById(key.scope)] : [];
    for (var i = 0; i < places.length; i++) {
      var twin = Array.from(places[i].querySelectorAll(key.tag)).find(function (c) {
        return c.className === key.cls && c.getAttribute('href') === key.href && c.textContent === key.text && c.getClientRects().length;
      });
      if (twin) { twin.focus({ preventScroll: true }); break; }
    }
    return result;
  }
  var lastSig = '', foldContext = '', foldChoices = {};
  // Everything a redraw changes, from which rows are shown to the rebuilt sections, happens inside
  // keepPlace, so the reader's place is measured before any of it moves.
  function render(force) {
    if (keepPlace(function () { return draw(force); })) refreshDetailPreview();
  }
  function draw(force) {
    var now = nowMs();
    renderLeagueOrder();
    var groups = {}; ORDER.forEach(function (b) { groups[b] = []; });
    var sig = Object.keys(HAVE).join(',') + '|' + Object.keys(compOff).join(',') + '|' + JSON.stringify(compChoice) + '|';
    var all = {}; ORDER.forEach(function (b) { all[b] = []; });
    rows.forEach(function (r) {
      var b = bucketOf(r, now);
      r._b = b; r.hidden = !(b && passes(r));
      r._card.render('schedule', now);
      if (b) all[b].push(r);
      if (b && !r.hidden) groups[b].push(r);
      sig += (b || '-') + ':';
    });
    ORDER.forEach(function (b) { all[b].sort(function (a, c) { return a._k - c._k || c._score - a._score; }); });
    // "Live" once ESPN says so; a kickoff time that has passed without word is awaiting its score.
    rows.forEach(function (r) {
      var l = r.querySelector('.row__live'); if (!l) return;
      var pending = r._state !== 'in';
      l.hidden = r._b !== 'live'; l.textContent = pending ? 'Awaiting score' : 'Live'; l.classList.toggle('row__live--pending', pending);
    });
    if (!force && sig === lastSig) { renderSummary(groups, all, now); return false; }
    lastSig = sig;
    keepFocus(function () { rebuild(groups, all, now); });
    return true;
  }
  // A redraw re-inserts the schedule's rows, which the browser's scroll anchoring can't follow, so
  // a change of view or lineup from the bar, or a live update, would leave the reader elsewhere on
  // the page. Note what is at the top of the reader's view (a schedule row, else a section) and
  // where, run the update, and scroll it back there. A row the change hides gives way to the first
  // shown row that kicks off no earlier, so the reader stays at the same time of day.
  function keepPlace(update) {
    if (window.scrollY <= 0) return update();
    var shown = function (el) { return el.isConnected && el.getClientRects().length > 0; };
    var below = bar ? bar.getBoundingClientRect().bottom : 0;
    var anchor = Array.from(document.querySelectorAll('#app > section:not(#outlook), #app > article, #app > footer, #outlook li.row'))
      .find(function (el) { return shown(el) && el.getBoundingClientRect().bottom > below + 1; });
    if (!anchor) return update();
    var was = anchor.getBoundingClientRect().top, result = update(), place = anchor;
    if (!shown(place) && anchor.matches('li.row')) {
      place = rows.filter(function (r) { return shown(r) && r._k >= anchor._k; })
        .sort(function (a, c) { return a._k - c._k || a.getBoundingClientRect().top - c.getBoundingClientRect().top; })[0];
    }
    if (place && shown(place)) {
      var moved = place.getBoundingClientRect().top - was;
      if (Math.abs(moved) >= 1) window.scrollBy(0, moved);
    }
    return result;
  }
  function rebuild(groups, all, now) {
    var context = Object.keys(HAVE).join(',') + '|' + Object.keys(compOff).join(',');
    if (context !== foldContext) { foldContext = context; foldChoices = {}; }
    var frag = document.createDocumentFragment();
    var anyUpcoming = false;
    ORDER.forEach(function (b) {
      var list = groups[b];
      if (!list.length) return;
      list.sort(function (a, c) { return a._k - c._k || c._score - a._score; });
      var sec, host;
      var h = document.createElement('h3'); h.className = 'bucket__h';
      // Today and Tomorrow give the day's full date in grey; a later day is its weekday, with the rest of
      // its date in grey ("Saturday" and "October 10"), which keeps the heading to one line on a phone.
      var day = DAYS.indexOf(b), date = day >= 0 ? dayDate(day, now) : null;
      var title = TITLES[b] || fmtLongDay.format(date);
      var when = !date ? '' : TITLES[b] ? fmtDay.format(date) : fmtMonthDay.format(date);
      h.innerHTML = '<span></span><span class="when"></span><span class="bucket__count"></span>';
      h.firstChild.textContent = title;
      h.children[1].textContent = when;
      h.children[2].textContent = list.length + (list.length === 1 ? ' match' : ' matches');
      if (FOLDED[b]) {
        sec = document.createElement('details'); sec.className = 'fold bucket'; sec.setAttribute('data-b', b);
        sec.open = Object.prototype.hasOwnProperty.call(foldChoices, b) && foldChoices[b];
        var sum = document.createElement('summary'); sum.appendChild(h);
        sum.addEventListener('click', function () { foldChoices[b] = !sec.open; });
        var car = document.createElement('span'); car.className = 'caret'; car.innerHTML = ' <span class="c">show &#9662;</span><span class="o">hide &#9652;</span>'; h.children[2].appendChild(car);
        sec.appendChild(sum); host = sec;
      } else {
        sec = document.createElement('section'); sec.className = 'bucket' + (b === 'live' ? ' bucket--live' : b === 'today' ? ' bucket--now' : '');
        sec.setAttribute('data-b', b); sec.appendChild(h); host = sec;
        anyUpcoming = true;
      }
      var ol = document.createElement('ol'); ol.className = 'rows'; list.forEach(function (r) { ol.appendChild(r); }); host.appendChild(ol);
      frag.appendChild(sec);
    });
    if (!anyUpcoming) {
      var e = document.createElement('p'); e.className = 'empty';
      e.textContent = 'No matches on your selected services and competitions in the next three days. Open Lineup to add services or competitions.';
      frag.insertBefore(e, frag.firstChild);
    }
    // Rows not placed (outside the window) are parked out of sight.
    var park = document.getElementById('park') || (function () { var p = document.createElement('div'); p.id = 'park'; p.hidden = true; document.body.appendChild(p); return p; })();
    rows.forEach(function (r) { if (!r._b) park.appendChild(r); });
    body.innerHTML = ''; body.appendChild(frag);
    renderNextup(now); renderPicks(now); renderMisses(all, now); renderLineup(all, now); renderSummary(groups, all, now);
    watchIcons();
    positionDrawer();
  }

  // ---- the top card (live now, or next up) and kickoff countdowns ------------------------------
  var nextupEl = document.getElementById('nextup'), nextupSection = document.getElementById('nextup-section'), nextRow = null;
  function fmtCount(ms) {
    var s = Math.max(0, Math.floor(ms / 1000)), d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600);
    var m = Math.floor((s % 3600) / 60), sec = s % 60;
    if (d >= 1) return d + 'd ' + (h < 10 ? '0' : '') + h + 'h';
    if (h >= 1) return h + 'h ' + (m < 10 ? '0' : '') + m + 'm';
    return m + ':' + (sec < 10 ? '0' : '') + sec;
  }
  // The model that wrote the ratings on show, as the tooltip names it: what settings.toml asked for,
  // else who answered; '' for a story that records neither.
  function raterName() {
    var s = STORY.s || {}, name = typeof s.requested_model === 'string' && s.requested_model ? s.requested_model : s.model;
    return typeof name === 'string' ? name : '';
  }
  function ratingOf(r) {
    var rating = STORY.rankings && STORY.rankings[r.getAttribute('data-id')];
    return rating && typeof rating.score === 'number' && rating.score >= 0 && rating.score <= 100 ? rating : null;
  }
  // A match's interest is the AI rating and the Outlook score in settings.toml's proportions, or the
  // Outlook score alone with AI off or for a match without an AI rating; its pick score adds the
  // visitor's league priority. All run from 0 to 100; null only on a page built without scores.
  function interestOf(r) {
    var rating = ratingOf(r), own = r._outlook, b = SCORING.blend;
    if (rating && own !== null && b.ai + b.outlook > 0) return (rating.score * b.ai + own * b.outlook) / (b.ai + b.outlook);
    return own !== null ? own : rating ? rating.score : null;
  }
  function pickValue(r) {
    var interest = interestOf(r), b = SCORING.blend;
    return interest === null ? null : (interest * b.interest + leaguePriority(r) * b.league_priority) / (b.interest + b.league_priority);
  }
  function byRating(a, b) {
    var av = pickValue(a), bv = pickValue(b);
    return (bv === null ? -1 : bv) - (av === null ? -1 : av) || byTime(a, b);
  }
  function leaguePriority(r) {
    var order = leagueOrder(), position = order.indexOf(r._lg);
    return position < 0 ? 0 : order.length < 2 ? 100 : 100 * (order.length - 1 - position) / (order.length - 1);
  }
  function blendedScore(r) { var v = pickValue(r); return v === null ? null : Math.round(v * 10) / 10; }
  // A pick score as one to five golden dots: one, and one more from each of settings.toml's [dots]
  // thresholds, which are fixed scores, so a dot means the same every day.
  function dotCount(score) { return 1 + SCORING.dots.filter(function (t) { return score >= t; }).length; }
  function fillDots(el, n) {
    if (el.childElementCount === n) return;
    el.replaceChildren(); for (var i = 0; i < n; i++) el.appendChild(document.createElement('i'));
  }
  // The dots are the whole display of a pick score, on rows and cards alike: the exact score and its
  // parts are in the tooltip, and a screen reader hears the score in words.
  function showDots(el, r, score) {
    var n = dotCount(score);
    fillDots(el, n);
    el.setAttribute('role', 'img');
    el.setAttribute('aria-label', 'Pick score ' + score + ' out of 100, ' + n + (n === 1 ? ' dot' : ' dots') + ' of 5');
    el.title = 'Pick score ' + score + '/100, ' + n + ' of 5 dots. ' + scoreDetails(r);
  }
  function share(a, b) { return Math.round(100 * a / (a + b)); }
  function oneDecimal(x) { return Math.round(x * 10) / 10; }
  var OUTLOOK_NAMES = [['stature', 'occasion'], ['close', 'evenly matched'], ['stakes', 'stakes'], ['tv', 'TV'], ['goals', 'goals expected']];
  // The tooltip on a pick score: every part with its weight and value, so a surprising pick explains itself.
  function scoreDetails(r) {
    var rating = ratingOf(r), own = r._outlook, b = SCORING.blend, both = rating && own !== null;
    var lines = [share(b.interest, b.league_priority) + '% ' + (both ? 'interest' : own !== null ? 'Outlook score' : 'AI rating') +
      ' (' + oneDecimal(interestOf(r)) + ') + ' + share(b.league_priority, b.interest) + '% league priority (' + Math.round(leaguePriority(r)) + ').'];
    if (both) lines.push('Interest: ' + share(b.ai, b.outlook) + '% AI rating (' + rating.score + ') + ' + share(b.outlook, b.ai) + '% Outlook score (' + own + ').');
    if (rating) lines.push('AI rating' + (raterName() ? ' by ' + raterName() : '') + ': ' + ratingDetails(rating) + '.');
    if (own !== null) {
      var parts = {};
      try { parts = JSON.parse(r.getAttribute('data-outlook-parts') || '{}') || {}; } catch (e) {}
      lines.push('Outlook score ' + own + ': ' + OUTLOOK_NAMES.map(function (p) {
        return p[1] + ' ' + (typeof parts[p[0]] === 'number' ? parts[p[0]] : 'no data');
      }).join(' · ') + '.');
    }
    return lines.join(' ');
  }
  function upcomingIn(now, keep) {
    return rows.filter(function (r) { return r._b && keep(r) && r._state !== 'post' && (r._k > now || r._b === 'live'); });
  }
  function availableUpcoming(now) { return upcomingIn(now, editorialPasses); }
  // Known coverage on some service, whatever this viewer's lineup: a listed broadcaster the page maps
  // to a service, or the competition's usual home. A match nobody is known to carry can't be watched
  // by anyone, so it is never a pick.
  function covered(r) { return r._o.some(function (o) { return o.v.length; }) || !!(r._r && r._r.v.length); }
  // The top card answers "what can I watch now, or next?" among the matches that pass the lineup:
  // the best-rated match ESPN reports in progress, or else the soonest confirmed kickoff however
  // far ahead (a time to be set can't be put in order, as in the summary's "Next kickoff"; such a
  // match stays in the picks and the schedule). A kickoff that passes without word from ESPN keeps
  // the card, as its schedule row keeps the Live section, until ESPN reports it or the window closes.
  function renderNextup(now) {
    var available = availableUpcoming(now);
    var live = available.filter(function (r) { return r._state === 'in' && r._b === 'live'; }).sort(byRating);
    var next = available.filter(function (r) { return r._state === 'pre' && r._tv; }).sort(function (a, b) { return byTime(a, b) || byRating(a, b); });
    nextRow = live[0] || next[0] || null;
    nextupSection.hidden = nextupEl.hidden = !nextRow;
    if (!nextRow) { nextupEl.removeAttribute('data-match-id'); return; }
    // The heading names the card ("Next up" or "Live now", from nextupText); the line under it, how
    // its match was chosen.
    var choice = pickValue(nextRow) !== null ? 'Best pick score' : 'First to kick off';
    setText(document.getElementById('nextup-sub'), nextRow._state !== 'in' ? 'The soonest kickoff in your lineup'
      : live.length > 1 ? choice + ' of the ' + live.length + ' in progress in your lineup' : 'In progress in your lineup');
    nextRow._card.render('nextup', now, nextupEl);
    tickNextup();
  }
  // The card's heading, status and count. "Live now" only once ESPN reports the match under way,
  // over its clock and score; before that "Next up", over the kickoff and a countdown, and once the
  // kickoff time has passed without word from ESPN, the words its schedule row and pick card use.
  // The live poll updates each competition as its answer comes and redraws once all have answered,
  // so a tick in between can find the card's match just finished: it reads as its row does ("FT"
  // over the score) until that redraw moves the card on. A status in two parts ("Kickoff 1:10 pm",
  // "status pending") is stacked by a phone's narrow column.
  function nextupText(r, now) {
    var clk = clockOf(r), title = r._state === 'pre' ? 'Next up' : 'Live now';
    if (r._state === 'in') return { title: title, what: clk, when: '', count: scoreOf(r) || 'In progress' };
    if (r._state === 'post') return { title: title, what: clk || 'FT', when: '', count: scoreOf(r) };
    var kickoff = 'Kickoff ' + timeLabel(r) + dayTag(r, now);
    return r._k > now ? { title: title, what: kickoff, when: '', count: fmtCount(r._k - now) }
      : { title: title, what: kickoff, when: 'status pending', count: 'Awaiting score' };
  }
  function setText(el, text) { if (el && el.textContent !== text) el.textContent = text; }
  // "A · B" in three spans; the separator is empty when there is no B.
  function pairHtml() { return '<span class="pair__a"></span><span class="pair__sep"></span><span class="pair__b"></span>'; }
  function setPair(el, a, b) {
    setText(el.children[0], a); setText(el.children[1], b ? ' · ' : ''); setText(el.children[2], b || '');
  }
  // Words ("Awaiting score") are set smaller than figures, so they don't widen the column.
  function showNextupText(status, count, text) {
    setText(document.getElementById('nextup-h'), text.title);
    setPair(status, text.what, text.when);
    setText(count, text.count);
    count.classList.toggle('nextup__count--words', !/\d/.test(text.count));
  }
  function tickNextup() {
    var now = nowMs();
    if (nextRow && !nextupSection.hidden) {
      nextupEl.classList.toggle('nextup--live', nextRow._state === 'in');
      showNextupText(document.getElementById('nextup-status'), document.getElementById('nextup-count'), nextupText(nextRow, now));
    }
    rows.forEach(function (r) { r._card.tick(r, now); });
    picksEl.querySelectorAll('.pick').forEach(function (card) { card._matchCard.tick(card, now); });
  }
  setInterval(tickNextup, 1000);

  // ---- match details: hover preview and native modal, both outside the card layout -------------
  var detailPreview = document.getElementById('match-preview'), detailDialog = document.getElementById('match-dialog');
  var previewState = null, dialogState = null, previewTimer = null, previewCloseTimer = null, backdropDown = false;
  function detailsFor(button) {
    var host = button.closest('[data-match-role], .miss');
    var card = host && (host._matchCard || host._row && host._row._card);
    return card ? { card: card, trigger: button, role: host.dataset.matchRole || 'miss' } : null;
  }
  function fillDetails(surface, state) {
    var prefix = surface === detailDialog ? 'match-dialog' : 'match-preview';
    var detail = state.card.row.querySelector('.row__detail');
    surface._signature = detail ? detail.innerHTML : '';
    surface.dataset.matchId = state.card.row.getAttribute('data-id');
    document.getElementById(prefix + '-title').textContent = matchName(state.card.row);
    document.getElementById(prefix + '-content').replaceChildren(state.card.detailsContent());
  }
  function hideDetailPreview() {
    clearTimeout(previewTimer); clearTimeout(previewCloseTimer);
    var state = previewState, restoreFocus = detailPreview.contains(document.activeElement);
    detailPreview.hidden = true; previewState = null;
    if (restoreFocus && state) restoreDetailFocus(state);
  }
  function positionDetailPreview() {
    if (!previewState) return;
    var rect = previewState.trigger.getBoundingClientRect(), margin = 12, gap = 8;
    var below = window.innerHeight - rect.bottom - margin - gap, above = rect.top - margin - gap;
    detailPreview.style.maxHeight = Math.max(80, Math.max(below, above)) + 'px';
    var box = detailPreview.getBoundingClientRect();
    var top = below >= box.height || below >= above ? rect.bottom + gap : rect.top - gap - box.height;
    detailPreview.style.left = Math.max(margin, Math.min(rect.right - box.width, window.innerWidth - box.width - margin)) + 'px';
    detailPreview.style.top = Math.max(margin, Math.min(top, window.innerHeight - box.height - margin)) + 'px';
  }
  function schedulePreviewClose() {
    clearTimeout(previewTimer); clearTimeout(previewCloseTimer);
    previewCloseTimer = setTimeout(function () { if (!detailPreview.contains(document.activeElement)) hideDetailPreview(); }, 220);
  }
  function visibleEl(el) { return el && el.isConnected && el.getClientRects().length > 0; }
  // The Details button for the same match in the same role, which a re-render may have rebuilt.
  function liveTrigger(state) {
    if (visibleEl(state.trigger)) return state.trigger;
    var host = Array.from(document.querySelectorAll('[data-match-role], .miss')).find(function (el) {
      return (el._matchCard || el._row && el._row._card) === state.card && (el.dataset.matchRole || 'miss') === state.role;
    });
    var button = host && host.querySelector('button.more');
    return visibleEl(button) ? button : null;
  }
  function restoreDetailFocus(state) {
    if (!state) return;
    var button = liveTrigger(state) || state.card.row.querySelector('button.more');
    (visibleEl(button) ? button : btnMenu).focus({ preventScroll: true });
  }
  // A live update rebuilds cards; the preview stays open while it or its button is hovered or holds
  // focus, and shows the match's current details. Otherwise it closes, as before.
  function refreshDetailPreview() {
    if (!previewState) return;
    var trigger = liveTrigger(previewState);
    var held = detailPreview.matches(':hover') || detailPreview.contains(document.activeElement) || (trigger && trigger.matches(':hover'));
    if (!held) { hideDetailPreview(); return; }
    if (trigger) previewState.trigger = trigger;
    var detail = previewState.card.row.querySelector('.row__detail');
    if (detailPreview._signature !== (detail ? detail.innerHTML : '')) keepFocus(function () { fillDetails(detailPreview, previewState); });
    if (trigger) positionDetailPreview();
  }
  function openDetailDialog(button) {
    var state = detailsFor(button); if (!state) return;
    hideDetailPreview(); dialogState = state; fillDetails(detailDialog, state);
    document.documentElement.classList.add('has-match-dialog');
    detailDialog.showModal();
  }
  document.addEventListener('pointerover', function (ev) {
    if (ev.pointerType !== 'mouse' || detailDialog.open) return;
    var button = ev.target.closest('button.more');
    if (!button || button.contains(ev.relatedTarget)) return;
    clearTimeout(previewTimer); clearTimeout(previewCloseTimer);
    previewTimer = setTimeout(function () {
      if (!button.isConnected || !button.matches(':hover')) return;
      var state = detailsFor(button); if (!state) return;
      previewState = state; fillDetails(detailPreview, state); detailPreview.hidden = false; positionDetailPreview();
    }, 180);
  });
  document.addEventListener('pointerout', function (ev) {
    var button = ev.target.closest('button.more');
    if (button && !button.contains(ev.relatedTarget) && !detailPreview.contains(ev.relatedTarget)) schedulePreviewClose();
  });
  detailPreview.addEventListener('pointerenter', function () { clearTimeout(previewCloseTimer); });
  detailPreview.addEventListener('pointerleave', function (ev) {
    if (!previewState || !previewState.trigger.contains(ev.relatedTarget)) schedulePreviewClose();
  });
  detailPreview.addEventListener('focusin', function () { clearTimeout(previewCloseTimer); });
  detailPreview.addEventListener('focusout', function (ev) { if (!detailPreview.contains(ev.relatedTarget)) schedulePreviewClose(); });
  document.addEventListener('keydown', function (ev) { if (ev.key === 'Escape') hideDetailPreview(); });
  document.addEventListener('pointerdown', function (ev) {
    if (!detailPreview.contains(ev.target) && !ev.target.closest('button.more')) hideDetailPreview();
  });
  // A preview being read follows its button when the page scrolls, including the scroll the browser
  // makes itself when a goal moves a row above it; any other preview closes. One still waiting to
  // open opens only if the pointer is still on its button.
  window.addEventListener('scroll', function (ev) {
    if (detailPreview.contains(ev.target) || !previewState) return;
    var trigger = liveTrigger(previewState), held = detailPreview.matches(':hover') || detailPreview.contains(document.activeElement);
    if (held && trigger) { previewState.trigger = trigger; positionDetailPreview(); } else hideDetailPreview();
  }, true);
  window.addEventListener('resize', hideDetailPreview);
  document.getElementById('match-dialog-close').addEventListener('click', function () { detailDialog.close(); });
  detailDialog.addEventListener('keydown', function (ev) {
    if (ev.key !== 'Tab') return;
    var stops = Array.from(detailDialog.querySelectorAll('button:not(:disabled), a[href]')).filter(function (el) { return el.getClientRects().length; });
    var first = stops[0], last = stops[stops.length - 1];
    if (ev.shiftKey && document.activeElement === first) { ev.preventDefault(); last.focus(); }
    else if (!ev.shiftKey && document.activeElement === last) { ev.preventDefault(); first.focus(); }
  });
  function outsideDialog(ev) {
    var rect = detailDialog.getBoundingClientRect();
    return ev.clientX < rect.left || ev.clientX > rect.right || ev.clientY < rect.top || ev.clientY > rect.bottom;
  }
  detailDialog.addEventListener('pointerdown', function (ev) { backdropDown = ev.target === detailDialog && outsideDialog(ev); });
  detailDialog.addEventListener('click', function (ev) { if (backdropDown && ev.target === detailDialog && outsideDialog(ev)) detailDialog.close(); });
  detailDialog.addEventListener('close', function () {
    var state = dialogState; dialogState = null; backdropDown = false;
    document.documentElement.classList.remove('has-match-dialog');
    if (state && state.focusTarget) state.focusTarget.focus(); else restoreDetailFocus(state);
  });
  document.addEventListener('click', function (ev) {
    var button = ev.target.closest('button.more');
    if (button) { openDetailDialog(button); return; }
    var link = ev.target.closest('a.detail__table');
    if (link) {
      hideDetailPreview();
      var table = document.querySelector('.tables__item[data-lg="' + link.getAttribute('data-lg') + '"]');
      if (table) table.open = true;
      if (detailDialog.open) {
        if (table && dialogState) dialogState.focusTarget = table.querySelector('summary');
        detailDialog.close();
      } else if (table) table.querySelector('summary').focus();
    }
  });

  // The window's rows still to come or in progress (today's finished matches and yesterday's are not).
  function upcoming(groups) { return DAYS.reduce(function (list, b) { return list.concat(groups[b]); }, groups.live.slice()); }
  function logoClone(r, i, cls) { var l = r.querySelectorAll('.logo')[i]; var c = l ? l.cloneNode(true) : document.createElement('i'); c.className = cls + (c.className.indexOf('logo--txt') > -1 ? ' logo--txt' : '') + (l ? ' ' + Array.prototype.filter.call(l.classList, function (x) { return x.indexOf('l-') === 0; }).join(' ') : ''); return c; }
  function timeLabel(r) { if (!r._tv) return 'TBD'; var st = splitTime(new Date(r._k)); return st.ap ? st.t + ' ' + st.ap : st.t; }
  function dayTag(r, now) { var idx = dayIndex(r._k, now); return idx === 0 ? '' : idx === 1 ? ' tomorrow' : ' ' + fmtShortDay.format(new Date(r._k)); }

  function setPickWhen(when, r, now) {
    var live = r._b === 'live' && r._state === 'in';
    var st = r._tv ? splitTime(new Date(r._k)) : { t: 'TBD', ap: '' }, sc = live ? scoreOf(r) : '';
    when.firstChild.textContent = sc || st.t;
    when.lastChild.textContent = live ? (sc ? 'live ' + clockOf(r) : 'live now') : st.ap + dayTag(r, now) + (r._tv && r._k <= now ? ' · status pending' : '');
  }
  function missTime(r, now) {
    var sc = r._b === 'live' ? scoreOf(r) : '', clk = clockOf(r);
    return sc ? 'live ' + sc + (clk ? ', ' + clk : '') : timeLabel(r) + dayTag(r, now);
  }
  function ratingDetails(rating) { return 'Popularity ' + rating.popularity + ' · Expected gameplay ' + rating.gameplay + ' · Competitive impact ' + rating.impact; }
  // The top three show the presumed best matches, so they come from every covered match on every
  // service and in every competition, whatever the lineup and filters; a pick this viewer can't watch
  // looks as its schedule row does outside the lineup. The top card and the schedule stay filtered.
  // They are the best-scored of the page's window, today and the three days after it, which the AI
  // rates each morning; not of the next 24 hours, where on a quiet day (an international break) the few
  // matches took every place whatever their scores, over far better ones the day after. With AI ratings
  // on the page, only rated matches compete: one the ratings missed would be scored by the Outlook score
  // alone, which runs several points higher, and could win on that. Fewer than three in the window
  // make fewer cards.
  var pickedRows = [];
  function renderPicks(now) {
    var available = upcomingIn(now, covered).filter(function (r) { return r !== nextRow; });
    if (available.some(function (r) { return ratingOf(r); })) available = available.filter(function (r) { return ratingOf(r); });
    var chosen = available.sort(byRating).slice(0, 3);
    chosen.sort(function (a, b) { return byTime(a, b) || byRating(a, b); });
    pickedRows = chosen;
    document.getElementById('picks-section').hidden = !chosen.length;
    picksEl.innerHTML = '';
    // Without ratings the cards are simply the next matches; calling them picks would claim a judgment.
    var rated = chosen.some(function (r) { return pickValue(r) !== null; }), aiRated = chosen.some(function (r) { return ratingOf(r); });
    document.getElementById('picks-h').textContent = !rated ? 'Upcoming' : chosen.length === 3 ? 'Top three' : chosen.length === 2 ? 'Top two' : 'Top pick';
    document.getElementById('picks-sub').textContent = !rated ? 'In kickoff order · every service and competition · no ratings yet'
      : 'The best of the next three days by ' + (aiRated ? 'AI rating + Outlook score + league priority' : 'Outlook score + league priority') +
        ' · every service and competition · in kickoff order';
    chosen.forEach(function (r) { picksEl.appendChild(r._card.render('pick', now)); });
  }

  // One match owns all three views. The generated schedule body is the canonical template:
  // clone it intact so forms, scores, venue, news, outlets and details cannot drift between roles.
  // The static row remains useful before JavaScript runs; live ESPN updates also land there.
  function MatchCard(row) {
    this.row = row;
    this.newsSignature = '';
  }
  MatchCard.prototype.detailsContent = function () {
    var panel = this.row.querySelector('.row__detail').cloneNode(true);
    panel.hidden = false;
    return panel;
  };
  MatchCard.prototype.tick = function (host, now) {
    var r = this.row, el = host.querySelector('.row__until'); if (!el) return;
    var d = r._k - now;
    var show = r._tv && r._state === 'pre' && d > 0 && d < 12 * 3600000 && r._b && r._b !== 'live';
    el.hidden = !show; if (show) el.textContent = 'in ' + fmtCount(d);
  };
  MatchCard.prototype.renderNews = function (now) {
    var r = this.row, note = matchBlurb(r), existing = r.querySelector('.row__story');
    if (!note) { if (existing) existing.remove(); this.newsSignature = ''; return; }
    var signature = JSON.stringify(note);
    if (!existing || signature !== this.newsSignature) {
      if (existing) existing.remove();
      existing = storyLine(note, 'row__story');
      var meta = r.querySelector('.row__meta'); meta.after(existing);
      this.newsSignature = signature;
    }
    existing.hidden = !editorialPasses(r) || staleNews(r, now);
  };
  function staleNews(r, now) { return r._state === 'post' || (r._state !== 'in' && r._k < now); }
  // Every schedule row shows its pick score's dots under the kickoff, as its card would, so any two
  // matches can be compared. They follow the visitor's league priority, so they are set at each redraw;
  // the time column isn't part of the cards, which show their own.
  MatchCard.prototype.renderPickScore = function () {
    var r = this.row, score = blendedScore(r), el = r.querySelector('.row__pick');
    if (!el) {
      el = document.createElement('span'); el.className = 'row__pick dots';
      r.querySelector('.row__kickoff').appendChild(el);
    }
    el.hidden = score === null;
    if (score !== null) showDots(el, r, score);
  };
  MatchCard.prototype.render = function (role, now, host) {
    var r = this.row;
    this.renderNews(now);
    if (role === 'schedule') {
      r.dataset.matchRole = role; r._matchCard = this; this.tick(r, now);
      this.renderPickScore();
      return r;
    }
    var top = role === 'nextup', off = !onSvc(r);
    host = host || document.createElement('article');
    // Only a pick can be off the lineup; it takes the off-lineup grey and the dimmed look of its row.
    host.className = (top ? 'nextup' + (r._state === 'in' ? ' nextup--live' : '') : 'pick') + ' svc-' + (off ? 'off' : r._svc) +
      (off ? ' match--off' : '');
    host.dataset.matchRole = role; host.dataset.matchId = r.getAttribute('data-id');
    host._row = r; host._matchCard = this; host.replaceChildren();
    var emblem = r.querySelector('.row__league .lg'), badge = null;
    if (emblem) {
      badge = emblem.cloneNode(true); badge.classList.add('match__league', top ? 'nextup__league' : 'pick__league');
      badge.title = r.getAttribute('data-comp');
      if (!top) host.appendChild(badge);
    }
    var head = document.createElement('div'); head.className = top ? 'nextup__left' : 'pick__head';
    // The pick score as its dots alone; a page built without scores labels its cards Upcoming instead.
    var score = blendedScore(r), label = document.createElement('div');
    label.className = (top ? 'nextup__rating' : 'pick__rating') + (score !== null ? ' dots' : '');
    if (score !== null) showDots(label, r, score);
    else label.textContent = 'Upcoming';
    head.appendChild(label);
    if (top) {
      label.id = 'nextup-rating'; label.hidden = score === null;
      var status = document.createElement('div'); status.className = 'nextup__status pair'; status.id = 'nextup-status';
      var count = document.createElement('div'); count.className = 'nextup__count'; count.id = 'nextup-count';
      status.innerHTML = pairHtml(); showNextupText(status, count, nextupText(r, now));
      head.appendChild(status); head.appendChild(count);
    } else {
      var when = document.createElement('div'); when.className = 'pick__when';
      when.innerHTML = '<span class="t"></span><span class="ap"></span>'; setPickWhen(when, r, now); head.appendChild(when);
      head.appendChild(r.querySelector('.row__until').cloneNode(true));
    }
    if (r._state === 'in' || showET) {
      var kickoff = document.createElement('div'); kickoff.className = 'match__kickoff';
      kickoff.textContent = (r._state === 'in' ? 'Kickoff ' + timeLabel(r) + dayTag(r, now) : '') +
        (showET && r._tv ? (r._state === 'in' ? ' · ' : '') + fmtET.format(new Date(r._k)) + ' ET' : '');
      head.appendChild(kickoff);
    }
    if (top && badge) head.appendChild(badge);
    host.appendChild(head);
    var content = r.querySelector('.row__body').cloneNode(true);
    content.classList.add('match__body');
    if (top) content.id = 'nextup-match';
    // A row outside the lineup hides its blurb; a pick shows it, since the pick is there to be read.
    var blurb = content.querySelector('.row__story');
    if (blurb && role === 'pick') blurb.hidden = staleNews(r, now);
    content.querySelectorAll('.team__name').forEach(function (name) {
      var link = document.createElement('a'); link.href = '#outlook'; link.textContent = name.textContent;
      link.addEventListener('click', function (ev) {
        ev.preventDefault(); var fold = r.closest('details.fold'); if (fold) fold.open = true;
        r.scrollIntoView({ block: 'center' });
      });
      name.replaceChildren(link);
    });
    // Without a blurb a card has room for what the Details panel holds: each team's season record and
    // top scorer join its lines (CSS shows them under .match--facts), and the panel's links take the
    // place of its Details button. With a blurb, or in a row, they stay in the panel.
    var detail = content.querySelector('.row__detail'), facts = !content.querySelector('.row__story');
    host.classList.toggle('match--facts', facts);
    if (facts) {
      var links = detail.querySelector('.detail__links'), more = content.querySelector('.pills .more');
      if (more) more.remove();
      if (links) { links.className = 'match__links'; content.appendChild(links); }
    }
    detail.remove();
    var watch = r.querySelector('.row__watch'), service = watch && watch.cloneNode(true);
    if (service) service.className = 'match__watch' + (top ? ' nextup__watch' : '');
    // A pick is tall and narrow, so its broadcaster goes in the body; the top card has the row's
    // short, wide shape, and its own column for it.
    if (service && !top) content.insertBefore(service, content.querySelector('.pills'));
    host.appendChild(content);
    if (service && top) host.appendChild(service);
    if (role === 'pick') {
      var colors = document.createElement('div'); colors.className = 'pick__colors'; colors.setAttribute('aria-hidden', 'true');
      [r.getAttribute('data-hc'), r.getAttribute('data-ac')].forEach(function (c) {
        var bar = document.createElement('i'); bar.style.background = /^[0-9a-f]{6}$/.test(c || '') ? '#' + c : 'var(--line-strong)'; colors.appendChild(bar);
      });
      host.appendChild(colors);
    }
    this.tick(host, now);
    return host;
  };

  function matchBlurb(r) {
    var rating = ratingOf(r), note = STORY.notes[r.getAttribute('data-id')];
    if (rating && typeof rating.blurb === 'string' && rating.blurb) return { note: rating.blurb, sources: rating.sources };
    if (note) return note;
    // During a data rollout, reuse only authored text referring to this exact match (indexed once per story).
    var item = STORY.single && STORY.single[r.getAttribute('data-id')];
    return item ? { note: item.segments.map(function (part) { return part.text; }).join(''), sources: item.sources } : null;
  }

  function renderMisses(groups, now) {
    var pool = upcoming(groups).filter(function (r) {
      return inFocus(r, now) && r._svc === 'none' && !r._unk && r._o.length && r._score >= 85 && !compHidden(r) && pickedRows.indexOf(r) < 0;
    }).sort(function (a, c) { return c._score - a._score || a._k - c._k; }).slice(0, 4);
    missesEl.innerHTML = '';
    pool.forEach(function (r) {
      var id = r.getAttribute('data-id');
      var d = document.createElement('div'); d.className = 'miss'; d.setAttribute('data-id', id); d._row = r;
      var teams = document.createElement('div'); teams.className = 'miss__teams';
      teams.appendChild(logoClone(r, 0, 'logo')); var vs = document.createElement('span'); vs.className = 'miss__vs'; vs.textContent = 'v'; teams.appendChild(vs); teams.appendChild(logoClone(r, 1, 'logo'));
      var bodyEl = document.createElement('div');
      var names = document.createElement('div'); names.className = 'miss__names'; names.textContent = r.getAttribute('data-home') + ' v ' + r.getAttribute('data-away') + ' ';
      var tm = document.createElement('span'); tm.className = 'miss__time'; tm.textContent = missTime(r, now); names.appendChild(tm);
      var where = document.createElement('div'); where.className = 'miss__where';
      var pills = r.querySelector('.pills'); if (pills) where.innerHTML = pills.innerHTML;
      bodyEl.className = 'miss__body'; bodyEl.appendChild(names);
      bodyEl.appendChild(where);
      d.appendChild(teams); d.appendChild(bodyEl);
      missesEl.appendChild(d);
    });
  }

  function renderLineup(groups, now) {
    // The window's matches (today and the three days after it), by service.
    var week = upcoming(groups).filter(function (r) { return !compHidden(r) && r._state !== 'post'; });
    var host = document.getElementById('lineup'); host.innerHTML = '';
    var ids = serviceOrder().filter(function (id) { return HAVE[id]; });
    if (!ids.length) { var e = document.createElement('p'); e.className = 'empty'; e.textContent = 'No services selected. Open "Lineup & filters" and tap the ones you have.'; host.appendChild(e); return; }
    ids.forEach(function (k) {
      var w = week.filter(function (r) { return r._svc === k; });
      var card = document.createElement('div'); card.className = 'svc svc-' + k + (w.length ? '' : ' svc--quiet'); card.setAttribute('data-svc', k);
      var head = document.createElement('div'); head.className = 'svc__head';
      head.innerHTML = '<i class="dot"></i><span class="svc__name"></span><span class="svc__count"></span>';
      head.children[1].textContent = SERVICE_NAMES[k] || k;
      head.children[2].textContent = w.length + ' in the next three days';
      var desc = document.createElement('p'); desc.className = 'svc__desc';
      var next = w.filter(function (r) { return inFocus(r, now) || r._k > now; }).sort(byTime)[0];
      desc.textContent = next ? 'Next: ' + matchName(next) + ', ' + timeLabel(next) + dayTag(next, now) + (next._outlet && next._outlet !== (SERVICE_NAMES[k] || '') ? ' on ' + next._outlet : '') + (next._basis === 'rule' ? ' (usual home; channel not posted yet)' : '') + '.' : 'Nothing listed in the next three days.';
      card.appendChild(head); card.appendChild(desc); host.appendChild(card);
    });
  }

  // ---- schedule facts and the authored overview -----------------------------------------------
  function onSvc(r) { return r._svc !== 'none'; }
  function byTime(a, c) { return a._k - c._k; }
  function matchName(r) { return r.getAttribute('data-home') + ' v ' + r.getAttribute('data-away'); }
  function proseTime(r) {
    if (!r._tv) return 'a time to be set';
    var st = splitTime(new Date(r._k));
    if (st.t === '12:00' && st.ap === 'pm') return 'noon';
    if (st.t === '12:00' && st.ap === 'am') return 'midnight';
    // "8 pm" on a 12-hour clock; a 24-hour clock keeps its minutes, or "20:00" would read as "20".
    return st.ap ? st.t.replace(/:00$/, '') + ' ' + st.ap : st.t;
  }
  // What a row shows now: "2–1" and "67'" (each empty before kickoff, or when it isn't known).
  function scoreOf(r) {
    var s = r.querySelectorAll('.team .score');
    return s.length === 2 && !s[0].hidden && !s[1].hidden ? s[0].textContent + '\u2013' + s[1].textContent : '';
  }
  function clockOf(r) { var s = r.querySelector('.row__status'); return s && !s.hidden ? s.textContent : ''; }

  function matchCount(n) { return n + (n === 1 ? ' match' : ' matches'); }
  function coverageSummary(list) {
    var listed = list.filter(function (r) { return onSvc(r) && r._basis === 'listed'; }).length;
    var usual = list.filter(function (r) { return onSvc(r) && r._basis === 'rule'; }).length;
    var unknown = list.filter(function (r) { return !onSvc(r) && (r._unk || !r._o.length); }).length;
    var parts = [Object.keys(HAVE).length ? listed + ' listed on your services' : 'No services selected'];
    if (usual) parts.push(usual + ' with usual coverage on your services (not yet listed)');
    if (unknown) parts.push(unknown + ' with unconfirmed coverage');
    return parts.join('; ') + '.';
  }
  function periodSummary(list, now) {
    var remaining = list.filter(function (r) { return r._state !== 'post'; });
    var selected = remaining.filter(function (r) { return !compHidden(r); });
    var hidden = remaining.length - selected.length;
    if (!selected.length) return hidden ? matchCount(hidden) + ' hidden by competition filters.' : 'No remaining matches in the loaded schedule.';
    var live = selected.filter(function (r) { return r._state === 'in'; }).length;
    var pending = selected.filter(function (r) { return r._state === 'pre' && r._tv && r._k <= now; }).length;
    var scheduled = selected.length - live - pending, parts = [];
    if (live) parts.push(live + ' live');
    if (scheduled) parts.push(scheduled + ' upcoming');
    if (pending) parts.push(pending + ' awaiting score updates');
    var text = parts.join(' · ') + ' in selected competitions. ' + coverageSummary(selected);
    if (hidden) text += ' ' + matchCount(hidden) + ' hidden by competition filters.';
    return text;
  }
  function summaryOutlet(r) {
    if (onSvc(r)) {
      var service = SERVICE_NAMES[r._svc] || r._svc;
      var outlet = r.getAttribute('data-outlet');
      if (r._basis === 'rule') return service + ' (usual coverage; not yet listed)';
      return service + (outlet && outlet !== service ? ' (' + outlet + ')' : '');
    }
    if (r._unk || !r._o.length) return 'coverage unconfirmed';
    return r._o.map(function (o) { return o.l; }).join(', ') + ' (not in your lineup)';
  }
  function composeSchedule(now) {
    var focus = rows.filter(function (r) { return inFocus(r, now); });
    var lines = [{ label: 'Next 24 hours', text: periodSummary(focus, now) }];
    var future = rows.filter(function (r) {
      return r._b && passes(r) && r._state === 'pre' && r._tv && r._k > now;
    }).sort(byTime);
    if (future.length) {
      var first = future[0], together = future.filter(function (r) { return r._k === first._k; });
      var date = localYmd(first._k) === localYmd(now) ? '' : fmtDay.format(new Date(first._k)) + ', ';
      var text = together.slice(0, 3).map(function (r) { return matchName(r) + ' — ' + summaryOutlet(r); }).join('; ');
      if (together.length > 3) text += '; ' + (together.length - 3) + ' more at this time';
      lines.push({ label: (first._k < now + FOCUS_MS ? 'Next kickoff · ' : 'Beyond 24 hours · next kickoff · ') + date + proseTime(first), text: text + '.' });
    }
    return lines;
  }

  // The overview is written by Claude (the full design). The browser only counts and formats schedule facts.
  function renderSummary(groups, all, now) {
    renderEditorial(now);
    function para(host, text, label) {
      var p = document.createElement('p');
      if (label) { var b = document.createElement('b'); b.textContent = label + ': '; p.appendChild(b); }
      p.appendChild(document.createTextNode(text)); host.appendChild(p); return p;
    }
    var summary = document.getElementById('schedule-summary'); summary.innerHTML = '';
    composeSchedule(now).forEach(function (line) { para(summary, line.text, line.label); });
    if (app.getAttribute('data-incomplete') === '1') {
      para(summary, 'Some fixtures may be missing because ESPN did not answer every request.').className = 'schedule-summary__note';
    }
    // Only the date: the schedule runs past the next 24 hours, so the line above the title can't claim that window.
    document.getElementById('eyebrow').textContent = fmtDay.format(new Date(now));
    // The window's matches still to come or in progress: today and the three days after it.
    document.getElementById('tally-n').textContent = upcoming(groups).filter(function (r) { return r._state !== 'post'; }).length;
    document.getElementById('tally-txt').textContent = 'matches on your services in the next three days';
  }

  // ---- freshness -----------------------------------------------------------------------------
  function checkStale() {
    var built = Date.parse(app.getAttribute('data-built'));
    var age = (Date.now() - built) / 3600000;
    var el = document.getElementById('stale');
    if (age > 30) { el.textContent = 'This page was last rebuilt ' + Math.round(age) + ' hours ago; the daily refresh may have failed, so fixtures and channels could have moved.'; el.hidden = false; }
    else el.hidden = true;
  }

  // ---- storylines: story.json, written by story.py at each rebuild, published beside the page ----
  // Tagged stories carry the build's 24-hour horizon, so crossing midnight does not discard
  // still-relevant phrases. Legacy stories retain their calendar-day check for match notes only.
  // An open tab checks freshness each minute and fetches updates every ten minutes.
  var STORY = { notes: {} };   // while a story is shown, also: s (as published), written (ms)
  var STORY_MAX_AGE_H = 30, STORY_EVERY_MS = 10 * 60000, storyAskedAt = 0;
  function localYmd(k) { var d = new Date(k); return '' + d.getFullYear() + ('0' + (d.getMonth() + 1)).slice(-2) + ('0' + d.getDate()).slice(-2); }
  function storyIsCurrent(s, written, now) {
    if ((now - written) / 3600000 > STORY_MAX_AGE_H) return false;
    if (s.focus_until) return Date.parse(s.focus_until) > now;
    if (typeof s.date !== 'string') return true;
    var day = s.date.replace(/-/g, '');
    return day === etYmd(now) || day === localYmd(now);
  }
  function safeUrl(u) { return typeof u === 'string' && /^https?:\/\/[^\s]+$/i.test(u) ? u : ''; }
  function hostOf(u) { var m = /^https?:\/\/(?:www\.)?([^\/:?#]+)/i.exec(u); return m ? m[1] : 'source'; }
  // Each link says where it goes. A page story.py marks as ESPN's facts was not read as reporting, so
  // it is named for what it supports, and two articles from one site are numbered, not repeated.
  function isFactsSource(s) { return s.kind === 'facts' || s.title === 'ESPN match facts'; }
  function sourceLinks(sources, max) {
    var frag = document.createDocumentFragment(), labels = {};
    (Array.isArray(sources) ? sources : []).slice(0, max).forEach(function (s) {
      var url = safeUrl(s && s.url); if (!url) return;
      var facts = isFactsSource(s), label = facts ? 'ESPN table and form' : hostOf(url);
      labels[label] = (labels[label] || 0) + 1;
      var a = document.createElement('a'); a.href = url; a.target = '_blank'; a.rel = 'noopener noreferrer';
      a.textContent = labels[label] > 1 ? label + ' (' + labels[label] + ')' : label;
      a.title = facts ? 'From ESPN\'s table, form and stage for this match, not from reporting' : (typeof s.title === 'string' ? s.title.slice(0, 200) : '');
      if (frag.childNodes.length) frag.appendChild(document.createTextNode(', '));
      frag.appendChild(a);
    });
    return frag;
  }
  function storyLine(n, cls) {
    var div = document.createElement('div'); div.className = cls;
    var tag = document.createElement('span'); tag.className = 'story-tag'; tag.textContent = 'Story'; div.appendChild(tag);
    div.appendChild(document.createTextNode(n.note));
    var links = sourceLinks(n.sources, 2);
    if (links.childNodes.length) { var src = document.createElement('span'); src.className = 'story-src'; src.appendChild(document.createTextNode('(')); src.appendChild(links); src.appendChild(document.createTextNode(')')); div.appendChild(document.createTextNode(' ')); div.appendChild(src); }
    return div;
  }
  function rowsForIds(ids) {
    if (!Array.isArray(ids)) return null;
    var found = ids.map(function (id) { return typeof id === 'string' ? ROW_BY_ID[id] : null; });
    return found.every(Boolean) ? found : null;
  }
  function editorialPasses(r) { return onSvc(r) && !compHidden(r); }
  function referencedRows(item) {
    if (!item || !Array.isArray(item.segments) || !item.segments.length) return [];
    var found = [], valid = item.segments.every(function (part) {
      if (!part || typeof part.text !== 'string') return false;
      var refs = rowsForIds(part.match_ids); if (!refs) return false;
      refs.forEach(function (r) { if (found.indexOf(r) < 0) found.push(r); });
      return true;
    });
    return valid ? found : [];
  }
  function appendEditorialText(host, segments) {
    segments.forEach(function (part) {
      var refs = rowsForIds(part.match_ids), filtered = refs && refs.length && !refs.every(editorialPasses);
      var span = document.createElement('span'); span.className = 'editorial-part' + (filtered ? ' editorial-part--filtered' : '');
      span.textContent = part.text;
      if (part.match_ids.length) span.setAttribute('data-matches', part.match_ids.join(' '));
      if (filtered) span.title = 'Excluded by your competition or service filters';
      host.appendChild(span);
    });
  }
  function editorialItem(item) {
    var refs = referencedRows(item);
    var el = document.createElement('span'); el.className = 'editorial-item';
    el.setAttribute('data-matches', refs.map(function (r) { return r.getAttribute('data-id'); }).join(' '));
    var text = document.createElement('span'); text.className = 'editorial-item__text';
    appendEditorialText(text, item.segments); el.appendChild(text);
    return el;
  }
  // The ratings design's overview: one plain paragraph about the whole slate, the same for every visitor,
  // so nothing in it is filtered or dimmed. A full-design story's tagged overview follows the filters below.
  function renderOverview(o) {
    var storyEl = document.getElementById('story'), sources = Array.isArray(o.sources) ? o.sources : [];
    var signature = JSON.stringify(['overview', o.text, sources.map(function (src) { return src && src.url; })]);
    if (storyEl._signature === signature) return;
    storyEl._signature = signature;
    keepFocus(function () {
      storyEl.hidden = false;
      storyEl.removeAttribute('data-league');
      setText(document.getElementById('story-h'), 'Overview');
      document.getElementById('story-lede').textContent = o.text;
      // The disclosure that the overview is written by AI; the footer says which model and how.
      var storyBy = document.getElementById('story-by'); storyBy.textContent = 'AI Summary';
      var links = sourceLinks(sources, 5);
      if (links.childNodes.length) { storyBy.appendChild(document.createTextNode(' · ')); storyBy.appendChild(links); }
    });
  }
  function renderEditorial(now) {
    var s = STORY.s, storyEl = document.getElementById('story');
    if (s && s.overview) return renderOverview(s.overview);
    var lead = s && Array.isArray(s.lede_items) ? s.lede_items : [];
    function eligible(item) {
      var refs = referencedRows(item);
      return refs.some(editorialPasses) && refs.every(function (r) { return r._state !== 'post' && (r._k >= now || inFocus(r, now)); });
    }
    function firstKickoff(item) {
      return Math.min.apply(Math, referencedRows(item).filter(editorialPasses).map(function (r) { return Math.max(now, r._k); }));
    }
    function near(item) { return eligible(item) && firstKickoff(item) < now + FOCUS_MS; }
    var blurbs = s && Array.isArray(s.league_blurbs) ? s.league_blurbs.filter(function (item) {
      return typeof item.interest === 'number' && item.interest >= 0 && item.interest <= 100 && eligible(item);
    }) : [];
    lead = lead.filter(eligible);
    var todayLead = lead.filter(function (item) { return referencedRows(item).some(function (r) { return editorialPasses(r) && (r._state === 'in' || localYmd(r._k) === localYmd(now)); }); });
    var nearLead = lead.filter(near);
    if (todayLead.length || nearLead.length) lead = todayLead.length ? todayLead : nearLead;
    else if (blurbs.some(near) || !lead.length) {
      var nearBlurbs = blurbs.filter(near);
      if (nearBlurbs.length) blurbs = nearBlurbs;
      else if (blurbs.length) {
        var first = Math.min.apply(Math, blurbs.map(firstKickoff));
        blurbs = blurbs.filter(function (item) { return firstKickoff(item) < first + FOCUS_MS; });
      }
      blurbs.sort(function (a, b) { return b.interest - a.interest || firstKickoff(a) - firstKickoff(b); });
      lead = blurbs.length ? [blurbs[0]] : lead;
    }
    var hasNear = lead.some(near), sources = [];
    lead.forEach(function (item) {
      (Array.isArray(item.sources) ? item.sources : []).forEach(function (source) {
        if (source && typeof source.url === 'string' && !sources.some(function (s) { return s.url === source.url; })) sources.push(source);
      });
    });
    // Called every minute: rebuilding unchanged text would drop the focus and selection of anyone on it.
    var signature = JSON.stringify([hasNear, sources.map(function (s) { return s.url; }), lead.map(function (item) {
      return item.segments.map(function (part) { var refs = rowsForIds(part.match_ids); return [part.text, !!(refs && refs.length && !refs.every(editorialPasses))]; });
    })]);
    if (storyEl._signature === signature) return;
    storyEl._signature = signature;
    keepFocus(function () {
      storyEl.hidden = !lead.length;
      var lede = document.getElementById('story-lede'); lede.innerHTML = '';
      lead.forEach(function (item, i) {
        if (i) lede.appendChild(document.createTextNode(' '));
        lede.appendChild(editorialItem(item));
      });
      document.getElementById('story-h').textContent = hasNear ? 'Overview' : 'Overview · Further ahead';
      if (lead.length && lead[0].league_id) storyEl.setAttribute('data-league', lead[0].league_id);
      else storyEl.removeAttribute('data-league');
      // The page's disclosure that the overview is written by AI (Anthropic's Usage Policy asks for one
      // on automatically published text); the footer says which model and how.
      // Only over an overview: a hidden section keeps no AI label, so a page with AI off carries none at all.
      var storyBy = document.getElementById('story-by'); storyBy.textContent = lead.length ? 'AI Summary' : '';
      var links = sourceLinks(sources, 4);
      if (links.childNodes.length) { storyBy.appendChild(document.createTextNode(' · ')); storyBy.appendChild(links); }
    });
  }
  function applyStory(s) {
    if (!s || typeof s !== 'object' || s.version !== 1) return;
    // Shapes story.py guarantees, checked again here: a malformed field must not stop the page's
    // filters and live scores, which every later render would otherwise throw on.
    ['lede_items', 'league_blurbs', 'league_order'].forEach(function (k) { if (!Array.isArray(s[k])) s[k] = []; });
    s.league_order = s.league_order.filter(function (k) { return typeof k === 'string'; });
    s.lede_items = s.lede_items.filter(function (item) { return item && typeof item === 'object'; });
    s.league_blurbs = s.league_blurbs.filter(function (item) { return item && typeof item === 'object'; });
    var o = s.overview;
    s.overview = o && typeof o === 'object' && typeof o.text === 'string' && o.text.trim() ? o : null;
    var written = Date.parse(typeof s.generated_at === 'string' ? s.generated_at : '');
    if (!(written > 0) || !storyIsCurrent(s, written, nowMs())) return;
    if (STORY.s && STORY.written >= written) return;   // the one on show already, or a newer one
    clearStory(true);
    var notes = {};
    var rawNotes = s.notes && typeof s.notes === 'object' && !Array.isArray(s.notes) ? s.notes : {};
    Object.keys(rawNotes).forEach(function (id) { var n = rawNotes[id]; if (n && typeof n.note === 'string' && n.note) notes[id] = n; });
    STORY = { notes: notes, s: s, written: written, single: {},
              rankings: s.rankings && typeof s.rankings === 'object' && !Array.isArray(s.rankings) ? s.rankings : {} };
    s.league_blurbs.forEach(function (item) {
      var refs = referencedRows(item), id = refs.length === 1 && refs[0].getAttribute('data-id');
      if (id && !STORY.single[id]) STORY.single[id] = item;
    });
    renderLeagueOrder();
    render(true);
  }
  // Takes the story down: the section, the notes under the rows, and (at the next render) the notes
  // on the cards and the overview.
  function clearStory(quiet) {
    if (!STORY.s) return;
    STORY = { notes: {} };
    var storyEl = document.getElementById('story'); storyEl.hidden = true; storyEl._signature = null;
    Array.prototype.slice.call(document.querySelectorAll('.row__story')).forEach(function (el) { el.parentNode.removeChild(el); });
    if (!quiet) render(true);
  }
  function loadStory() {
    if (!AI_ON || !window.fetch || location.protocol === 'file:') return;
    storyAskedAt = Date.now();
    fetch('story.json', { cache: 'no-cache' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(applyStory)
      .catch(function () {});
  }
  function checkStory() {
    if (STORY.s && !storyIsCurrent(STORY.s, STORY.written, nowMs())) clearStory(false);
    if (!document.hidden && Date.now() - storyAskedAt >= STORY_EVERY_MS) loadStory();
  }

  // ---- live scores: ESPN's scoreboard, read by the browser while matches are on -----------------
  // The page is rebuilt three times a day, so a match that kicks off between rebuilds would show no
  // score until the next one. From 15 minutes before a kickoff until ESPN calls the match over, the
  // page asks ESPN's public scoreboard (which allows any origin) for that competition's day, once a
  // minute while the tab is visible, and updates the rows in place. A match that finished since the
  // rebuild is asked about once, so "Earlier today" carries its result; competitions the viewer has
  // switched off are not asked about. The requests start after the page has drawn, each is abandoned
  // after 8 seconds, and any failure leaves the page as built; repeated failures back off to one try
  // in ten minutes.
  var LIVE = { base: 'https://site.api.espn.com/apis/site/v2/sports/soccer/', everyMs: 60000, timeoutMs: 8000,
               leadMs: 15 * 60000, tailMs: 4 * 3600000, lookbackMs: 30 * 3600000,
               busy: false, startedAt: 0, okAt: 0, fails: 0, nextAt: 0 };
  (function () {   // a test server on this machine may stand in for ESPN: ?scoresbase=http://localhost:8000/espn/
    var m = /[?&]scoresbase=([^&#]+)/.exec(location.search), b = m ? decodeURIComponent(m[1]) : '';
    if (/^(localhost|127\.0\.0\.1)$/.test(location.hostname) && /^https?:\/\/(localhost|127\.0\.0\.1)(:\d+)?\//.test(b)) LIVE.base = b;
  })();
  function liveDue(r, now) {
    if (!r._tv || r._state === 'post' || compHidden(r) || now < r._k - LIVE.leadMs) return false;
    if (now < r._k + LIVE.tailMs) return true;
    return !r._asked && now < r._k + LIVE.lookbackMs;
  }
  // Resolves with the parsed body, or rejects on an HTTP error, a network error or the timeout,
  // whichever comes first; the timeout covers the body as well as the headers.
  function getJson(url) {
    return new Promise(function (resolve, reject) {
      var ctl = window.AbortController ? new AbortController() : null, done = false;
      function finish(fn, v) { if (done) return; done = true; clearTimeout(timer); fn(v); }
      var timer = setTimeout(function () { if (ctl) ctl.abort(); finish(reject, new Error('timeout')); }, LIVE.timeoutMs);
      fetch(url, { cache: 'no-store', credentials: 'omit', signal: ctl ? ctl.signal : undefined })
        .then(function (res) { if (!res.ok) throw new Error('HTTP ' + res.status); return res.json(); })
        .then(function (j) { finish(resolve, j); }, function (e) { finish(reject, e); });
    });
  }
  // The same reading of ESPN's status as build.py's interpret().
  function liveStatus(st) {
    var t = (st && st.type) || {}, desc = (t.description || '').toLowerCase();
    if (t.state === 'in') return desc.indexOf('halftime') === 0 ? 'HT' : (st.displayClock || t.shortDetail || 'Live');
    if (t.state === 'post') return /^(full time|final|full-time)$/.test(desc) ? 'FT' : (t.shortDetail || t.description || 'FT');
    return '';
  }
  function liveGoals(comp, side) {
    var by = {}, parts = [];
    (comp.details || []).forEach(function (d) {
      if (!d || !d.scoringPlay || d.shootout) return;
      var who = (d.athletesInvolved || [])[0] || {}, note = d.penaltyKick ? 'pen' : d.ownGoal ? 'og' : '';
      var tid = String((d.team || {}).id || '');
      (by[tid] = by[tid] || []).push(((who.shortName || who.displayName || '') + ' ' + ((d.clock || {}).displayValue || '')).trim() + (note ? ' (' + note + ')' : ''));
    });
    ['home', 'away'].forEach(function (h) {
      var t = side[h].team || {}, list = by[String(t.id || '')];
      if (list) parts.push({ team: (t.abbreviation || '').slice(0, 4) || t.displayName || t.name || '', who: list.join(', ') });
    });
    return parts;
  }
  function setGoals(r, parts) {
    var el = r.querySelector('.row__goals');
    if (!el) {
      var before = r.querySelector('.row__body > .row__note, .row__body > .pills'); if (!before) return;
      el = document.createElement('div'); el.className = 'row__goals'; before.parentNode.insertBefore(el, before);
    }
    el.textContent = '';
    parts.forEach(function (p, i) {
      if (i) el.appendChild(document.createTextNode(' · '));
      var b = document.createElement('b'); b.textContent = p.team; el.appendChild(b);
      el.appendChild(document.createTextNode(' ' + p.who));
    });
  }
  function flash(el) {
    el.classList.remove('score--new'); void el.offsetWidth; el.classList.add('score--new');
    setTimeout(function () { el.classList.remove('score--new'); }, 3200);
  }
  // Brings one row up to date with ESPN's event, noting in `changed` what moved.
  function applyEvent(r, ev, first, changed) {
    var comp = (ev.competitions || [])[0]; if (!comp) return;
    var st = comp.status || ev.status || {}, state = (st.type || {}).state;
    if (state !== 'pre' && state !== 'in' && state !== 'post') return;
    var side = {};
    (comp.competitors || []).forEach(function (c) { if (c && (c.homeAway === 'home' || c.homeAway === 'away')) side[c.homeAway] = c; });
    if (!side.home || !side.away) return;
    var status = liveStatus(st), off = state === 'post' && /^(canceled|cancelled|postponed)$/i.test(status);
    var played = state !== 'pre' && !off;
    var vals = [side.home, side.away].map(function (c) {
      var v = c.score != null && typeof c.score === 'object' ? c.score.displayValue : c.score;
      return played && /^\d{1,3}$/.test(String(v)) ? String(v) : '';
    });
    r._seen = Date.now();
    if (state !== r._state) {
      r._state = state; r.setAttribute('data-state', state); changed.state = true;
      if (off) { r._score = 0; r.setAttribute('data-score', '0'); }
    }
    var els = r.querySelectorAll('.team .score');
    vals.forEach(function (v, i) {
      var el = els[i]; if (!el) return;
      if (el.hidden && v === '' || !el.hidden && el.textContent === v) return;
      if (!first && !el.hidden && v !== '') flash(el);
      el.textContent = v; el.hidden = v === ''; changed.score = true;
    });
    var s = r.querySelector('.row__status');
    if (s && s.textContent !== status) { s.textContent = status; s.hidden = !status; changed.clock = true; }
    // Replace the scorers only with a list, or with nothing at 0-0: a feed that has the score but not
    // yet the scorer shouldn't wipe the scorers the page was built with.
    var goals = played ? liveGoals(comp, side) : [], g = r.querySelector('.row__goals'), beforeGoals = g ? g.textContent : '';
    if (goals.length) setGoals(r, goals);
    else if (g && (!played || vals[0] === '0' && vals[1] === '0')) g.parentNode.removeChild(g);
    g = r.querySelector('.row__goals');
    if ((g ? g.textContent : '') !== beforeGoals) changed.goals = true;
  }
  function showLiveNote() {
    var el = document.getElementById('livenote');
    if (!LIVE.okAt && !LIVE.fails) return;
    var t = LIVE.okAt ? splitTime(new Date(LIVE.okAt)) : null, when = t ? t.t + ' ' + t.ap : '';
    el.textContent = !LIVE.fails ? 'Scores update live from ESPN while matches are on; last checked at ' + when + '.'
      : LIVE.okAt ? 'Scores update live from ESPN while matches are on; last checked at ' + when + ', and the latest check didn’t get through, so it will try again shortly.'
      : 'Couldn’t reach ESPN for live scores just now, so scores are as of the last rebuild; it will try again shortly.';
    el.hidden = false;
  }
  function refreshLiveText() {
    var now = nowMs();
    keepPlace(function () {
      keepFocus(function () {
        if (nextRow) nextRow._card.render('nextup', now, nextupEl);
        picksEl.querySelectorAll('.pick').forEach(function (a) { if (a._row) a._row._card.render('pick', now, a); });
      });
    });
    refreshDetailPreview();
    watchIcons();
    missesEl.querySelectorAll('.miss').forEach(function (d) { var t = d.querySelector('.miss__time'); if (d._row && t) t.textContent = missTime(d._row, now); });
  }
  function pollLive() {
    if (!window.fetch || !window.Promise || LIVE.busy || document.hidden) return;
    if (Date.now() < LIVE.nextAt || Date.now() - LIVE.startedAt < 20000) return;
    var now = nowMs(), groups = {}, keys = [];
    rows.forEach(function (r) {
      if (!liveDue(r, now)) return;
      var key = r._lg + '/' + etYmd(r._k);
      if (!groups[key]) { groups[key] = []; keys.push(key); }
      groups[key].push(r);
    });
    if (!keys.length) return;
    LIVE.busy = true; LIVE.startedAt = Date.now();
    var changed = { state: false, score: false, clock: false }, ok = 0;
    Promise.all(keys.map(function (key) {
      var lg = key.split('/')[0], day = key.split('/')[1];
      return getJson(LIVE.base + encodeURIComponent(lg) + '/scoreboard?dates=' + day + '&limit=200').then(function (data) {
        ok++;
        var byId = {};
        ((data && data.events) || []).forEach(function (ev) { if (ev && ev.id != null) byId[String(ev.id)] = ev; });
        groups[key].forEach(function (r) {
          var first = !r._asked, ev = byId[r.getAttribute('data-id')];
          r._asked = true;
          try { if (ev) applyEvent(r, ev, first, changed); } catch (e) {}
        });
      }).catch(function () {});
    })).then(function () {
      LIVE.busy = false;
      if (ok) { LIVE.okAt = Date.now(); LIVE.fails = 0; LIVE.nextAt = 0; }
      else { LIVE.fails++; LIVE.nextAt = Date.now() + Math.min(LIVE.everyMs * Math.pow(2, LIVE.fails), 10 * 60000) - 2000; }
      showLiveNote();
      if (changed.state || changed.score) render(true);
      else if (changed.clock || changed.goals) { render(false); refreshLiveText(); }
    });
  }

  evaluateAll();
  applyFilterUI();
  render(true);
  checkStale();
  loadStory();
  window.addEventListener('hashchange', function () { render(true); checkStory(); });
  setTimeout(pollLive, 0);
  setInterval(function () { render(false); checkStale(); checkStory(); }, 60000);
  setInterval(pollLive, LIVE.everyMs);
  document.addEventListener('visibilitychange', function () { if (!document.hidden) { render(false); checkStale(); checkStory(); pollLive(); } });
})();
