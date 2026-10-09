# ESPN response samples

These files retain a small set of actual ESPN response fields, captured on 9 October 2026. Each JSON file records its source URL. Tests read them offline; no API keys or live requests are needed.

- `espn-scoreboards.json` contains Arsenal–Leeds and América–Monterrey from the 10 October scoreboards. It preserves IDs, display names, kickoff/status objects, broadcast aliases, records, and numeric odds. Other events, editorial content, betting links, and tracking metadata were removed. The remaining values and shapes are unchanged.
- `espn-mex-teams.json` preserves the 18 Liga MX team IDs, display names and abbreviations used to check club-specific broadcast mappings.

These complement the synthetic fixtures rather than serving as a complete ESPN schema. When an upstream shape or identity changes, add a small representative sample and its capture date. Never copy credentials or unrelated response content into a fixture.
