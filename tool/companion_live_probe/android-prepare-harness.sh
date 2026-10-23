#!/bin/sh
# Generate a disposable Android shell. Does not boot/install/run a device.
set -eu
if [ "$#" -ne 1 ]; then
  echo "Usage: FLUTTER_BIN=/path/to/flutter $0 NEW_PROJECT_DIRECTORY" >&2
  exit 64
fi
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
probe_project=$1
probe_flutter=${FLUTTER_BIN:-flutter}
if [ -e "$probe_project" ]; then
  echo "Refusing to overwrite an existing directory: $probe_project" >&2
  exit 64
fi
"$probe_flutter" --version --machine | python3 -c '
import json, sys
v = json.load(sys.stdin)
expected = {
    "frameworkVersion": "3.47.4",
    "frameworkRevision": "9584c6713b324636289d067944a46fd6b49df14b",
    "dartSdkVersion": "3.13.3",
}
for key, value in expected.items():
    if v.get(key) != value:
        raise SystemExit(f"Toolchain mismatch: {key}={v.get(key)!r}; expected {value}")
'
"$probe_flutter" create --empty --no-pub --platforms=android \
  --project-name companion_live_probe --org dev.socceroutlook.spike "$probe_project"
cat > "$probe_project/pubspec.yaml" <<'EOF'
name: companion_live_probe
description: Disposable P0 native transport and plugin evidence.
publish_to: none
version: 0.1.0+1
environment:
  sdk: ^3.13.3
dependencies:
  flutter:
    sdk: flutter
  flutter_timezone: 5.1.1
  path_provider: 2.1.6
  sqflite: 2.4.4+2
dev_dependencies:
  flutter_test:
    sdk: flutter
  flutter_lints: 6.0.0
flutter:
  uses-material-design: true
EOF
cp "$script_dir/android-pubspec.lock" "$probe_project/pubspec.lock"
sh "$script_dir/ios-prepare-harness.sh" "$probe_project"
# Never auto-install or replace shared SDK packages while running this spike.
cat >> "$probe_project/android/gradle.properties" <<'EOF'

android.builder.sdkDownload=false
EOF
cd "$probe_project"
"$probe_flutter" pub get --enforce-lockfile
