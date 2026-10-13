// Optional maintainer publishing. The repository-scoped token is held only in this closure;
// GitHub receives validated recommendations, never the private prompt or preferences.
(function (global) {
  'use strict';
  var REPOSITORY = 'caparomula/soccer-outlook', BRANCH = 'main', PATH = 'content/shared-ai.json';
  var API = 'https://api.github.com/repos/' + REPOSITORY, LIMIT = 512 * 1024, TIMEOUT = 15000;

  function create(options) {
    var fields = {}, names = ['section', 'token', 'connect', 'disconnect', 'saved', 'status', 'error'];
    names.forEach(function (name) { fields[name] = document.getElementById('ai-publish-' + name); });
    var apply = document.getElementById('ai-apply'), token = '', busy = false, controller = null;
    var generation = 0, locked = [], exportShared = options.exportShared || function () { return options.personal.exportShared(); };
    var configured = fields.section && fields.section.getAttribute('data-repository') === REPOSITORY &&
      fields.section.getAttribute('data-branch') === BRANCH && fields.section.getAttribute('data-path') === PATH;

    function issue(message) { var error = new Error(); error.publicMessage = message; throw error; }
    function setError(message) { if (fields.error) fields.error.textContent = message; }
    function status(message) { if (fields.status) fields.status.textContent = message; }
    function ready() {
      try { return !!exportShared(); } catch (error) { return false; }
    }
    function refresh() {
      if (apply) apply.textContent = token ? 'Apply & publish' : 'Apply response';
      if (fields.connect) { fields.connect.hidden = !!token; fields.connect.disabled = busy || !configured; }
      if (fields.disconnect) { fields.disconnect.hidden = !token; fields.disconnect.disabled = busy; }
      if (fields.token) fields.token.disabled = busy || !!token || !configured;
      if (fields.saved) fields.saved.disabled = busy || !token || !ready();
      if (fields.section) fields.section.setAttribute('aria-busy', busy ? 'true' : 'false');
      if (busy) locked.forEach(function (item) { item.field.disabled = true; });
    }
    function working(value) {
      if (value && !busy) {
        locked = ['apply', 'clear', 'generate', 'weight', 'preferences', 'response'].map(function (name) {
          var field = document.getElementById('ai-' + name);
          return field ? { field: field, disabled: field.disabled } : null;
        }).filter(Boolean);
      }
      busy = value;
      if (!busy) { locked.forEach(function (item) { item.field.disabled = item.disabled; }); locked = []; }
      refresh();
    }
    function message(error) {
      return error && error.publicMessage || 'GitHub could not be reached. Your response remains saved in this browser. Check the repository before trying again; an interrupted save may already have succeeded.';
    }
    async function request(path, auth, init) {
      controller = new AbortController();
      var current = controller, timeout = global.setTimeout(function () { current.abort(); }, TIMEOUT);
      try {
        var headers = { 'Accept': 'application/vnd.github+json', 'Authorization': 'Bearer ' + auth,
          'X-GitHub-Api-Version': '2022-11-28' };
        if (init && init.body) headers['Content-Type'] = 'application/json';
        var response = await global.fetch(API + path, Object.assign({}, init || {}, {
          headers: headers, credentials: 'omit', redirect: 'error', cache: 'no-store', signal: current.signal
        }));
        var data = null;
        if (response.ok) {
          try { data = await response.json(); }
          catch (error) { issue('GitHub returned an unreadable response. No automatic retry was made; check the repository before trying again.'); }
        }
        return { status: response.status, ok: response.ok, data: data };
      } catch (error) {
        if (current.signal.aborted) issue('The GitHub request timed out or was cancelled. Your local response is kept. Check the repository before trying again.');
        throw error;
      } finally {
        global.clearTimeout(timeout);
        if (controller === current) controller = null;
      }
    }
    function checkAccess(result) {
      if (result.status === 401) issue('GitHub did not accept this token. Connect again with a current repository-scoped token.');
      if (result.status === 403) issue('GitHub denied access. Check that the token has Contents: read and write access to this repository.');
      if (!result.ok) issue('The repository could not be accessed. Check the token and repository permissions, then try again.');
      if (!result.data || !result.data.permissions || result.data.permissions.push !== true) {
        issue('This GitHub account does not have write access to ' + REPOSITORY + '. Your response remains private to this browser.');
      }
    }
    async function connect() {
      if (busy || !configured) return false;
      var candidate = fields.token ? fields.token.value.trim() : '', run = ++generation;
      if (fields.token) fields.token.value = '';
      setError('');
      if (!candidate || candidate.length > 512 || /\s/.test(candidate)) {
        setError('Paste a fine-grained GitHub token with Contents: read and write access to this repository.');
        return false;
      }
      working(true); status('Checking repository access…');
      try {
        var result = await request('', candidate);
        if (generation !== run) return false;
        checkAccess(result); token = candidate;
        status('Connected to ' + REPOSITORY + '. Apply & publish will share each accepted response as site defaults.');
        return true;
      } catch (error) {
        if (generation === run) { token = ''; status(''); setError(message(error)); }
        return false;
      } finally {
        candidate = ''; working(false);
        var dialog = document.getElementById('ai-settings');
        if (generation === run && dialog && dialog.open) {
          var focus = token ? fields.disconnect : fields.token;
          if (focus) focus.focus({ preventScroll: true });
        }
      }
    }
    function disconnect(announce) {
      generation += 1; token = '';
      if (controller) controller.abort();
      if (fields.token) fields.token.value = '';
      setError('');
      status(announce === false ? '' : 'Disconnected. Applying a response now changes only this browser.');
      refresh();
      if (announce !== false && fields.token) fields.token.focus({ preventScroll: true });
    }
    function snapshot() {
      var exported;
      try { exported = exportShared(); }
      catch (error) { issue('There is no current AI response to publish. It may have expired; generate and apply a fresh response.'); }
      if (!exported || typeof exported !== 'object' || Array.isArray(exported)) issue('Apply a valid AI response before publishing.');
      var content;
      try { content = JSON.stringify(exported, null, 2) + '\n'; }
      catch (error) { issue('The saved response could not be prepared for publishing. Apply a fresh response.'); }
      if (new TextEncoder().encode(content).length > LIMIT) issue('The shared response is too large to publish. It must be no more than 512 KB.');
      if (token && content.indexOf(token) !== -1) issue('The response contains the connected credential. Remove it from the response before publishing.');
      return content;
    }
    function base64(text) {
      var bytes = new TextEncoder().encode(text), binary = '';
      for (var start = 0; start < bytes.length; start += 8192) {
        binary += String.fromCharCode.apply(null, bytes.subarray(start, start + 8192));
      }
      return global.btoa(binary);
    }
    async function publish() {
      if (busy || !configured) return false;
      setError('');
      if (!token) { setError('Connect GitHub before publishing site defaults.'); return false; }
      var content;
      try { content = snapshot(); } catch (error) { setError(message(error)); refresh(); return false; }
      var run = ++generation;
      working(true); status('Saving recommendations to the repository…');
      try {
        var existing = await request('/contents/' + PATH + '?ref=' + BRANCH, token);
        if (generation !== run) return false;
        if (existing.status === 401) { token = ''; issue('The GitHub token is no longer accepted. Connect again before publishing.'); }
        if (existing.status !== 404 && !existing.ok) issue('The existing shared response could not be read. Nothing was overwritten; check repository access and try again.');
        var body = { message: 'Update shared AI recommendations', branch: BRANCH, content: base64(content) };
        if (existing.ok) {
          if (!existing.data || existing.data.type !== 'file' || typeof existing.data.sha !== 'string' || !existing.data.sha) {
            issue('The shared response is not a regular repository file. Nothing was overwritten.');
          }
          body.sha = existing.data.sha;
        }
        if (snapshot() !== content) issue('Your response changed while publishing. Use Publish saved response to publish the current version.');
        var result = await request('/contents/' + PATH, token, { method: 'PUT', body: JSON.stringify(body) });
        if (generation !== run) return false;
        if (result.status === 409 || result.status === 422) issue('The repository changed or rejected this update. Your local response is kept. Check the repository, then choose Publish saved response to try again.');
        if (result.status === 401) { token = ''; issue('The GitHub token is no longer accepted. Your local response is kept; connect again before publishing.'); }
        if (result.status === 403) issue('GitHub refused this update. Check Contents: read and write permission and branch rules. Your local response is kept.');
        if (!result.ok) issue('GitHub did not confirm the update. Your local response is kept. Check the repository before trying again.');
        status('Published to repository; deployment pending.');
        var commit = result.data && result.data.commit && result.data.commit.sha;
        if (typeof commit === 'string' && /^[a-f0-9]{40,64}$/.test(commit) && fields.status) {
          var link = document.createElement('a'); link.href = 'https://github.com/' + REPOSITORY + '/commit/' + commit;
          link.target = '_blank'; link.rel = 'noopener noreferrer'; link.textContent = 'View commit';
          fields.status.appendChild(document.createTextNode(' ')); fields.status.appendChild(link);
        }
        if (options.onPublished) options.onPublished({ commit: commit || null });
        return true;
      } catch (error) {
        if (generation === run) { status(''); setError(message(error)); }
        return false;
      } finally { working(false); }
    }

    if (fields.connect) fields.connect.addEventListener('click', connect);
    if (fields.disconnect) fields.disconnect.addEventListener('click', function () { disconnect(); });
    if (fields.saved) fields.saved.addEventListener('click', publish);
    if (fields.token) fields.token.addEventListener('keydown', function (event) {
      if (event.key === 'Enter') { event.preventDefault(); connect(); }
    });
    global.addEventListener('pagehide', function () { disconnect(false); });
    refresh();
    if (!configured) setError('Publishing is unavailable because the repository configuration is missing or different.');
    return { publish: publish, connected: function () { return !!token; }, disconnect: disconnect, refresh: refresh };
  }
  global.SoccerOutlookGitHubPublish = { create: create };
})(window);
