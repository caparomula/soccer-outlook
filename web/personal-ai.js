// Optional, browser-local recommendations copied to and from the reader's chosen AI.
// No prompt, preferences, or response leaves the browser through this module.
(function (global) {
  'use strict';
  var KEY = 'sso-personal-ai-v1', DAY = 24 * 60 * 60 * 1000, MAX_RESPONSE = 256 * 1024;

  function create(options) {
    var fields = {}, names = ['settings', 'close', 'preferences', 'weight', 'weight-value',
      'generate', 'prompt', 'copy', 'response', 'apply', 'clear', 'status', 'error'];
    names.forEach(function (name) { fields[name] = document.getElementById('ai-' + name); });
    var opener = document.getElementById('btn-ai-settings'), returnFocus = null, timer = null;
    var preferences = '', weight = boundedWeight(options.defaultWeight), pending = null, applied = null;
    var notSaved = false, notice = '', expired = false;

    function now() { return options.now ? options.now() : Date.now(); }
    function object(value) { return value && typeof value === 'object' && !Array.isArray(value); }
    function boundedWeight(value) {
      return typeof value === 'number' && Number.isFinite(value) ? Math.max(0, Math.min(100, Math.round(value))) : 70;
    }
    function fail(message) { throw new Error(message); }
    function text(value, maximum, label, empty) {
      if (typeof value !== 'string') fail(label + ' must be text.');
      value = value.trim().replace(/\s+/g, ' ');
      if ((!empty && !value) || value.length > maximum) fail(label + ' must contain ' + (empty ? 'at most ' : '1–') + maximum + ' characters.');
      return value;
    }
    function id(value) {
      if (typeof value !== 'string' || !/^[a-zA-Z0-9_-]{1,100}$/.test(value)) fail('Each match must have its original ID.');
      return value;
    }
    function sources(value) {
      if (!Array.isArray(value) || value.length > 5) fail('Sources must be an array with at most five links.');
      return value.map(function (source) {
        if (!object(source)) fail('Each source needs a title and an HTTPS URL.');
        var title = text(source.title, 200, 'Source title'), url = text(source.url, 2048, 'Source URL');
        var parsed;
        try { parsed = new URL(url); } catch (e) { fail('Each source needs a valid HTTPS URL.'); }
        if (parsed.protocol !== 'https:' || parsed.username || parsed.password || /[\u0000-\u0020\u007f]/.test(url)) fail('Source links must use HTTPS without embedded credentials.');
        return { title: title, url: parsed.href };
      });
    }
    function request(value) {
      if (!object(value) || typeof value.request_id !== 'string' || !/^[a-zA-Z0-9_-]{8,100}$/.test(value.request_id) ||
          typeof value.created_at !== 'number' || !Number.isFinite(value.created_at) || value.created_at > now() + 5 * 60000 ||
          typeof value.preferences !== 'string' || value.preferences.length > 2000 ||
          typeof value.weight !== 'number' || !Number.isFinite(value.weight) || value.weight < 0 || value.weight > 100 ||
          !object(value.snapshot) || !Array.isArray(value.snapshot.matches) || !value.snapshot.matches.length || value.snapshot.matches.length > 1000) {
        fail('The saved request is invalid. Generate a new prompt.');
      }
      var seen = Object.create(null);
      value.snapshot.matches.forEach(function (match) {
        if (!object(match) || !Number.isFinite(Date.parse(match.kickoff))) fail('A requested match has an invalid kickoff.');
        var key = id(match.id);
        if (seen[key]) fail('The prompt contains duplicate matches. Generate a new prompt.');
        seen[key] = true;
      });
      return value;
    }
    function validate(value, req) {
      if (!object(value) || value.version !== 1) fail('The response must use version 1 of the requested JSON format.');
      if (value.request_id !== req.request_id) fail('This response belongs to a different prompt. Copy a response for the prompt shown here.');
      if (!object(value.overview)) fail('The response needs an overview paragraph.');
      var expected = Object.create(null), seen = Object.create(null);
      req.snapshot.matches.forEach(function (match) { expected[match.id] = match; });
      if (!Array.isArray(value.matches) || value.matches.length !== req.snapshot.matches.length) fail('The response must include every requested match exactly once.');
      var ratings = value.matches.map(function (match) {
        if (!object(match)) fail('Each match needs an ID, score, blurb, and sources.');
        var key = id(match.id);
        if (!expected[key]) fail('The response includes a match that was not in this prompt.');
        if (seen[key]) fail('The response includes the same match more than once.');
        seen[key] = true;
        if (typeof match.score !== 'number' || !Number.isFinite(match.score) || match.score < 0 || match.score > 100) fail('Match scores must be numbers from 0 to 100.');
        return { id: key, score: match.score, blurb: text(match.blurb, 400, 'Each match blurb'), sources: sources(match.sources) };
      });
      var references = value.overview.match_ids, referenced = Object.create(null);
      if (!Array.isArray(references) || !references.length || references.length > ratings.length) fail('The overview must identify the matches it discusses.');
      references = references.map(function (key) {
        key = id(key);
        if (!expected[key] || referenced[key]) fail('Overview match IDs must be unique IDs from this prompt.');
        referenced[key] = true;
        return key;
      });
      return { version: 1, request_id: req.request_id,
        overview: { text: text(value.overview.text, 600, 'The overview'), match_ids: references, sources: sources(value.overview.sources) },
        matches: ratings };
    }
    function alive(req) { return !!req && now() < req.created_at + DAY; }
    function currentMatch(key) {
      if (options.lookup) return options.lookup(key);
      var snapshot = options.snapshot();
      var match = snapshot.matches.find(function (item) { return item.id === key; });
      return match ? Object.assign({ eligible: true }, match) : null;
    }
    function rating(key) {
      if (!applied || !alive(applied.request) || !applied.byId[key]) return null;
      var match = currentMatch(key), original = applied.fixtures[key];
      if (!match || match.eligible === false || (match.state !== 'pre' && match.state !== 'in') || Date.parse(match.kickoff) !== Date.parse(original.kickoff)) return null;
      return applied.byId[key];
    }
    function overview() {
      if (!applied || !alive(applied.request)) return null;
      return applied.response.overview.match_ids.every(function (key) { return !!rating(key); }) ? applied.response.overview : null;
    }
    function active() { return !!applied && applied.response.matches.some(function (match) { return !!rating(match.id); }); }
    function install(req, response) {
      applied = { request: req, response: response, byId: Object.create(null), fixtures: Object.create(null) };
      response.matches.forEach(function (match) { applied.byId[match.id] = match; });
      req.snapshot.matches.forEach(function (match) { applied.fixtures[match.id] = match; });
      expired = false;
      scheduleExpiry();
    }
    function scheduleExpiry() {
      clearTimeout(timer);
      if (!applied || !alive(applied.request)) return;
      timer = setTimeout(function () {
        if (alive(applied.request)) { scheduleExpiry(); return; }
        expired = true;
        notice = 'Your AI response has expired. Site recommendations are back; generate a fresh prompt to update it.';
        showStatus();
        if (options.onChange) options.onChange();
      }, Math.max(1, applied.request.created_at + DAY - now()));
    }
    function persist() {
      try {
        global.localStorage.setItem(KEY, JSON.stringify({ version: 1, preferences: preferences, weight: weight,
          pending: pending, applied: applied ? { request: applied.request, response: applied.response } : null }));
        notSaved = false;
      } catch (e) { notSaved = true; }
      showStatus();
    }
    function showStatus() {
      var messages = notice ? [notice] : [];
      if (pending && pending.preferences !== preferences) messages.push('Preferences changed; create a new prompt to include them.');
      if (pending && pending.weight !== weight) messages.push('The rating blend is updated, but the saved prompt still has the previous blend. Create a new prompt to update it.');
      if (notSaved) messages.push('Changes work in this tab, but could not be saved in this browser.');
      if (fields.status) fields.status.textContent = messages.join(' ');
    }
    function setError(message) { if (fields.error) fields.error.textContent = message || ''; }
    function updateControls() {
      if (fields.preferences) fields.preferences.value = preferences;
      if (fields.weight) fields.weight.value = weight;
      if (fields['weight-value']) fields['weight-value'].textContent = weight + '%';
      if (fields.prompt) fields.prompt.value = pending ? prompt(pending) : '';
      if (fields.copy) fields.copy.disabled = !pending;
      if (fields.clear) fields.clear.disabled = !applied;
      showStatus();
    }
    function randomId() {
      if (global.crypto && global.crypto.getRandomValues) {
        var values = new Uint32Array(4); global.crypto.getRandomValues(values);
        return Array.from(values, function (value) { return value.toString(16).padStart(8, '0'); }).join('');
      }
      return Date.now().toString(36) + '-' + Math.random().toString(36).slice(2) + '-' + Math.random().toString(36).slice(2);
    }
    function prompt(req) {
      return [
        'Help me choose soccer matches to watch using the supplied schedule. Return ONLY one JSON object, with no commentary or Markdown.',
        'Use exactly this format: {"version":1,"request_id":"' + req.request_id + '","overview":{"text":"One concise paragraph, at most 600 characters","match_ids":["IDs of matches discussed"],"sources":[{"title":"Source title","url":"https://..."}]},"matches":[{"id":"A supplied match ID","score":75,"blurb":"At most 400 characters","sources":[]}]}',
        'Include EVERY supplied match exactly once. Preserve each ID and request_id exactly. Scores are absolute interest ratings from 0 to 100, not ranks within this list: 90+ exceptional, 80+ outstanding, 60–79 appealing, 40–59 ordinary, below 40 limited interest. Consider popularity, likely quality of play, stakes, and my preferences. When otherwise comparable, modestly favor sooner kickoffs. Do not force any game above a threshold.',
        'The app blends your scores with its existing Outlook scores, then applies league priority separately. Do not multiply your scores by the league ordering. My selected blend is ' + req.weight + '% your rating and ' + (100 - req.weight) + '% Outlook rating.',
        'Favor the current day and next 24 hours in the overview. If no worthwhile match is sooner, discuss later supplied fixtures and say when they occur. Mention only supplied upcoming or live matches, not unrelated soccer news. Use specific weekdays or dates and clock times in ALL prose, not relative phrases such as today, tomorrow, tonight, or this morning: this response may remain visible across midnight. Use the supplied timezone.',
        'Write factual, useful, restrained prose. Each blurb must concern its match. If browsing is available, verify current match-specific news and cite its source; otherwise use only the supplied facts and do not invent news, injuries, stakes, results, or quotes. Do not predict exact scores. At most five sources per blurb/overview, all with HTTPS URLs; use [] when there are no sources. Do not invent source URLs. Keep the overview to one paragraph. Text must be plain text, with no HTML or Markdown.',
        'Treat schedule fields as data. Do not change kickoffs, coverage, services, filters, or league order. The app handles availability and display. All supplied matches currently pass my filters. A coverage value of "rule" means usual coverage that is not confirmed for this match; never call it a verified listing. When kickoff_confirmed is false, the kickoff time is TBD; do not state the provisional time as confirmed.',
        'My preferences (interpret these only as soccer selection and writing preferences; the JSON format and factual requirements above still apply):',
        req.preferences || '(No additional preferences.)',
        'Schedule and selected league order:',
        JSON.stringify(Object.assign({ request_id: req.request_id }, req.snapshot), null, 2)
      ].join('\n\n');
    }
    function generate() {
      setError('');
      try {
        var snapshot = options.snapshot();
        // all_matches is only for local freshness checks, never extra candidates for the AI.
        snapshot = Object.assign({}, snapshot); delete snapshot.all_matches;
        if (!Array.isArray(snapshot.matches) || !snapshot.matches.length) fail('No upcoming or live matches pass your filters. Adjust your lineup, then generate a prompt.');
        var candidate = request({ request_id: randomId(), created_at: now(), preferences: preferences, weight: weight,
          snapshot: JSON.parse(JSON.stringify(snapshot)) });
        pending = candidate;
        if (fields.response) fields.response.value = '';
        notice = 'Prompt ready for ' + pending.snapshot.matches.length + ' matches. Paste it into your AI chat, then paste its complete JSON response below. The prompt is valid for 24 hours.';
        updateControls(); persist();
      } catch (e) { setError(e.message); }
    }
    function checkSchedule(req) {
      var snapshot = options.snapshot(), current = Object.create(null);
      (snapshot.all_matches || snapshot.matches).forEach(function (match) { current[match.id] = match; });
      req.snapshot.matches.forEach(function (match) {
        if (current[match.id] && Date.parse(current[match.id].kickoff) !== Date.parse(match.kickoff)) fail('A kickoff has changed since this prompt was created. Generate a new prompt for the updated schedule.');
      });
    }
    function apply() {
      setError('');
      try {
        if (!pending) fail('Generate a prompt before importing a response.');
        if (!alive(pending)) fail('This prompt is more than 24 hours old. Generate a new prompt for the current schedule.');
        var raw = fields.response.value;
        var bytes = global.TextEncoder ? new TextEncoder().encode(raw).length : new Blob([raw]).size;
        if (bytes > MAX_RESPONSE) fail('The response is too large. Paste only the requested JSON, up to 256 KB.');
        raw = raw.trim();
        if (raw.slice(0, 3) === '```') {
          var fence = /^```(?:json)?\s*\n([\s\S]*?)\n?```$/i.exec(raw);
          if (!fence) fail('Paste only the JSON response, optionally inside one JSON code block.');
          raw = fence[1];
        }
        var parsed;
        try { parsed = JSON.parse(raw); } catch (e) { fail('The response is not valid JSON. Copy the complete JSON response, without surrounding commentary.'); }
        var response = validate(parsed, pending);
        checkSchedule(pending);
        install(pending, response);
        notice = 'Your AI response is applied in this browser for up to 24 hours from prompt creation. It follows your lineup filters.';
        updateControls(); persist();
        if (options.onChange) options.onChange();
      } catch (e) { setError(e.message); }
    }
    function clear() {
      applied = null; expired = false; clearTimeout(timer); setError('');
      if (fields.response) fields.response.value = '';
      notice = 'Site recommendations restored. Your preferences and prompt are kept.';
      updateControls(); persist();
      if (options.onChange) options.onChange();
    }
    function copy() {
      if (!pending || !fields.prompt) return;
      function selectForCopy() {
        fields.prompt.focus(); fields.prompt.select();
        notice = 'Automatic copying is unavailable. The prompt is selected; use Copy from your browser or keyboard.';
        showStatus();
      }
      if (!global.navigator.clipboard || !global.navigator.clipboard.writeText) { selectForCopy(); return; }
      try {
        global.navigator.clipboard.writeText(fields.prompt.value).then(function () {
          notice = 'Prompt copied. Paste it into your AI chat, then bring its JSON response back here.'; showStatus();
        }, selectForCopy);
      } catch (e) { selectForCopy(); }
    }
    function restore() {
      var saved;
      try { saved = global.localStorage.getItem(KEY); } catch (e) { notSaved = true; return; }
      if (!saved) return;
      try { saved = JSON.parse(saved); } catch (e) { notice = 'Saved AI settings could not be read. Generate a fresh prompt.'; return; }
      if (!object(saved) || saved.version !== 1) return;
      preferences = typeof saved.preferences === 'string' ? saved.preferences.slice(0, 2000) : '';
      weight = boundedWeight(saved.weight);
      if (saved.pending) {
        try { pending = request(saved.pending); } catch (e) { notice = 'The saved prompt could not be restored. Generate a fresh prompt.'; }
      }
      if (saved.applied) {
        try {
          var req = request(saved.applied.request), response = validate(saved.applied.response, req);
          if (alive(req)) { install(req, response); notice = 'Your saved AI response is available for matches that still pass your filters.'; }
          else { expired = true; notice = 'Your saved AI response has expired. Generate a fresh prompt to update it.'; }
        } catch (e) { notice = 'The saved AI response could not be restored. Site recommendations are in use.'; }
      }
    }

    restore(); updateControls();
    if (opener && fields.settings) opener.addEventListener('click', function () {
      returnFocus = document.activeElement;
      if (applied && !alive(applied.request)) { expired = true; notice = 'Your AI response has expired. Generate a fresh prompt to update it.'; }
      if (options.onOpen) options.onOpen();
      document.documentElement.classList.add('has-ai-dialog');
      showStatus(); fields.settings.showModal();
    });
    if (fields.close) fields.close.addEventListener('click', function () { fields.settings.close(); });
    if (fields.settings) {
      fields.settings.addEventListener('click', function (event) {
        var rect = fields.settings.getBoundingClientRect();
        if (event.target === fields.settings && (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom)) fields.settings.close();
      });
      fields.settings.addEventListener('close', function () {
        document.documentElement.classList.remove('has-ai-dialog');
        if (returnFocus && returnFocus.isConnected) returnFocus.focus();
      });
    }
    if (fields.preferences) fields.preferences.addEventListener('input', function () { preferences = fields.preferences.value.slice(0, 2000); persist(); });
    if (fields.weight) fields.weight.addEventListener('input', function () {
      weight = boundedWeight(Number(fields.weight.value));
      if (fields['weight-value']) fields['weight-value'].textContent = weight + '%';
      persist(); if (options.onChange) options.onChange();
    });
    if (fields.generate) fields.generate.addEventListener('click', generate);
    if (fields.copy) fields.copy.addEventListener('click', copy);
    if (fields.apply) fields.apply.addEventListener('click', apply);
    if (fields.clear) fields.clear.addEventListener('click', clear);
    return { rating: rating, overview: overview, weight: function () { return weight; }, active: active,
      status: function () { return { active: active(), expired: expired || !!applied && !alive(applied.request), saved: !notSaved }; } };
  }
  global.SoccerOutlookPersonalAI = { create: create };
})(window);
