/* Team vitals: optional, on-demand ESPN lookups. No profile request is made until a team is opened. */
(function () {
  'use strict';
  var CORE = 'https://sports.core.api.espn.com/v2/sports/soccer/teams/';
  var SITE = 'https://site.api.espn.com/apis/site/v2/sports/soccer/';
  var CACHE_KEY = 'soccer-team-vitals-v1', TTL = 6 * 3600000, pending = new Map(), memory = {};
  function text(value) { return typeof value === 'string' ? value : ''; }
  function list(value) { return Array.isArray(value) ? value : []; }
  function number(value) { return typeof value === 'number' && isFinite(value) && value >= 0 ? value : null; }
  function json(url) {
    var controller = new AbortController(), timer = setTimeout(function () { controller.abort(); }, 8000);
    return fetch(url, { signal: controller.signal, credentials: 'omit', referrerPolicy: 'no-referrer' }).then(function (r) {
      if (!r.ok) throw new Error('Team lookup unavailable');
      return r.json();
    }).finally(function () { clearTimeout(timer); });
  }
  function profile(data, id, league) {
    if (!data || String(data.id) !== id) throw new Error('Wrong team');
    // Follow no API-supplied URL. Extract only a league slug and construct our own HTTPS request.
    var ref = text(data.defaultLeague && data.defaultLeague.$ref);
    var match = /^https?:\/\/sports\.core\.api\.espn\.com\/v2\/sports\/soccer\/leagues\/([a-z0-9.-]+)(?:[/?]|$)/.exec(ref);
    var season = /\/seasons\/(\d{4})\//.exec(text(data.$ref));
    var venue = data.venue || {}, address = venue.address || {};
    return { league: data.isNational === true ? league : match ? match[1] : league,
      national: data.isNational === true, nickname: text(data.nickname), season: season ? +season[1] : null,
      venue: data.isNational === true ? '' : text(venue.fullName),
      city: data.isNational === true ? '' : [text(address.city), text(address.country)].filter(Boolean).join(', '),
      form: text(data.form) };
  }
  function roster(data, id, season) {
    if (!data || !data.team || String(data.team.id) !== id || !Array.isArray(data.athletes) ||
        !data.season || (season && data.season.year !== season)) throw new Error('No current squad');
    var players = data.athletes.filter(function (a) { return a && typeof a === 'object'; }).map(function (a) {
      var stats = {};
      list(a.statistics && a.statistics.splits && a.statistics.splits.categories).forEach(function (category) {
        list(category && category.stats).forEach(function (s) { if (s) stats[s.name] = number(s.value); });
      });
      return { name: text(a.displayName), position: text(a.position && a.position.displayName),
        goals: stats.totalGoals, assists: stats.goalAssists };
    }).filter(function (a) { return a.name && (a.goals || 0) + (a.assists || 0) > 0; });
    players.sort(function (a, b) {
      return (b.goals || 0) + (b.assists || 0) - (a.goals || 0) - (a.assists || 0) ||
        (b.goals || 0) - (a.goals || 0) || a.name.localeCompare(b.name);
    });
    // The coach field is deliberately unused: ESPN can return long-departed coaches here.
    var seasonName = text(data.season.displayName), stage = text(data.season.name);
    if (stage && stage !== seasonName) seasonName += (seasonName ? ' · ' : '') + stage;
    return { season: seasonName, standing: text(data.team.standingSummary),
      record: text(data.team.recordSummary), players: players.slice(0, 3) };
  }
  function cached(key) {
    try { memory = Object.assign({}, JSON.parse(localStorage.getItem(CACHE_KEY) || '{}'), memory); } catch (_) {}
    var entry = memory[key], age = entry ? Date.now() - entry.checked : -1;
    return age >= 0 && age < TTL && entry.profile && /^[a-z0-9.-]+$/.test(entry.profile.league) &&
      entry.roster && Array.isArray(entry.roster.players) && entry.roster.players.every(function (p) { return p && typeof p.name === 'string'; }) ? entry : null;
  }
  function save(key, value) {
    memory[key] = value;
    var fresh = {};
    Object.keys(memory).filter(function (k) {
      var age = memory[k] ? Date.now() - memory[k].checked : -1; return age >= 0 && age < TTL;
    }).sort(function (a, b) { return memory[b].checked - memory[a].checked; }).slice(0, 30).forEach(function (k) { fresh[k] = memory[k]; });
    memory = fresh;
    try { localStorage.setItem(CACHE_KEY, JSON.stringify(memory)); } catch (_) {}
  }
  function load(id, league) {
    if (!/^\d+$/.test(id) || !/^[a-z0-9.-]+$/.test(league)) return Promise.reject(new Error('No team identifier'));
    var key = league + ':' + id, hit = cached(key);
    if (hit) return Promise.resolve(hit);
    if (pending.has(key)) return pending.get(key);
    var value = { profile: null, roster: null }, request = json(CORE + id + '?lang=en&region=us').then(function (data) {
      value.profile = profile(data, id, league);
    }).catch(function () {}).then(function () {
      var p = value.profile, competition = p ? p.league : league;
      return json(SITE + competition + '/teams/' + id + '/roster').then(function (data) {
        // National squads use the match's competition, not the core profile's default competition.
        value.roster = roster(data, id, p && !p.national ? p.season : null);
      }).catch(function () {});
    }).then(function () {
      value.checked = Date.now();
      if (value.profile && value.roster) save(key, value);
      return value;
    }).finally(function () { pending.delete(key); });
    pending.set(key, request); return request;
  }
  function element(tag, cls, value) {
    var el = document.createElement(tag); if (cls) el.className = cls;
    if (value !== undefined) el.textContent = value; return el;
  }
  function recordText(record) {
    var m = /^(\d+)-(\d+)-(\d+)$/.exec(record || '');
    return m ? m[1] + ' won · ' + m[2] + ' drawn · ' + m[3] + ' lost' : text(record);
  }
  function formLine(form) {
    form = text(form).replace(/[^WDL]/g, '').slice(-5); if (!form) return null;
    var line = element('p'), cells = element('span', 'form');
    cells.setAttribute('role', 'img');
    cells.setAttribute('aria-label', 'Recent results: ' + form.split('').map(function (c) { return { W: 'win', D: 'draw', L: 'loss' }[c]; }).join(', '));
    form.split('').forEach(function (c) { cells.appendChild(element('b', 'f f-' + c.toLowerCase())); });
    line.append(cells, document.createTextNode('Last ' + form.length + ' matches'));
    return line;
  }
  function competitionSection(title, record, standing, form) {
    var section = element('section'); section.appendChild(element('h3', '', title));
    if (standing) section.appendChild(element('p', 'team-sheet__note', standing));
    if (record) section.appendChild(element('p', 'team-sheet__record', recordText(record)));
    var recent = formLine(form); if (recent) section.appendChild(recent);
    return section;
  }
  function init(options) {
    var dialog = document.getElementById('team-dialog'), content = document.getElementById('team-dialog-content');
    var status = document.getElementById('team-dialog-status'), retry = document.getElementById('team-dialog-retry');
    var active = null, backdrop = false, leagues = options.leagues || {};
    function render(state, value) {
      var t = state.team, p = value && value.profile, r = value && value.roster;
      var league = p ? p.league : t.league, fragment = document.createDocumentFragment();
      var standing = [t.rank ? 'Position ' + t.rank : '', t.points !== '' ? t.points + ' points' : '', t.group].filter(Boolean).join(' · ');
      if (p) {
        var facts = element('dl', 'team-sheet__facts');
        [['Home city', p.city], ['Home ground', p.venue], ['Nickname', p.nickname !== t.name ? p.nickname : ''],
          [p.national ? 'Team' : 'Home league', p.national ? 'National team' : leagues[league] || '']].forEach(function (fact) {
          if (!fact[1]) return;
          var pair = element('div'); pair.append(element('dt', '', fact[0]), element('dd', '', fact[1])); facts.appendChild(pair);
        });
        if (facts.childElementCount) fragment.appendChild(facts);
      }
      if (r) fragment.appendChild(competitionSection(r.season || leagues[league] || league,
        r.record || (league === t.league ? t.record : ''), r.standing || (league === t.league ? standing : ''),
        p && !p.national ? p.form : league === t.league ? t.form : ''));
      if (!r || league !== t.league) {
        var section = competitionSection(leagues[t.league] || t.league, t.record, standing, t.form);
        if (t.leader && t.goals && t.goals !== '0') section.appendChild(element('p', '', 'Top scorer: ' + t.leader + ' · ' + t.goals + (t.goals === '1' ? ' goal' : ' goals')));
        fragment.appendChild(section);
      }
      if (r && r.players.length) {
        var leaders = element('section'); leaders.appendChild(element('h3', '', 'Leading contributors'));
        leaders.appendChild(element('p', 'team-sheet__note', 'Most goals + assists · ' + r.season));
        var players = element('ul', 'team-sheet__players');
        r.players.forEach(function (player) {
          var item = element('li'), name = element('span'); name.appendChild(element('strong', '', player.name));
          if (player.position) name.appendChild(element('small', '', player.position));
          var stats = [];
          if (number(player.goals) !== null) stats.push(player.goals + (player.goals === 1 ? ' goal' : ' goals'));
          if (number(player.assists) !== null) stats.push(player.assists + (player.assists === 1 ? ' assist' : ' assists'));
          item.append(name, element('span', '', stats.join(' · '))); players.appendChild(item);
        });
        leaders.appendChild(players); fragment.appendChild(leaders);
      }
      content.replaceChildren(fragment);
      retry.hidden = !value || !!(p && r);
      status.textContent = !value ? 'Loading team profile…' : p && r ?
        'ESPN · checked ' + new Date(value.checked).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) :
        'Some team information is unavailable. Available schedule facts are shown above.';
    }
    function retrieve(state) {
      retry.hidden = true; status.textContent = 'Loading team profile…';
      load(state.team.id, state.team.league).then(function (value) {
        if (active === state && dialog.open) render(state, value);
      }).catch(function () { if (active === state && dialog.open) render(state, {}); });
    }
    document.addEventListener('click', function (ev) {
      var trigger = ev.target.closest('a.team-link');
      if (!trigger || ev.ctrlKey || ev.metaKey || ev.shiftKey || ev.altKey || ev.button) return;
      var team;
      try { team = JSON.parse(trigger.dataset.team); } catch (_) { return; }
      ev.preventDefault(); options.beforeOpen();
      var host = trigger.closest('[data-match-role]');
      active = { team: team, trigger: trigger, role: host && host.dataset.matchRole,
        match: host && (host.dataset.matchId || host.dataset.id) };
      document.getElementById('team-dialog-title').textContent = team.name;
      var badge = document.getElementById('team-dialog-badge'), original = trigger.querySelector('.logo');
      badge.replaceChildren(); if (original) badge.appendChild(original.cloneNode(true));
      var links = document.getElementById('team-dialog-links'); links.replaceChildren();
      [['club', 'Team on ESPN'], ['team/squad', 'Full squad'], ['team/stats', 'Team statistics']].forEach(function (item) {
        var a = element('a', '', item[1]); a.href = 'https://www.espn.com/soccer/' + item[0] + '/_/id/' + team.id;
        a.target = '_blank'; a.rel = 'noopener'; links.appendChild(a);
      });
      render(active, null); document.documentElement.classList.add('has-team-dialog'); dialog.showModal();
      dialog.scrollTop = 0; retrieve(active);
    });
    retry.addEventListener('click', function () {
      if (active) { document.getElementById('team-dialog-close').focus(); retrieve(active); }
    });
    document.getElementById('team-dialog-close').addEventListener('click', function () { dialog.close(); });
    function visible(el) { return el && el.isConnected && el.getClientRects().length && !el.closest('details:not([open])'); }
    dialog.addEventListener('close', function () {
      var previous = active; active = null; backdrop = false; document.documentElement.classList.remove('has-team-dialog');
      if (!previous) return;
      var trigger = previous.trigger;
      if (!visible(trigger)) {
        trigger = Array.from(document.querySelectorAll('a.team-link')).find(function (el) {
          var host = el.closest('[data-match-role]');
          return visible(el) && host && host.dataset.matchRole === previous.role &&
            (host.dataset.matchId || host.dataset.id) === previous.match && el.dataset.team === previous.trigger.dataset.team;
        });
      }
      (visible(trigger) ? trigger : options.fallbackFocus).focus({ preventScroll: true });
    });
    dialog.addEventListener('keydown', function (ev) {
      if (ev.key !== 'Tab') return;
      var stops = Array.from(dialog.querySelectorAll('button, a[href]')).filter(visible), first = stops[0], last = stops[stops.length - 1];
      if (ev.shiftKey && document.activeElement === first) { ev.preventDefault(); last.focus(); }
      else if (!ev.shiftKey && document.activeElement === last) { ev.preventDefault(); first.focus(); }
    });
    function outside(ev) {
      var r = dialog.getBoundingClientRect(); return ev.clientX < r.left || ev.clientX > r.right || ev.clientY < r.top || ev.clientY > r.bottom;
    }
    dialog.addEventListener('pointerdown', function (ev) { backdrop = ev.target === dialog && outside(ev); });
    dialog.addEventListener('click', function (ev) { if (backdrop && ev.target === dialog && outside(ev)) dialog.close(); });
  }
  window.SoccerTeams = { init: init };
}());
