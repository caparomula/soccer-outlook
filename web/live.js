// Live-score transport and scheduling. The page supplies match metadata and applies returned events;
// this module knows nothing about cards, filters, or their DOM. build.py embeds it before app.js.
(function (global) {
  'use strict';
  function create(options) {
    var everyMs = 60000, leadMs = 15 * 60000, tailMs = 4 * 3600000, lookbackMs = 30 * 3600000;
    var base = 'https://site.api.espn.com/apis/site/v2/sports/soccer/', checked = Object.create(null);
    var state = { busy: false, startedAt: 0, okAt: 0, fails: 0, nextAt: 0 };
    var active = null, suspended = false;
    function suspend() {
      suspended = true;
      var batch = active; active = null; state.busy = false;
      if (batch) {
        state.startedAt = 0;
        batch.cancel.forEach(function (cancel) { cancel(); });
      }
    }
    // Safari may run a queued poll between beforeunload and pagehide. Stop before
    // that gap, and resume if the document returns from the back/forward cache.
    global.addEventListener('beforeunload', suspend);
    global.addEventListener('pagehide', suspend);
    global.addEventListener('pageshow', function () {
      if (!suspended) return;
      suspended = false; poll();
    });
    // Offline browser checks may replace ESPN only when the page itself is served from localhost.
    var param = /[?&]scoresbase=([^&#]+)/.exec(location.search), override = '';
    try { override = param ? decodeURIComponent(param[1]) : ''; } catch (e) {}
    if (/^(localhost|127\.0\.0\.1)$/.test(location.hostname) && /^https?:\/\/(localhost|127\.0\.0\.1)(:\d+)?\//.test(override)) base = override;

    function due(match, now) {
      if (!match.eligible || now < match.kickoff - leadMs || now >= match.kickoff + lookbackMs) return false;
      if (now < match.kickoff + tailMs) return true;
      // One successful catch-up after the live window, even if this tab already saw the match live.
      // A tab returning after sleep can then get the final score. Failed requests don't consume it;
      // a successful response does, so an unfinished or missing event isn't polled indefinitely.
      return checked[match.id] === undefined || checked[match.id] < match.kickoff + tailMs;
    }
    function getJson(url, batch) {
      return new Promise(function (resolve, reject) {
        var ctl = global.AbortController ? new AbortController() : null, done = false;
        function finish(fn, value) { if (done) return; done = true; clearTimeout(timer); fn(value); }
        var timer = setTimeout(function () { if (ctl) ctl.abort(); finish(reject, new Error('timeout')); }, 8000);
        batch.cancel.push(function () { if (ctl) ctl.abort(); finish(reject, new Error('cancelled')); });
        fetch(url, { cache: 'no-store', credentials: 'omit', signal: ctl ? ctl.signal : undefined })
          .then(function (res) { if (!res.ok) throw new Error('HTTP ' + res.status); return res.json(); })
          .then(function (data) { finish(resolve, { data: data, receivedAt: new Date().toISOString() }); }, function (error) { finish(reject, error); });
      });
    }
    function poll() {
      if (suspended || !global.fetch || !global.Promise || state.busy || document.hidden) return;
      if (Date.now() < state.nextAt || Date.now() - state.startedAt < 20000) return;
      var now = options.now(), groups = Object.create(null);
      options.matches().forEach(function (match) {
        if (!due(match, now)) return;
        var key = match.league + '/' + options.dateKey(match.kickoff);
        (groups[key] = groups[key] || []).push(match);
      });
      var keys = Object.keys(groups); if (!keys.length) return;
      state.busy = true; state.startedAt = Date.now();
      var batch = { cancel: [] }; active = batch;
      function current() { return active === batch && !suspended; }
      var changed = { state: false, score: false, clock: false, goals: false }, ok = 0;
      return Promise.all(keys.map(function (key) {
        var parts = key.split('/');
        return getJson(base + encodeURIComponent(parts[0]) + '/scoreboard?dates=' + parts[1] + '&limit=200', batch).then(function (response) {
          if (!current()) return;
          var events = response.data && response.data.events;
          if (!Array.isArray(events)) throw new Error('Invalid scoreboard');
          ok++;
          var byId = Object.create(null);
          events.forEach(function (event) { if (event && event.id != null) byId[String(event.id)] = event; });
          groups[key].forEach(function (match) {
            var first = checked[match.id] === undefined, event = byId[match.id];
            checked[match.id] = options.now();
            try { if (event) options.applyEvent(match.id, event, first, changed, response.receivedAt); } catch (e) {}
          });
          if (options.responded) options.responded(changed);
        }).catch(function () {});
      })).then(function () {
        if (!current()) return;
        active = null; state.busy = false;
        if (ok) { state.okAt = Date.now(); state.fails = 0; state.nextAt = 0; }
        else { state.fails++; state.nextAt = Date.now() + Math.min(everyMs * Math.pow(2, state.fails), 10 * 60000) - 2000; }
        options.completed(state, changed);
      });
    }
    return { poll: poll, everyMs: everyMs };
  }
  global.SoccerOutlookLive = { create: create };
})(window);
