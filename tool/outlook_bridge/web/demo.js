const request = {
  version: 1,
  operation: "sports_day",
  cases: [{
    id: "spring-boundary",
    instant: "2026-03-08T08:00:00Z",
    reference: "2026-03-07T17:00:00Z",
    time_zone: "America/New_York"
  }]
};
document.querySelector("#result").textContent =
  JSON.stringify(JSON.parse(window.outlookSportsDay(JSON.stringify(request))), null, 2);
