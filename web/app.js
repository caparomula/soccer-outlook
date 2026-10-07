(function () {
  'use strict';
  var DAY_START = 4;                 // a sports day runs 4 am to 4 am local time
  var FOCUS_MS = 24 * 60 * 60000;
  var LIVE_MS = 125 * 60000;
  var LS = { mode: 'ssg2-mode', comp: 'ssg2-comp-off', priority: 'ssg4-league-order', services: 'ssg4-service-order' };
  var app = document.getElementById('app');
  var body = document.getElementById('outlook-body');
  var rows = Array.prototype.slice.call(document.querySelectorAll('li.row'));
  if (!rows.length) return;
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
  function rankOf(id) { var i = serviceOrder().indexOf(id); return i < 0 ? 99 : i; }
  var SHORT = { cable: 'cable', ota: 'antenna', free: 'free app' };   // buckets where the channel leads
  function escHtml(t) { return String(t).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  var ORDER = ['live', 'morning', 'afternoon', 'evening', 'tonight', 'tomorrow', 'later', 'earlier', 'yesterday'];
  var TITLES = { live: 'Live now', morning: 'This morning', afternoon: 'This afternoon', evening: 'This evening', tonight: 'Tonight', tomorrow: 'Tomorrow', later: 'Beyond 24 hours', earlier: 'Earlier today', yesterday: 'Yesterday' };
  var FOLDED = { later: true, earlier: true, yesterday: true };

  rows.forEach(function (r) {
    r._k = Date.parse(r.getAttribute('data-utc'));
    r._tv = r.getAttribute('data-tv') === '1';
    r._svc = r.getAttribute('data-svc');
    r._lg = r.getAttribute('data-lg');
    r._score = parseInt(r.getAttribute('data-score'), 10) || 0;
    r._state = r.getAttribute('data-state');
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
  var mode = read(LS.mode) === 'all' ? 'all' : 'mine';
  var drawerOpen = false;
  var drawer = document.getElementById('drawer'), btnMenu = document.getElementById('btn-menu');
  var storedHave = read('ssg3-have');
  var HAVE = {};
  (Array.isArray(storedHave) ? storedHave : SERVICES.owner).forEach(function (k) { HAVE[k] = true; });
  var compOff = {};
  var storedComp = read(LS.comp);
  if (storedComp) { storedComp.forEach(function (k) { compOff[k] = true; }); }
  else { drawer.querySelectorAll('[data-kind="comp"][data-default-off="1"]').forEach(function (b) { compOff[b.getAttribute('data-key')] = true; }); }
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
    var preferred = Array.isArray(storedPriority) ? storedPriority : (STORY && STORY.s && STORY.s.league_order || []);
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
  function filterEnabled(kind, id) { return kind === 'have' ? !!HAVE[id] : !compOff[id]; }
  function setFilterEnabled(kind, id, enabled) {
    if (kind === 'have') { if (enabled) HAVE[id] = true; else delete HAVE[id]; }
    else { if (enabled) delete compOff[id]; else compOff[id] = true; }
  }
  function persistFilters(kind) {
    if (kind === 'have') { storedHave = Object.keys(HAVE); write('ssg3-have', storedHave); evaluateAll(); }
    else write(LS.comp, Object.keys(compOff));
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
    controls.classList.toggle('mode-mine', mode === 'mine');
    drawer.hidden = !drawerOpen; btnMenu.setAttribute('aria-expanded', String(drawerOpen));
    document.getElementById('filter-sum').textContent = filterSummary();
    document.getElementById('btn-mine').setAttribute('aria-pressed', String(mode === 'mine'));
    document.getElementById('btn-all').setAttribute('aria-pressed', String(mode === 'all'));
    drawer.querySelectorAll('.fpill').forEach(function (b) {
      var k = b.getAttribute('data-key');
      var on = b.getAttribute('data-kind') === 'have' ? !!HAVE[k] : !compOff[k];
      b.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
  }
  function passes(r) {
    // This view promises availability; unconfirmed coverage belongs in Everything.
    if (mode === 'mine' && !onSvc(r)) return false;
    if (compOff[r._lg]) return false;
    return true;
  }
  // The lineup panel: opened from the Lineup button, closed by it, by Done, by Escape or by a tap
  // outside. Opening moves focus into the panel; closing from the keyboard returns it to the button.
  function setDrawer(open, refocus) {
    if (!open && filterDrag) finishFilterDrag({ pointerId: filterDrag.id, type: 'pointercancel' });
    drawerOpen = open; applyFilterUI();
    if (open) { drawer.scrollTop = 0; drawer.focus({ preventScroll: true }); }
    else if (refocus) btnMenu.focus({ preventScroll: true });
  }
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
    if (b.id === 'btn-mine' || b.id === 'btn-all') { mode = b.id === 'btn-all' ? 'all' : 'mine'; write(LS.mode, mode); }
    else if (b.id === 'btn-reset') {
      mode = 'mine'; HAVE = {}; SERVICES.owner.forEach(function (k) { HAVE[k] = true; }); storedHave = null;
      compOff = {}; drawer.querySelectorAll('[data-kind="comp"][data-default-off="1"]').forEach(function (x) { compOff[x.getAttribute('data-key')] = true; });
      storedPriority = null; storedServiceOrder = null;
      write(LS.mode, mode); try { localStorage.removeItem(LS.comp); localStorage.removeItem('ssg3-have'); localStorage.removeItem(LS.priority); localStorage.removeItem(LS.services); } catch (e) {}
      evaluateAll();
    }
    else if (b.hasAttribute('data-move-league')) {
      var order = leagueOrder(), index = order.indexOf(b.getAttribute('data-move-league')), direction = Number(b.getAttribute('data-direction'));
      var target = index + direction;
      if (index < 0 || target < 0 || target >= order.length) return;
      order.splice(target, 0, order.splice(index, 1)[0]);
      var focusLeague = b.getAttribute('data-move-league');
      saveLeagueOrder(order);
      var moved = document.querySelector('#league-order [data-league="' + focusLeague + '"]');
      moved.querySelector('button:not(:disabled)').focus({ preventScroll: true });
      return;
    }
    else if (b.id === 'btn-clear' || b.id === 'btn-select-services') {
      HAVE = {};
      if (b.id === 'btn-select-services') SERVICES.order.forEach(function (k) { HAVE[k] = true; });
      storedHave = Object.keys(HAVE); write('ssg3-have', storedHave); evaluateAll();
    }
    else if (b.id === 'btn-clear-leagues' || b.id === 'btn-select-leagues') {
      compOff = {};
      if (b.id === 'btn-clear-leagues') drawer.querySelectorAll('[data-kind="comp"]').forEach(function (x) { compOff[x.getAttribute('data-key')] = true; });
      write(LS.comp, Object.keys(compOff));
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
  function bucketOf(r, now) {
    if (r._state !== 'post' && r._k >= now + FOCUS_MS) return 'later';
    // A live match can cross midnight or the 4 am sports-day boundary.
    if (inFocus(r, now) && r._tv && r._k <= now) return 'live';
    var idx = dayIndex(r._k, now);
    if (idx < 0) return idx === -1 ? 'yesterday' : null;
    if (idx === 0) {
      if (r._state === 'post') return 'earlier';
      if (r._state === 'in' && (now < r._k + LIVE_MS + 30 * 60000 || Date.now() - (r._seen || 0) < 10 * 60000)) return 'live';
      if (r._tv && now >= r._k && now < r._k + LIVE_MS) return 'live';
      if (r._tv && now >= r._k + LIVE_MS) return 'earlier';
      if (!r._tv) return 'tonight';
      var h = new Date(r._k).getHours(); if (h < DAY_START) h += 24;
      return h < 12 ? 'morning' : h < 17 ? 'afternoon' : h < 20 ? 'evening' : 'tonight';
    }
    if (idx === 1) return 'tomorrow';
    if (idx <= 8) return 'later';
    return null;
  }
  function nowBucketName(now) {
    var h = new Date(now).getHours(); if (h < DAY_START) h += 24;
    return h < 12 ? 'morning' : h < 17 ? 'afternoon' : h < 20 ? 'evening' : 'tonight';
  }

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
  document.getElementById('outlook-sub').textContent = 'The next 24 hours; later fixtures follow below';

  // ---- rendering -----------------------------------------------------------------------------
  var lastSig = '', foldContext = '', foldChoices = {};
  function render(force) {
    var now = nowMs();
    renderLeagueOrder();
    var groups = {}; ORDER.forEach(function (b) { groups[b] = []; });
    var sig = mode + '|' + Object.keys(HAVE).join(',') + '|' + Object.keys(compOff).join(',') + '|';
    var all = {}; ORDER.forEach(function (b) { all[b] = []; });
    rows.forEach(function (r) {
      var b = bucketOf(r, now);
      r._b = b; r.hidden = !(b && passes(r));
      if (b) all[b].push(r);
      if (b && !r.hidden) groups[b].push(r);
      sig += (b || '-') + ':';
    });
    ORDER.forEach(function (b) { all[b].sort(function (a, c) { return a._k - c._k || c._score - a._score; }); });
    rows.forEach(function (r) { var l = r.querySelector('.row__live'); if (l) l.hidden = r._b !== 'live'; });
    if (!force && sig === lastSig) { renderSummary(groups, all, now); return; }
    lastSig = sig;

    var sparse = upcoming(groups).filter(function (r) { return inFocus(r, now); }).length < 5;
    var context = mode + '|' + Object.keys(HAVE).join(',') + '|' + Object.keys(compOff).join(',') + '|' + sparse;
    if (context !== foldContext) { foldContext = context; foldChoices = {}; }
    var frag = document.createDocumentFragment();
    var current = nowBucketName(now), anyUpcoming = false;
    ORDER.forEach(function (b) {
      var list = groups[b];
      if (!list.length) return;
      list.sort(function (a, c) { return a._k - c._k || c._score - a._score; });
      var sec, host;
      var h = document.createElement('h3'); h.className = 'bucket__h';
      var title = TITLES[b];
      var when = '';
      if (b === 'tomorrow') when = fmtDay.format(new Date(list[0]._k));
      if (b === 'live') title = 'Live now';
      h.innerHTML = '<span></span><span class="when"></span><span class="bucket__count"></span>';
      h.firstChild.textContent = title;
      h.children[1].textContent = when;
      h.children[2].textContent = list.length + (list.length === 1 ? ' match' : ' matches');
      if (FOLDED[b]) {
        sec = document.createElement('details'); sec.className = 'fold bucket'; sec.setAttribute('data-b', b);
        sec.open = Object.prototype.hasOwnProperty.call(foldChoices, b) ? foldChoices[b] : b === 'later' && sparse;
        var sum = document.createElement('summary'); sum.appendChild(h);
        sum.addEventListener('click', function () { foldChoices[b] = !sec.open; });
        var car = document.createElement('span'); car.className = 'caret'; car.innerHTML = ' <span class="c">show &#9662;</span><span class="o">hide &#9652;</span>'; h.children[2].appendChild(car);
        sec.appendChild(sum); host = sec;
      } else {
        sec = document.createElement('section'); sec.className = 'bucket' + (b === 'live' ? ' bucket--live' : (b === current ? ' bucket--now' : ''));
        sec.appendChild(h); host = sec;
        anyUpcoming = true;
      }
      if (b === 'later') {
        var intro = document.createElement('div'); intro.id = 'forecast-later'; intro.className = 'forecast';
        intro.setAttribute('aria-label', 'Beyond 24 hours context'); intro.hidden = true; frag.appendChild(intro);
        var byDay = {};
        list.forEach(function (r) { var d = sportsDayStart(new Date(r._k)).toDateString(); (byDay[d] = byDay[d] || []).push(r); });
        Object.keys(byDay).forEach(function (d) {
          var dh = document.createElement('div'); dh.className = 'dayhead'; dh.textContent = fmtDay.format(new Date(byDay[d][0]._k)); host.appendChild(dh);
          var ol = document.createElement('ol'); ol.className = 'rows'; byDay[d].forEach(function (r) { ol.appendChild(r); }); host.appendChild(ol);
        });
      } else {
        var ol2 = document.createElement('ol'); ol2.className = 'rows'; list.forEach(function (r) { ol2.appendChild(r); }); host.appendChild(ol2);
      }
      frag.appendChild(sec);
    });
    if (!anyUpcoming) {
      var e = document.createElement('p'); e.className = 'empty';
      e.textContent = mode === 'mine' ? 'No matches on your selected services and competitions in the next 24 hours. Choose "Everything" to include other matches, including unconfirmed coverage, or adjust your filters.' : 'No matches in the next 24 hours with these competition filters.';
      frag.insertBefore(e, frag.firstChild);
    }
    // Rows not placed (outside the window) are parked out of sight.
    var park = document.getElementById('park') || (function () { var p = document.createElement('div'); p.id = 'park'; p.hidden = true; document.body.appendChild(p); return p; })();
    rows.forEach(function (r) { if (!r._b) park.appendChild(r); });
    body.innerHTML = ''; body.appendChild(frag);
    renderNextup(groups, now); renderPicks(groups, now); renderMisses(all, now); renderLineup(all, now); renderSummary(groups, all, now);
    watchIcons();
  }

  // ---- next up: the Pit Dash countdown ---------------------------------------------------------
  var nextupEl = document.getElementById('nextup'), nextRow = null, nextLive = false;
  function fmtCount(ms) {
    var s = Math.max(0, Math.floor(ms / 1000)), h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    if (h >= 1) return h + 'h ' + (m < 10 ? '0' : '') + m + 'm';
    return m + ':' + (sec < 10 ? '0' : '') + sec;
  }
  function ratingOf(r) {
    var rating = STORY.rankings && STORY.rankings[r.getAttribute('data-id')];
    return rating && typeof rating.score === 'number' && rating.score >= 0 && rating.score <= 100 ? rating : null;
  }
  function byRating(a, b) {
    var ar = ratingOf(a), br = ratingOf(b);
    return (br ? blendedScore(b) : -1) - (ar ? blendedScore(a) : -1) ||
      (br ? br.score : -1) - (ar ? ar.score : -1) || byTime(a, b);
  }
  function leaguePriority(r) {
    var order = leagueOrder(), position = order.indexOf(r._lg);
    return position < 0 ? 0 : order.length < 2 ? 100 : 100 * (order.length - 1 - position) / (order.length - 1);
  }
  function blendedScore(r) {
    var rating = ratingOf(r);
    return rating ? Math.round((rating.score * 0.8 + leaguePriority(r) * 0.2) * 10) / 10 : null;
  }
  function scoreDetails(r) {
    var rating = ratingOf(r);
    return '80% Claude interest (' + rating.score + ') + 20% league priority (' + Math.round(leaguePriority(r)) + '). ' + ratingDetails(rating);
  }
  function availableUpcoming(now) {
    return rows.filter(function (r) { return r._b && editorialPasses(r) && r._state !== 'post' && (r._k > now || r._b === 'live'); });
  }
  function renderNextup(groups, now) {
    var pool = availableUpcoming(now).filter(function (r) { return r._state === 'in' && r._b === 'live'; });
    nextRow = pool.sort(byRating)[0] || null; nextLive = !!nextRow;
    nextupEl.hidden = !nextRow;
    if (!nextRow) { nextupEl.removeAttribute('data-match-id'); return; }
    nextupEl.setAttribute('data-match-id', nextRow.getAttribute('data-id'));
    var rating = ratingOf(nextRow), label = document.getElementById('nextup-rating');
    label.hidden = !rating;
    label.textContent = rating ? 'Pick score · ' + blendedScore(nextRow) + '/100' : '';
    if (rating) label.title = scoreDetails(nextRow);
    nextupEl.classList.toggle('nextup--live', nextLive);
    var m = document.getElementById('nextup-match'); m.innerHTML = '';
    m.appendChild(logoClone(nextRow, 0, 'logo'));
    var names = document.createElement('span'); names.className = 'nextup__names'; names.textContent = matchName(nextRow); m.appendChild(names);
    m.appendChild(logoClone(nextRow, 1, 'logo'));
    var meta = document.createElement('span'); meta.className = 'nextup__meta'; meta.textContent = nextRow.getAttribute('data-comp') + ' \u00b7 ' + (nextRow._tv ? proseTime(nextRow) + dayTag(nextRow, now) : 'time TBD'); m.appendChild(meta);
    var svc = document.createElement('span'); svc.className = 'nextup__svc svc-' + nextRow._svc; svc.innerHTML = '<i class="dot"></i>';
    svc.appendChild(document.createTextNode(summaryOutlet(nextRow))); m.appendChild(svc);
    tickNextup();
  }
  function tickNextup() {
    var now = nowMs();
    if (nextRow && !nextupEl.hidden) {
      var sc = scoreOf(nextRow), clk = clockOf(nextRow);
      document.getElementById('nextup-status').textContent = 'Live now' + (clk ? ' · ' + clk : '');
      document.getElementById('nextup-count').textContent = sc || 'In progress';
    }
    // Per-row countdowns for today's upcoming matches.
    rows.forEach(function (r) {
      var el = r.querySelector('.row__until'); if (!el) return;
      var d = r._k - now;
      var show = r._tv && r._state === 'pre' && d > 0 && d < 12 * 3600000 && r._b && r._b !== 'live';
      el.hidden = !show; if (show) el.textContent = 'in ' + fmtCount(d);
    });
  }
  setInterval(tickNextup, 1000);

  // ---- details panels and table links ---------------------------------------------------------
  document.addEventListener('click', function (ev) {
    var btn = ev.target.closest('button.more');
    if (btn) {
      var host = btn.closest('.row__body, .miss'); if (!host) return;
      var panel = host.querySelector('.row__detail'); if (!panel) return;
      panel.hidden = !panel.hidden; btn.setAttribute('aria-expanded', String(!panel.hidden)); btn.textContent = panel.hidden ? 'Details' : 'Hide details';
      return;
    }
    var tl = ev.target.closest('a.detail__table');
    if (tl) { var d = document.querySelector('.tables__item[data-lg="' + tl.getAttribute('data-lg') + '"]'); if (d) { d.open = true; } }
  });

  function upcoming(groups) { return [].concat(groups.live, groups.morning, groups.afternoon, groups.evening, groups.tonight, groups.tomorrow); }
  function logoClone(r, i, cls) { var l = r.querySelectorAll('.logo')[i]; var c = l ? l.cloneNode(true) : document.createElement('i'); c.className = cls + (c.className.indexOf('logo--txt') > -1 ? ' logo--txt' : '') + (l ? ' ' + Array.prototype.filter.call(l.classList, function (x) { return x.indexOf('l-') === 0; }).join(' ') : ''); return c; }
  function timeLabel(r) { if (!r._tv) return 'TBD'; var st = splitTime(new Date(r._k)); return st.t + ' ' + st.ap; }
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
  function renderPicks(groups, now) {
    var available = availableUpcoming(now).filter(function (r) { return r !== nextRow; });
    var chosen = available.filter(function (r) { return inFocus(r, now); }).sort(byRating).slice(0, 3);
    var later = available.filter(function (r) { return !inFocus(r, now); }).sort(byTime);
    while (chosen.length < 3 && later.length) {
      var boundary = later[0]._k + FOCUS_MS, window = later.filter(function (r) { return r._k < boundary; });
      chosen = chosen.concat(window.sort(byRating).slice(0, 3 - chosen.length));
      later = later.filter(function (r) { return r._k >= boundary; });
    }
    chosen.sort(byRating);
    document.getElementById('picks-section').hidden = !chosen.length;
    picksEl.innerHTML = '';
    document.getElementById('picks-h').textContent = chosen.length === 3 ? 'Top three' : chosen.length === 2 ? 'Top two' : 'Top pick';
    document.getElementById('picks-sub').textContent = 'Next 24 hours first · match interest + league priority';
    chosen.forEach(function (r) {
      var a = document.createElement('article'); a.className = 'pick svc-' + r._svc; a._row = r;
      a.setAttribute('data-match-id', r.getAttribute('data-id'));
      var emblem = r.querySelector('.row__league .lg');
      if (emblem) { var leagueLogo = emblem.cloneNode(true); leagueLogo.classList.add('pick__league'); leagueLogo.title = r.getAttribute('data-comp'); a.appendChild(leagueLogo); }
      var rating = ratingOf(r), ratingLabel = document.createElement('div'); ratingLabel.className = 'pick__rating';
      ratingLabel.textContent = rating ? 'Pick score · ' + blendedScore(r) + '/100' : 'Upcoming';
      if (rating) ratingLabel.title = scoreDetails(r); a.appendChild(ratingLabel);
      var when = document.createElement('div'); when.className = 'pick__when';
      when.innerHTML = '<span class="t"></span><span class="ap"></span>';
      setPickWhen(when, r, now);
      var teams = document.createElement('div'); teams.className = 'pick__teams';
      teams.appendChild(logoClone(r, 0, 'logo logo--lg')); var vs = document.createElement('span'); vs.className = 'pick__vs'; vs.textContent = 'v'; teams.appendChild(vs); teams.appendChild(logoClone(r, 1, 'logo logo--lg'));
      var names = document.createElement('div'); names.className = 'pick__names';
      var link = document.createElement('a'); link.href = '#outlook'; link.textContent = matchName(r); names.appendChild(link);
      link.addEventListener('click', function (ev) { ev.preventDefault(); var fold = r.closest('details.fold'); if (fold) fold.open = true; r.scrollIntoView({ block: 'center' }); });
      var subs = Array.prototype.map.call(r.querySelectorAll('.team'), function (t) { var x = t.querySelector('.team__sub'); return x ? x.textContent.trim() : ''; });
      var sub = null;
      if (subs.some(Boolean)) { sub = document.createElement('div'); sub.className = 'pick__sub'; sub.textContent = (subs[0] || '\u2013') + ' v ' + (subs[1] || '\u2013'); }
      var comp = document.createElement('div'); comp.className = 'pick__comp'; comp.textContent = r.getAttribute('data-comp');
      var colors = document.createElement('div'); colors.className = 'pick__colors';
      [r.getAttribute('data-hc'), r.getAttribute('data-ac')].forEach(function (c) { var i = document.createElement('i'); i.style.background = /^[0-9a-f]{6}$/.test(c || '') ? '#' + c : 'var(--line-strong)'; colors.appendChild(i); });
      var svc = document.createElement('div'); svc.className = 'pick__svc'; svc.innerHTML = '<i class="dot"></i>';
      var outlet = r.getAttribute('data-outlet'), sname = SERVICE_NAMES[r._svc] || r._svc;
      svc.appendChild(document.createTextNode(SHORT[r._svc] ? outlet + ' · ' + SHORT[r._svc] : sname + (outlet && outlet !== sname ? ' · ' + outlet : '') + (r.getAttribute('data-basis') === 'rule' ? ' (usually)' : '')));
      a.appendChild(when); a.appendChild(teams); a.appendChild(names); if (sub) a.appendChild(sub); a.appendChild(comp); a.appendChild(svc);
      var pn = matchBlurb(r);
      if (pn) {
        var ps = document.createElement('div'); ps.className = 'pick__story'; ps.textContent = pn.note; a.appendChild(ps);
        var sources = document.createElement('div'); sources.className = 'pick__sources'; sources.appendChild(sourceLinks(pn.sources, 2)); a.appendChild(sources);
      }
      a.appendChild(colors);
      picksEl.appendChild(a);
    });
  }
  function matchBlurb(r) {
    var rating = ratingOf(r), note = STORY.notes[r.getAttribute('data-id')];
    if (rating && rating.blurb) return { note: rating.blurb, sources: rating.sources || [] };
    if (note) return note;
    // During a data rollout, reuse only authored text referring to this exact match.
    var s = STORY.s || {}, candidates = s.league_blurbs || [];
    var item = candidates.find(function (candidate) { var refs = referencedRows(candidate); return refs.length === 1 && refs[0] === r; });
    return item ? { note: item.segments.map(function (part) { return part.text; }).join(''), sources: item.sources } : null;
  }

  function renderMisses(groups, now) {
    var pool = upcoming(groups).filter(function (r) { return inFocus(r, now) && r._svc === 'none' && !r._unk && r._o.length && r._score >= 85 && !compOff[r._lg]; }).sort(function (a, c) { return c._score - a._score || a._k - c._k; }).slice(0, 4);
    var open = {};
    missesEl.querySelectorAll('.miss').forEach(function (m) { var p = m.querySelector('.row__detail'); if (p && !p.hidden) open[m.getAttribute('data-id')] = true; });
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
      var more = where.querySelector('button.more'); if (more) { more.textContent = 'Details'; more.setAttribute('aria-expanded', 'false'); }
      bodyEl.className = 'miss__body'; bodyEl.appendChild(names);
      bodyEl.appendChild(where);
      d.appendChild(teams); d.appendChild(bodyEl);
      // The card gets its own copy of the row's details panel: the row itself is hidden whenever
      // "On my services" is on, which is exactly when these cards matter.
      var detail = r.querySelector('.row__detail');
      if (detail) {
        var copy = detail.cloneNode(true); copy.hidden = !open[id]; d.appendChild(copy);
        if (open[id] && more) { more.textContent = 'Hide details'; more.setAttribute('aria-expanded', 'true'); }
      }
      missesEl.appendChild(d);
    });
  }

  function renderLineup(groups, now) {
    var week = upcoming(groups).concat(groups.later).filter(function (r) { return !compOff[r._lg] && r._state !== 'post'; });
    var today = week.filter(function (r) { return inFocus(r, now); });
    var host = document.getElementById('lineup'); host.innerHTML = '';
    var ids = serviceOrder().filter(function (id) { return HAVE[id]; });
    if (!ids.length) { var e = document.createElement('p'); e.className = 'empty'; e.textContent = 'No services selected. Open "Lineup & filters" and tap the ones you have.'; host.appendChild(e); return; }
    ids.forEach(function (k) {
      var t = today.filter(function (r) { return r._svc === k; }), w = week.filter(function (r) { return r._svc === k; });
      var card = document.createElement('div'); card.className = 'svc svc-' + k + (t.length ? '' : ' svc--quiet'); card.setAttribute('data-svc', k);
      var head = document.createElement('div'); head.className = 'svc__head';
      head.innerHTML = '<i class="dot"></i><span class="svc__name"></span><span class="svc__count"></span>';
      head.children[1].textContent = SERVICE_NAMES[k] || k;
      head.children[2].textContent = t.length + ' in the next 24 hours';
      var desc = document.createElement('p'); desc.className = 'svc__desc';
      var next = w.filter(function (r) { return inFocus(r, now) || r._k > now; }).sort(byTime)[0];
      desc.textContent = next ? 'Next: ' + matchName(next) + ', ' + timeLabel(next) + dayTag(next, now) + (next._outlet && next._outlet !== (SERVICE_NAMES[k] || '') ? ' on ' + next._outlet : '') + (next._basis === 'rule' ? ' (usual home; channel not posted yet)' : '') + '.' : 'Nothing listed in the next week.';
      card.appendChild(head); card.appendChild(desc); host.appendChild(card);
    });
  }

  // ---- schedule facts and the separately authored forecast -------------------------------------
  function onSvc(r) { return r._svc !== 'none'; }
  function byTime(a, c) { return a._k - c._k; }
  function matchName(r) { return r.getAttribute('data-home') + ' v ' + r.getAttribute('data-away'); }
  function proseTime(r) {
    if (!r._tv) return 'a time to be set';
    var st = splitTime(new Date(r._k));
    if (st.t === '12:00' && st.ap === 'pm') return 'noon';
    if (st.t === '12:00' && st.ap === 'am') return 'midnight';
    return st.t.replace(/:00$/, '') + ' ' + st.ap;
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
    var selected = remaining.filter(function (r) { return !compOff[r._lg]; });
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
  function composeSchedule(all, now) {
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

  // Forecasts are authored by Claude. The browser only counts and formats schedule facts.
  function renderSummary(groups, all, now) {
    renderEditorial(now);
    function para(host, text, label) {
      var p = document.createElement('p');
      if (label) { var b = document.createElement('b'); b.textContent = label + ': '; p.appendChild(b); }
      p.appendChild(document.createTextNode(text)); host.appendChild(p); return p;
    }
    var summary = document.getElementById('schedule-summary'); summary.innerHTML = '';
    composeSchedule(all, now).forEach(function (line) { para(summary, line.text, line.label); });
    if (app.getAttribute('data-incomplete') === '1') {
      para(summary, 'Some fixtures may be missing because ESPN did not answer every request.').className = 'forecast__by';
    }
    document.getElementById('eyebrow').textContent = fmtDay.format(new Date(now)) + ' · Next 24 hours';
    var up = upcoming(groups).filter(function (r) { return inFocus(r, now); }), on = up.filter(onSvc);
    document.getElementById('tally-n').textContent = on.length;
    document.getElementById('tally-txt').textContent = mode === 'mine' ? 'matches on your services in the next 24 hours' : 'of ' + up.length + ' matches in the next 24 hours are on your services';
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
  function sourceLinks(sources, max) {
    var frag = document.createDocumentFragment();
    (sources || []).slice(0, max).forEach(function (s, i) {
      var url = safeUrl(s && s.url); if (!url) return;
      var a = document.createElement('a'); a.href = url; a.target = '_blank'; a.rel = 'noopener noreferrer';
      a.textContent = hostOf(url); a.title = (s.title || '').slice(0, 200);
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
    var found = ids.map(function (id) { return rows.find(function (r) { return r.getAttribute('data-id') === id; }); });
    return found.every(Boolean) ? found : null;
  }
  function editorialPasses(r) { return onSvc(r) && !compOff[r._lg]; }
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
  function editorialItem(item, inline) {
    var refs = referencedRows(item);
    var el = document.createElement(inline ? 'span' : 'div'); el.className = 'editorial-item';
    el.setAttribute('data-matches', refs.map(function (r) { return r.getAttribute('data-id'); }).join(' '));
    var text = document.createElement('span'); text.className = 'editorial-item__text';
    appendEditorialText(text, item.segments); el.appendChild(text);
    var tags = document.createElement('div'); tags.className = 'editorial-tags'; tags.setAttribute('aria-label', 'Teams, competitions and broadcasters');
    var entries = {};
    function tag(kind, key, label, r) {
      var id = kind + ':' + key;
      if (!entries[id]) entries[id] = { kind: kind, key: key, label: label, rows: [] };
      entries[id].rows.push(r);
    }
    refs.forEach(function (r) {
      tag('comp', r._lg, r.getAttribute('data-comp'), r);
      ['home', 'away'].forEach(function (side) { var name = r.getAttribute('data-' + side); tag('team', name, name, r); });
      if (r._o.length) r._o.forEach(function (o) { tag('broadcaster', o.l, o.l, r); });
      else if (r._r) tag('broadcaster', r._r.l, r._r.l + ' (usual coverage)', r);
      else tag('broadcaster', 'unknown', 'Coverage unconfirmed', r);
    });
    Object.keys(entries).forEach(function (key) {
      var entry = entries[key], filtered = !entry.rows.some(editorialPasses);
      var t = document.createElement('span'); t.className = 'editorial-tag' + (filtered ? ' editorial-tag--filtered' : '');
      t.setAttribute('data-kind', entry.kind); t.setAttribute('data-key', entry.key); t.textContent = entry.label;
      if (filtered) t.title = 'Excluded by your filters';
      tags.appendChild(t);
    });
    if (inline) return { text: el, tags: tags };
    el.appendChild(tags);
    var links = sourceLinks(item.sources, 3);
    if (links.childNodes.length) { var src = document.createElement('span'); src.className = 'story-src'; src.appendChild(links); el.appendChild(src); }
    return el;
  }
  function renderEditorial(now) {
    var s = STORY.s, storyEl = document.getElementById('story'), forecastEl = document.getElementById('forecast');
    var lead = s && Array.isArray(s.lede_items) ? s.lede_items : [];
    var forecast = s && s.forecast && Array.isArray(s.forecast.items) ? s.forecast.items : [];
    var legacyLead = lead.slice();
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
    var allBlurbs = blurbs.slice();
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
    var hasNear = lead.some(near);
    forecast = forecast.filter(eligible);
    storyEl.hidden = !lead.length;
    rows.forEach(function (r) {
      var note = r.querySelector('.row__story');
      if (note) note.hidden = !editorialPasses(r) || r._state === 'post' || (r._state !== 'in' && r._k < now);
    });
    var lede = document.getElementById('story-lede'); lede.innerHTML = '';
    var tags = document.getElementById('story-tags'); tags.innerHTML = '';
    var tagKeys = {}, sources = [];
    lead.forEach(function (item, i) {
      var rendered = editorialItem(item, true);
      if (i) lede.appendChild(document.createTextNode(' '));
      lede.appendChild(rendered.text);
      Array.prototype.forEach.call(rendered.tags.children, function (tag) {
        var key = tag.getAttribute('data-kind') + ':' + tag.getAttribute('data-key');
        if (!tagKeys[key]) { tags.appendChild(tag.cloneNode(true)); tagKeys[key] = true; }
      });
      (item.sources || []).forEach(function (source) { if (!sources.some(function (s) { return s.url === source.url; })) sources.push(source); });
    });
    document.getElementById('story-h').textContent = hasNear ? 'Overview' : 'Overview · Further ahead';
    if (lead.length && lead[0].league_id) storyEl.setAttribute('data-league', lead[0].league_id);
    else storyEl.removeAttribute('data-league');
    var storyBy = document.getElementById('story-by'); storyBy.textContent = 'Written by Claude';
    var links = sourceLinks(sources, 4);
    if (links.childNodes.length) { storyBy.appendChild(document.createTextNode(' · ')); storyBy.appendChild(links); }
    var leadIds = {};
    lead.forEach(function (item) { referencedRows(item).forEach(function (r) { leadIds[r.getAttribute('data-id')] = true; }); });
    picksEl.querySelectorAll('.pick').forEach(function (pick) { if (pick.querySelector('.pick__story')) leadIds[pick.getAttribute('data-match-id')] = true; });
    function itemText(item) { return item.segments.map(function (part) { return part.text; }).join(''); }
    function sectionIntro(host, later) {
      if (!host) return;
      host.innerHTML = ''; host.hidden = true;
      function inWindow(r) { return later ? r._state !== 'post' && r._k >= now + FOCUS_MS : inFocus(r, now); }
      var visible = rows.filter(function (r) { return r._b && passes(r) && inWindow(r); }).sort(byTime);
      if (!visible.length) return;
      function suitable(item) {
        var refs = referencedRows(item);
        return eligible(item) && refs.every(inWindow) && itemText(item).length <= 450 &&
          !refs.some(function (r) { return leadIds[r.getAttribute('data-id')]; });
      }
      // One new paragraph per window; do not repeat the opening's match coverage.
      var pool = forecast.filter(suitable);
      if (!pool.length) pool = allBlurbs.concat(legacyLead).filter(suitable);
      if (later && pool.length) {
        var first = Math.min.apply(Math, pool.map(firstKickoff));
        pool = pool.filter(function (item) { return firstKickoff(item) < first + FOCUS_MS; });
      }
      pool.sort(function (a, b) { return (b.interest || 0) - (a.interest || 0) || firstKickoff(a) - firstKickoff(b); });
      var item = pool[0];
      if (item) {
        host.appendChild(editorialItem(item));
        var by = document.createElement('p'); by.className = 'forecast__by';
        by.textContent = 'Forecast by Claude · ' + (later ? 'Beyond 24 hours' : 'Next 24 hours'); host.appendChild(by);
      } else {
        // A concise schedule fact covers a window without adding invented editorial copy.
        var p = document.createElement('p');
        p.textContent = matchCount(visible.length) + (later ? ' further ahead' : ' in the next 24 hours') +
          (mode === 'mine' ? ' on your services.' : ' in the selected competitions.');
        host.appendChild(p);
      }
      host.hidden = false;
    }
    sectionIntro(forecastEl, false);
    sectionIntro(document.getElementById('forecast-later'), true);

  }
  function applyStory(s) {
    if (!s || s.version !== 1 || typeof s.headline !== 'string' || typeof s.lede !== 'string') return;
    var written = Date.parse(s.generated_at || '');
    if (!(written > 0) || !storyIsCurrent(s, written, nowMs())) return;
    if (STORY.s && STORY.written >= written) return;   // the one on show already, or a newer one
    clearStory(true);
    var notes = {};
    Object.keys(s.notes || {}).forEach(function (id) { var n = s.notes[id]; if (n && typeof n.note === 'string' && n.note) notes[id] = n; });
    STORY = { notes: notes, s: s, written: written, rankings: s.rankings && typeof s.rankings === 'object' ? s.rankings : {} };
    renderLeagueOrder();
    rows.forEach(function (r) {
      var n = notes[r.getAttribute('data-id')]; if (!n) return;
      var meta = r.querySelector('.row__meta'); if (!meta || r.querySelector('.row__story')) return;
      meta.parentNode.insertBefore(storyLine(n, 'row__story'), meta.nextSibling);
    });
    render(true);
  }
  // Takes the story down: the section, the notes under the rows, and (at the next render) the notes
  // on the cards and Claude's forecast.
  function clearStory(quiet) {
    if (!STORY.s) return;
    STORY = { notes: {} };
    document.getElementById('story').hidden = true;
    Array.prototype.slice.call(document.querySelectorAll('.row__story')).forEach(function (el) { el.parentNode.removeChild(el); });
    if (!quiet) render(true);
  }
  function loadStory() {
    if (!window.fetch || location.protocol === 'file:') return;
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
    if (!r._tv || r._state === 'post' || compOff[r._lg] || now < r._k - LIVE.leadMs) return false;
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
    var goals = played ? liveGoals(comp, side) : [], g = r.querySelector('.row__goals');
    if (goals.length) setGoals(r, goals);
    else if (g && (!played || vals[0] === '0' && vals[1] === '0')) g.parentNode.removeChild(g);
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
    picksEl.querySelectorAll('.pick').forEach(function (a) { var w = a.querySelector('.pick__when'); if (a._row && w) setPickWhen(w, a._row, now); });
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
      else if (changed.clock) { render(false); refreshLiveText(); }
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
