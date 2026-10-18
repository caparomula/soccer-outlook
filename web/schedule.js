// Same-origin schedule snapshots update fixture facts without rerunning AI or reloading preferences.
// Publication owns country, package and replay checks; this reader never infers viewing rights.
(function (global) {
  'use strict';
  var EVERY_MS = 5 * 60000;
  function httpsUrl(value) {
    if (typeof value !== 'string' || /[\u0000-\u0020]/.test(value)) return false;
    try { var url = new URL(value); return url.protocol === 'https:' && !url.username && !url.password; } catch (e) { return false; }
  }
  function object(value) { return !!value && typeof value === 'object' && !Array.isArray(value); }
  function parse(payload, options) {
    if (!object(payload) || payload.version !== 1 || payload.complete !== true) throw new Error('Incomplete schedule');
    if (!options.revision || payload.runtime_revision !== options.revision) throw new Error('New page version');
    var generated = Date.parse(payload.generated_at);
    if (!isFinite(generated) || generated > Date.now() + 5 * 60000 || generated < Date.now() - 30 * 3600000) throw new Error('Stale schedule');
    if (generated <= options.after) return null;
    if (!Array.isArray(payload.rows) || payload.rows.length > 5000) throw new Error('Invalid fixtures');
    var ids = Object.create(null), total = 0;
    var rows = payload.rows.map(function (html) {
      if (typeof html !== 'string' || html.length > 150000 || (total += html.length) > 12000000) throw new Error('Invalid fixture markup');
      var template = document.createElement('template'); template.innerHTML = html;
      if (template.content.childNodes.length !== 1) throw new Error('Invalid fixture root');
      var row = template.content.firstElementChild;
      if (!row || !row.matches('li.row')) throw new Error('Invalid fixture root');
      [row].concat(Array.from(row.querySelectorAll('*'))).forEach(function (node) {
        if (!/^(LI|DIV|SPAN|I|B|A|P|STRONG|BUTTON|DETAILS|SUMMARY|UL|EM|SMALL|TIME)$/.test(node.tagName)) throw new Error('Unsafe fixture element');
        Array.from(node.attributes).forEach(function (attr) {
          if (!/^(class|hidden|title|type|role|href|target|rel|aria-[a-z-]+|data-[a-z-]+)$/.test(attr.name)) throw new Error('Unsafe fixture attribute');
          if (attr.name === 'href' && attr.value !== '#tables' && !httpsUrl(attr.value)) throw new Error('Unsafe fixture link');
        });
        if (node.tagName === 'BUTTON' && node.getAttribute('type') !== 'button') throw new Error('Invalid fixture button');
      });
      var id = row.dataset.id;
      if (!/^[a-zA-Z0-9_-]{1,80}$/.test(id || '') || ids[id]) throw new Error('Invalid fixture identity');
      ids[id] = true;
      if (!Object.prototype.hasOwnProperty.call(options.leagues, row.dataset.lg) ||
          !/^(pre|in|post)$/.test(row.dataset.state || '') || !/^[01]$/.test(row.dataset.tv || '') ||
          !isFinite(Date.parse(row.dataset.utc)) || !row.dataset.home || !row.dataset.away) throw new Error('Invalid fixture facts');
      if (row.querySelectorAll('.row__teams > .team').length !== 2 || !row.querySelector('.row__detail') ||
          !row.querySelector('.row__kickoff') || !row.querySelector('.row__footer .more')) throw new Error('Incomplete fixture');
      function outlet(value) {
        return object(value) && typeof value.l === 'string' && value.l.length > 0 && value.l.length <= 200 &&
          Array.isArray(value.v) && value.v.every(function (service) { return options.services.indexOf(service) >= 0; });
      }
      var outlets = JSON.parse(row.dataset.o || '[]'), rule = row.hasAttribute('data-r') ? JSON.parse(row.dataset.r) : null;
      if (!Array.isArray(outlets) || outlets.length > 50 || !outlets.every(outlet) || rule && !outlet(rule)) throw new Error('Invalid viewing routes');
      var watch = JSON.parse(row.dataset.watch || '{}');
      if (!object(watch)) throw new Error('Invalid viewing links');
      Object.keys(watch).forEach(function (network) {
        if (!object(watch[network])) throw new Error('Invalid viewing links');
        Object.keys(watch[network]).forEach(function (service) {
          if (options.services.indexOf(service) < 0 || !object(watch[network][service]) || !httpsUrl(watch[network][service].url)) throw new Error('Unsafe viewing link');
        });
      });
      // These objects are only read as facts; validating their shape also avoids a partial redraw.
      if (!object(JSON.parse(row.dataset.aiFacts || '{}')) || !object(JSON.parse(row.dataset.outlookParts || '{}'))) throw new Error('Invalid fixture context');
      return row;
    });
    var logos = payload.logos || {}, css = '';
    if (!object(logos) || Object.keys(logos).length > 5000) throw new Error('Invalid logo catalog');
    Object.keys(logos).forEach(function (key) {
      var url = logos[key];
      if (!/^[A-Za-z0-9_-]+$/.test(key) || typeof url !== 'string' || url.length > 500000 ||
          !(httpsUrl(url) || /^data:image\/(?:png|jpeg|webp|svg\+xml);base64,[A-Za-z0-9+/]+=*$/.test(url))) throw new Error('Invalid logo');
      // JSON quoting escapes quotes/backslashes before this URL enters a CSS string.
      css += '.l-' + key + '{background-image:url(' + JSON.stringify(url) + ')}';
    });
    return { generatedAt: generated, generated: payload.generated_at, rows: rows, logoCss: css };
  }
  function create(options) {
    var state = { busy: false, updatedAt: Date.parse(options.builtAt) || 0, okAt: 0, fails: 0, nextAt: 0, reason: '' };
    var active = null, suspended = false;
    // A navigation (including Safari's back/forward cache) ends the old document's
    // request. Its late promise callbacks must not redraw or report a source outage.
    function suspend() {
      suspended = true;
      var request = active; active = null; state.busy = false;
      if (request) {
        clearTimeout(request.timeout);
        if (request.controller) request.controller.abort();
      }
    }
    // WebKit may run an already-queued polling timer after beforeunload, before
    // pagehide; starting fetch in that gap produces an access-control exception.
    global.addEventListener('beforeunload', suspend);
    global.addEventListener('pagehide', suspend);
    global.addEventListener('pageshow', function () {
      if (!suspended) return;
      suspended = false; poll();
    });
    function poll() {
      if (suspended || !options.revision || !global.fetch || document.hidden || state.busy || Date.now() < state.nextAt) return;
      state.busy = true;
      var controller = global.AbortController ? new AbortController() : null;
      var request = { controller: controller, timeout: setTimeout(function () { if (controller) controller.abort(); }, 10000) };
      active = request;
      function current() { return active === request && !suspended; }
      return fetch('schedule.json', { mode: 'same-origin', cache: 'no-cache', credentials: 'omit', signal: controller ? controller.signal : undefined })
        .then(function (response) { if (!current()) return; if (!response.ok) throw new Error('Schedule unavailable'); return response.text(); })
        .then(function (text) {
          if (!current()) return;
          if (text.length > 16000000) throw new Error('Invalid schedule size');
          var snapshot = parse(JSON.parse(text), { revision: options.revision, after: state.updatedAt, leagues: options.leagues, services: options.services });
          if (snapshot) { options.apply(snapshot); state.updatedAt = snapshot.generatedAt; }
          state.okAt = Date.now(); state.fails = 0; state.reason = ''; state.nextAt = Date.now() + EVERY_MS;
        }).catch(function (error) {
          if (!current()) return;
          state.fails++; state.reason = error.message; state.nextAt = Date.now() + Math.min(EVERY_MS * Math.pow(2, state.fails - 1), 30 * 60000);
        }).then(function () {
          clearTimeout(request.timeout);
          if (!current()) return;
          active = null; state.busy = false;
          if (options.completed) options.completed(state);
        });
    }
    return { poll: poll, everyMs: EVERY_MS };
  }
  global.SoccerOutlookSchedule = { create: create };
})(window);
