  function liveStatus(st) {
    var t = (st && st.type) || {}, desc = (t.description || '').toLowerCase();
    if (t.state === 'in') return desc.indexOf('halftime') === 0 ? 'HT' : (st.displayClock || t.shortDetail || 'Live');
    if (t.state === 'post') return /^(full time|final|full-time)$/.test(desc) ? 'FT' : (t.shortDetail || t.description || 'FT');
    return '';
  }
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

    return {state: state, label: status, played: played, scores: vals};
  }
