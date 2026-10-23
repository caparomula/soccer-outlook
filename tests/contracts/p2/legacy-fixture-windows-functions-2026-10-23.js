  var WINDOW_DAYS = 3;               // the page shows today and the three days after it (story.py's WINDOW_DAYS)
  var HISTORY_DAYS = 3;              // backward browsing stops after the three previous sports days
  var FOCUS_MS = 24 * 60 * 60000;
  var LIVE_MS = 125 * 60000;
  var DAYS = ['today', 'tomorrow', 'day2', 'day3'];
  var PAST = ['yesterday', 'dayminus2', 'dayminus3'];
  function inFocus(r, now) {
    return r._state !== 'post' && r._k < now + FOCUS_MS &&
      (r._k >= now || (r._k >= now - LIVE_MS && (r._state === 'in' || r._tv)) ||
       (r._state === 'in' && Date.now() - (r._seen || 0) < 10 * 60000));
  }
  function bucketOf(r, now) {
    var idx = dayIndex(r._k, now);
    if (idx > WINDOW_DAYS || idx < -HISTORY_DAYS) return null;
    // A live match can cross midnight or the 4 am sports-day boundary.
    if (inFocus(r, now) && r._tv && r._k <= now) return 'live';
    if (idx < 0) return PAST[-idx - 1];
    if (idx === 0) {
      if (r._state === 'post') return 'earlier';
      if (r._state === 'in' && (now < r._k + LIVE_MS + 30 * 60000 || Date.now() - (r._seen || 0) < 10 * 60000)) return 'live';
      if (r._tv && now >= r._k && now < r._k + LIVE_MS) return 'live';
      if (r._tv && now >= r._k + LIVE_MS) return 'earlier';
    }
    return DAYS[idx];
  }
