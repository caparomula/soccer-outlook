// Browser-local recommendations and a shared reader with the same validation and freshness rules.
// No automatic network access; callers may explicitly publish the sanitized export.
(function (global) {
  'use strict';
  var KEY = 'sso-personal-ai-v1', DAY = 24 * 60 * 60 * 1000, MAX_RESPONSE = 256 * 1024, MAX_SHARED = 512 * 1024;

  function create(options, headless) {
    var fields = {}, names = ['preferences', 'weight', 'weight-value',
      'generate', 'prompt', 'copy', 'response', 'apply', 'clear', 'status', 'error'];
    if (!headless) names.forEach(function (name) { fields[name] = document.getElementById('ai-' + name); });
    var timer = null;
    var preferences = '', weight = boundedWeight(options.defaultWeight), pending = null, applied = null;
    var notSaved = false, notice = '', expired = false, obsoletePrompt = false;

    function now() { return options.now ? options.now() : Date.now(); }
    function object(value) { return value && typeof value === 'object' && !Array.isArray(value); }
    function boundedWeight(value) {
      return typeof value === 'number' && Number.isFinite(value) ? Math.max(0, Math.min(100, Math.round(value))) : 70;
    }
    function fail(message) { throw new Error(message); }
    function byteSize(value) { return global.TextEncoder ? new TextEncoder().encode(value).length : new Blob([value]).size; }
    function exactKeys(value, allowed, label) {
      if (!object(value) || Object.keys(value).some(function (key) { return allowed.indexOf(key) === -1; })) fail(label + ' contains unsupported fields.');
    }
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
    function schema(req) { return req.schema_version === undefined ? 1 : req.schema_version; }
    function leagueId(value) {
      if (typeof value !== 'string' || !/^[a-zA-Z0-9_.-]{1,100}$/.test(value)) fail('Each headline must use a supplied league ID.');
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
    function request(value, shared) {
      if (!object(value) || typeof value.request_id !== 'string' || !/^[a-zA-Z0-9_-]{8,100}$/.test(value.request_id) ||
          typeof value.created_at !== 'number' || !Number.isFinite(value.created_at) || value.created_at > now() + 5 * 60000 ||
          typeof value.preferences !== 'string' || value.preferences.length > 2000 ||
          typeof value.weight !== 'number' || !Number.isFinite(value.weight) || value.weight < 0 || value.weight > 100 ||
          (schema(value) !== 1 && schema(value) !== 2) ||
          !object(value.snapshot) || !Array.isArray(value.snapshot.matches) || value.snapshot.matches.length > 1000 ||
          (schema(value) === 1 && !value.snapshot.matches.length)) {
        fail('The saved request is invalid. Generate a new prompt.');
      }
      var leagues = Object.create(null);
      if (schema(value) === 2) {
        if (!Array.isArray(value.snapshot.league_order) || !value.snapshot.league_order.length ||
            (!shared && (!Array.isArray(value.snapshot.services) || !value.snapshot.services.length))) fail('Select at least one league and broadcaster, then generate a prompt.');
        value.snapshot.league_order.forEach(function (league) {
          if (!object(league)) fail('The saved league order is invalid. Generate a new prompt.');
          var key = leagueId(league.id);
          if (leagues[key]) fail('The saved league order contains a duplicate. Generate a new prompt.');
          leagues[key] = true;
        });
      }
      var seen = Object.create(null);
      value.snapshot.matches.forEach(function (match) {
        if (!object(match) || !Number.isFinite(Date.parse(match.kickoff))) fail('A requested match has an invalid kickoff.');
        var key = id(match.id);
        if (seen[key]) fail('The prompt contains duplicate matches. Generate a new prompt.');
        if (schema(value) === 2 && !leagues[match.league_id]) fail('A requested match has an unknown league. Generate a new prompt.');
        seen[key] = true;
      });
      return value;
    }
    function validateHeadlines(value, req, expected) {
      if (!Array.isArray(value)) fail('The response needs a headlines array; use [] if there is no useful league news.');
      if (!Array.isArray(req.snapshot.league_order)) fail('This saved prompt has no league list. Generate a new prompt.');
      var leagues = Object.create(null), seen = Object.create(null);
      req.snapshot.league_order.forEach(function (league) { leagues[league.id] = true; });
      if (value.length > req.snapshot.league_order.length) fail('Include at most one headline for each supplied league.');
      return value.map(function (item) {
        if (!object(item)) fail('Each headline needs a league ID, title, interest, match IDs, sources, and expiry.');
        var key = leagueId(item.league_id);
        if (!leagues[key]) fail('A headline names a league that was not in this prompt.');
        if (seen[key]) fail('Include at most one headline for each league.');
        seen[key] = true;
        if (typeof item.interest !== 'number' || !Number.isFinite(item.interest) || item.interest < 0 || item.interest > 100) fail('Headline interest must be a number from 0 to 100.');
        if (!Array.isArray(item.match_ids) || item.match_ids.length > req.snapshot.matches.length) fail('Headline match IDs must be an array of IDs from this prompt.');
        var referenced = Object.create(null);
        var references = item.match_ids.map(function (matchId) {
          matchId = id(matchId);
          if (!expected[matchId] || referenced[matchId]) fail('Headline match IDs must be unique IDs from this prompt.');
          if (expected[matchId].league_id !== key) fail('Each headline can reference only matches from its own league.');
          referenced[matchId] = true;
          return matchId;
        });
        var links = sources(item.sources);
        if (!references.length && !links.length) fail('A league calendar notice without match IDs needs a verified source.');
        var expires = typeof item.expires_at === 'string' ? Date.parse(item.expires_at) : NaN;
        if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?(?:Z|[+-]\d{2}:\d{2})$/.test(item.expires_at) ||
            !Number.isFinite(expires) || expires <= req.created_at || expires > req.created_at + DAY) fail('Each headline needs an ISO expiry after prompt creation and no later than response_expires_at.');
        return { league_id: key, headline: text(item.headline, 80, 'Each headline'),
          lede: text(item.lede === undefined ? '' : item.lede, 240, 'Each headline summary', true),
          interest: item.interest, match_ids: references, sources: links, expires_at: new Date(expires).toISOString() };
      });
    }
    function validate(value, req) {
      if (!object(value) || (value.version !== 1 && value.version !== 2)) fail('The response must use version 1 or 2 of the requested JSON format.');
      var version = value.version;
      if (value.request_id !== req.request_id) fail('This response belongs to a different prompt. Copy a response for the prompt shown here.');
      if (version === 1 && !object(value.overview)) fail('The response needs an overview paragraph.');
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
        return { id: key, score: match.score, blurb: text(match.blurb, 400, 'Each match blurb', true), sources: sources(match.sources) };
      });
      if (version === 2) return { version: 2, request_id: req.request_id, headlines: validateHeadlines(value.headlines, req, expected), matches: ratings };
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
      if (!options.snapshot) return null;
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
      if (!applied || !alive(applied.request) || applied.response.version !== 1) return null;
      return applied.response.overview.match_ids.every(function (key) { return !!rating(key); }) ? applied.response.overview : null;
    }
    function headlines() {
      if (!applied || !alive(applied.request) || applied.response.version !== 2) return null;
      return applied.response.headlines.filter(function (item) {
        var eligible = options.leagueEligible ? options.leagueEligible(item.league_id) :
          options.snapshot && options.snapshot().services.length && options.snapshot().league_order.some(function (league) { return league.id === item.league_id; });
        return eligible && now() < Date.parse(item.expires_at) && item.match_ids.every(function (key) { return !!rating(key); });
      });
    }
    function active() { return !!applied && applied.response.matches.some(function (match) { return !!rating(match.id); }); }
    function install(req, response) {
      applied = { request: req, response: response, byId: Object.create(null), fixtures: Object.create(null) };
      response.matches.forEach(function (match) { applied.byId[match.id] = match; });
      req.snapshot.matches.forEach(function (match) { applied.fixtures[match.id] = match; });
      expired = false;
      scheduleExpiry();
    }
    function sharedCandidate(value) {
      var raw;
      try { raw = JSON.stringify(value); } catch (e) { fail('Shared AI data must be a JSON object.'); }
      if (!raw || byteSize(raw) > MAX_SHARED) fail('Shared AI data must be a JSON object no larger than 512 KB.');
      // Detach from the caller before validation; later mutations cannot change an installed response.
      value = JSON.parse(raw);
      exactKeys(value, ['version', 'created_at', 'weight', 'leagues', 'fixtures', 'response'], 'Shared AI data');
      if (value.version !== 1 || !Array.isArray(value.leagues) || !value.leagues.length || value.leagues.length > 1000 ||
          !Array.isArray(value.fixtures) || value.fixtures.length > 1000 || !object(value.response)) fail('Shared AI data has an invalid format.');
      if (byteSize(JSON.stringify(value.response)) > MAX_RESPONSE) fail('The shared AI response is larger than 256 KB.');
      var fixtures = value.fixtures.map(function (match) {
        exactKeys(match, ['id', 'kickoff', 'league_id'], 'A shared fixture');
        if (typeof match.kickoff !== 'string' || !Number.isFinite(Date.parse(match.kickoff))) fail('A shared fixture has an invalid kickoff.');
        return { id: id(match.id), kickoff: new Date(Date.parse(match.kickoff)).toISOString(), league_id: leagueId(match.league_id) };
      });
      var req = request({ schema_version: 2, request_id: value.response.request_id, created_at: value.created_at,
        weight: value.weight, preferences: '', snapshot: { matches: fixtures,
          league_order: value.leagues.map(function (key) { return { id: leagueId(key) }; }) } }, true);
      if (!alive(req)) fail('The shared AI response has expired. Generate a fresh response before publishing.');
      var response = validate(value.response, req);
      return { request: req, response: response, payload: { version: 1, created_at: req.created_at, weight: req.weight,
        leagues: req.snapshot.league_order.map(function (league) { return league.id; }), fixtures: fixtures, response: response } };
    }
    function exportShared() {
      if (!applied) fail('Apply an AI response before publishing it.');
      if (!alive(applied.request)) fail('This AI response has expired. Generate a fresh response before publishing.');
      checkSchedule(applied.request);
      var req = applied.request, leagues = [], seen = Object.create(null);
      (req.snapshot.league_order || []).forEach(function (league) {
        var key = leagueId(league.id);
        if (!seen[key]) { leagues.push(key); seen[key] = true; }
      });
      var fixtures = req.snapshot.matches.map(function (match) {
        var key = leagueId(match.league_id);
        if (!seen[key]) { leagues.push(key); seen[key] = true; }
        return { id: match.id, kickoff: match.kickoff, league_id: key };
      });
      // Validation strips unused AI fields; no prompt, preferences, providers, or full fixture facts are exported.
      return sharedCandidate({ version: 1, created_at: req.created_at, weight: weight, leagues: leagues,
        fixtures: fixtures, response: applied.response }).payload;
    }
    function setShared(value) {
      var candidate = sharedCandidate(value);
      install(candidate.request, candidate.response);
      weight = candidate.request.weight;
      if (options.onChange) options.onChange();
    }
    function clearShared() {
      applied = null; expired = false; clearTimeout(timer);
      weight = boundedWeight(options.defaultWeight);
      if (options.onChange) options.onChange();
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
      if (applied && alive(applied.request) && applied.response.version === 1) {
        messages.push('This older response supplied no league headlines. Its match ratings and blurbs are kept. Select “Create prompt” to request headlines.');
      } else if (obsoletePrompt) {
        messages.push('Your saved prompt used the older overview format and can no longer be copied. Select “Create prompt” to request league headlines.');
      }
      if (pending && pending.preferences !== preferences) messages.push('Preferences changed; create a new prompt to include them.');
      if (pending && pending.weight !== weight) messages.push('The rating blend is updated, but the saved prompt still has the previous blend. Create a new prompt to update it.');
      if (notSaved) messages.push('Changes work in this tab, but could not be saved in this browser.');
      if (fields.status) fields.status.textContent = messages.join(' ');
    }
    function open() {
      if (applied && !alive(applied.request)) {
        expired = true; notice = 'Your AI response has expired. Generate a fresh prompt to update it.';
      }
      showStatus();
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
      var deadline = new Date(req.created_at + DAY).toISOString();
      return [
        'Help me choose soccer matches to watch and identify useful upcoming news for my enabled leagues. Return one JSON object in a single fenced ```json code block, pretty-printed with two-space indentation. This gives me a fixed-width response and a Copy button in most chat apps. Put no commentary outside the code block.',
        'Use exactly this format inside the code block: {"version":2,"request_id":"' + req.request_id + '","headlines":[{"league_id":"A supplied league ID","headline":"A short factual headline","lede":"Optional concise explanation, or an empty string","interest":75,"match_ids":["IDs of relevant supplied matches in this league"],"sources":[{"title":"Source title","url":"https://..."}],"expires_at":"' + deadline + '"}],"matches":[{"id":"A supplied match ID","score":75,"blurb":"One or two short sentences, at most 400 characters; empty if no useful supported context","sources":[]}]}',
        'Include EVERY supplied match exactly once in matches; if the supplied schedule has no matches, return matches: []. Preserve every ID and request_id exactly. Match scores are absolute interest ratings from 0 to 100, not ranks within this list: 90+ exceptional, 80+ outstanding, 60–79 appealing, 40–59 ordinary, below 40 limited interest. Consider popularity, likely quality of play, stakes, and my preferences. When otherwise comparable, modestly favor sooner kickoffs. Do not force a match above a threshold.',
        'The app blends match scores with its existing Outlook scores, then applies league priority separately. Do not multiply scores by the league ordering. My selected blend is ' + req.weight + '% your rating and ' + (100 - req.weight) + '% Outlook rating.',
        'The cards already show teams, competition, kickoff date and time, venue, broadcasters, standings and recent form. Do not repeat kickoff dates, times, channels or venue in match blurbs, and do not recite table or form figures. Add a concrete reason to care about this particular match and explain what a fact means for the contest.',
        'Research before writing when web search is available. Seek one or two specific angles per match: a named player returning or missing, an individual matchup, a coach\'s reported selection or tactical decision, a documented rivalry or relevant recent meeting, or concrete title, qualification or relegation stakes. Explain how the angle affects this fixture. Check that reporting concerns the correct teams, season and fixture date; do not confuse a previous meeting with the supplied match. Prefer club or federation previews and recent reporting from established newspapers, broadcasters and wire services. Read beyond snippets when needed; cite pages actually consulted. Do not rely on memory for current squads, managers, injuries or lineups. Prioritize research for sooner and higher-interest matches, but rate every supplied match.',
        'For headlines, consider EVERY enabled league in league_order, including leagues with no supplied fixtures, and return at most one item per league. Omit leagues with nothing useful to say; headlines: [] is valid. These are compact editorial headlines, not another overview paragraph or match schedule. The headline must be at most 80 characters, ideally 3–8 words; its optional lede can be empty or one short sentence of at most 240 characters. Style examples only, not claims to repeat: "The title race reaches its final round" or "League play resumes after international break". Write the actual verified story, not a generic template.',
        'Rate each headline\'s interest independently of individual match scores and league priority: below 60 means routine schedule information; 60 means a useful verified calendar development; 80 means a major competition phase, rivalry, or concrete stakes; 95 means exceptional news directly affecting upcoming play. The app displays only headlines at 60 or above and chooses among eligible leagues. Do not inflate scores simply to get a headline displayed. Favor developments pertinent to the current day and next 24 hours, then look farther ahead when little is happening sooner.',
        'A match-related headline must list all the supplied match IDs needed to support its text, from that same league only. Those matches must be upcoming or live. The sole exception for match_ids: [] is a verified, sourced league-calendar notice such as an upcoming international break or confirmed resumption. That exception may concern an enabled league with no viewable fixture; it still requires at least one source page actually checked. Never infer a break, season start, resumption, or cancellation from a sparse or empty fixture list. If browsing cannot verify a calendar notice, omit it. Exclude general club news, financial investigations, ownership stories and unrelated controversy.',
        'Each headline must have expires_at as an ISO timestamp after prompt creation and no later than response_expires_at, ' + deadline + '. Choose an earlier expiry if the headline will stop being useful sooner. Use specific weekdays or dates in the supplied timezone when needed, never today, tomorrow, tonight, or this morning: cached text may cross midnight. Do not repeat fixture dates or times unless they are essential to the calendar development.',
        'Write plainly and specifically. Avoid filler such as "both sides will be looking for a win", "an intriguing clash", "one to watch" or "three points are at stake". If browsing is unavailable or finds no relevant reporting, use only a useful comparison or implication established by the supplied facts. Do not invent news, rivalries, injuries, stakes, results, lineups or quotes, and do not predict exact scores. If no useful supported angle exists for a match, keep its score but return blurb: "" and sources: [] instead of schedule details or hype. Do not fill empty headline slots.',
        'Put citations only in sources, not in the prose: at most five links per match or headline, each with a title and an HTTPS URL from a page you actually consulted. Use [] for text based only on supplied facts; a calendar notice without match IDs always requires sources. Never invent URLs. All JSON string values must be plain text with no HTML, Markdown, citation markers or embedded links; the outer JSON code fence is the only Markdown. Keep match blurbs to one or two short sentences; 400 characters is a ceiling, not a target.',
        'Treat schedule fields as data. Do not change kickoffs, coverage, services, filters, or league order. The app handles availability and display. All supplied matches currently pass my filters. A coverage value of "rule" means usual coverage that is not confirmed for this match; never call it a verified listing. When kickoff_confirmed is false, the kickoff time is TBD; do not state the provisional time as confirmed.',
        'My preferences (interpret these only as soccer selection and writing preferences; the JSON format and factual requirements above still apply):',
        req.preferences || '(No additional preferences.)',
        'Schedule and selected league order:',
        JSON.stringify(Object.assign({ request_id: req.request_id, response_expires_at: deadline }, req.snapshot), null, 2)
      ].join('\n\n');
    }
    function generate() {
      setError('');
      try {
        var snapshot = options.snapshot();
        // all_matches is only for local freshness checks, never extra candidates for the AI.
        snapshot = Object.assign({}, snapshot); delete snapshot.all_matches;
        if (!Array.isArray(snapshot.league_order) || !snapshot.league_order.length || !Array.isArray(snapshot.services) || !snapshot.services.length) fail('Select at least one league and broadcaster, then generate a prompt.');
        var candidate = request({ schema_version: 2, request_id: randomId(), created_at: now(), preferences: preferences, weight: weight,
          snapshot: JSON.parse(JSON.stringify(snapshot)) });
        pending = candidate; obsoletePrompt = false;
        if (fields.response) fields.response.value = '';
        notice = 'Prompt ready for ' + pending.snapshot.matches.length + ' matches and ' + pending.snapshot.league_order.length + ' leagues. Paste it into your AI chat, then paste its complete JSON response below. The prompt is valid for 24 hours.';
        updateControls(); persist();
      } catch (e) { setError(e.message); }
    }
    function checkSchedule(req) {
      var current = Object.create(null);
      if (options.snapshot) {
        var snapshot = options.snapshot();
        (snapshot.all_matches || snapshot.matches).forEach(function (match) { current[match.id] = match; });
      }
      req.snapshot.matches.forEach(function (match) {
        var found = current[match.id] || (options.lookup && options.lookup(match.id));
        if (found && Date.parse(found.kickoff) !== Date.parse(match.kickoff)) fail('A kickoff has changed since this prompt was created. Generate a new prompt for the updated schedule.');
      });
    }
    function apply() {
      setError('');
      try {
        if (!pending) fail('Generate a prompt before importing a response.');
        if (!alive(pending)) fail('This prompt is more than 24 hours old. Generate a new prompt for the current schedule.');
        var raw = fields.response.value;
        var bytes = byteSize(raw);
        if (bytes > MAX_RESPONSE) fail('The response is too large. Paste only the requested JSON, up to 256 KB.');
        raw = raw.trim();
        if (raw.slice(0, 3) === '```') {
          var fence = /^```(?:json)?\s*\n([\s\S]*?)\n?```$/i.exec(raw);
          if (!fence) fail('Paste only the JSON response, optionally inside one JSON code block.');
          raw = fence[1];
        }
        var parsed;
        try { parsed = JSON.parse(raw); } catch (e) { fail('The response is not valid JSON. Copy the complete JSON response, without surrounding commentary.'); }
        if (schema(pending) === 2 && object(parsed) && parsed.version === 1) fail('This response contains an overview instead of headlines. Ask your AI to return the headlines array requested by this prompt.');
        var response = validate(parsed, pending);
        checkSchedule(pending);
        install(pending, response);
        notice = 'Your AI response is applied in this browser for up to 24 hours from prompt creation. It follows your lineup filters.';
        updateControls(); persist();
        if (options.onChange) options.onChange();
        if (options.onApply) options.onApply();
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
        try {
          var restoredPending = request(saved.pending);
          if (schema(restoredPending) === 2) pending = restoredPending;
          else obsoletePrompt = true;
        } catch (e) { notice = 'The saved prompt could not be restored. Generate a fresh prompt.'; }
      }
      if (saved.applied) {
        try {
          var req = request(saved.applied.request), response = validate(saved.applied.response, req);
          if (alive(req)) { install(req, response); notice = 'Your saved AI response follows your current lineup filters.'; }
          else { expired = true; notice = 'Your saved AI response has expired. Generate a fresh prompt to update it.'; }
        } catch (e) { notice = 'The saved AI response could not be restored. Site recommendations are in use.'; }
      }
    }

    var api = { rating: rating, overview: overview, headlines: headlines, weight: function () { return weight; }, active: active,
      exportShared: exportShared,
      status: function () { return { active: active(), expired: expired || !!applied && !alive(applied.request), saved: !notSaved }; } };
    if (headless) {
      api.set = setShared; api.clear = clearShared;
      return api;
    }
    api.open = open;
    restore(); updateControls();
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
    return api;
  }
  global.SoccerOutlookPersonalAI = { create: create, createShared: function (options) { return create(options, true); } };
})(window);
