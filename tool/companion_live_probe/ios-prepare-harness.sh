#!/bin/sh
# Prepare an already-created disposable Flutter project. No device operations.
set -eu
if [ "$#" -ne 1 ]; then
  echo "Usage: $0 DISPOSABLE_FLUTTER_PROJECT" >&2
  exit 64
fi
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
probe_project=$1
test -f "$probe_project/pubspec.yaml"
mkdir -p "$probe_project/assets"
cp "$script_dir/ios-native-main.dart" "$probe_project/lib/main.dart"
cp "$script_dir/lib/probe.dart" "$probe_project/lib/probe.dart"
cat > "$probe_project/assets/localhost.cnf" <<'EOF'
[req]
distinguished_name=dn
x509_extensions=extensions
prompt=no
[dn]
CN=site.api.espn.com
[extensions]
subjectAltName=DNS:site.api.espn.com,IP:127.0.0.1
basicConstraints=critical,CA:TRUE
keyUsage=critical,digitalSignature,keyEncipherment,keyCertSign
EOF
openssl req -x509 -newkey rsa:2048 -nodes -days 2 \
  -config "$probe_project/assets/localhost.cnf" \
  -keyout "$probe_project/assets/localhost-key.pem" \
  -out "$probe_project/assets/localhost-cert.pem" 2>/dev/null
python3 - "$probe_project/pubspec.yaml" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
text = path.read_text()
if "  assets:\n" in text:
    raise SystemExit("Use a new disposable shell without an assets section")
text = text.replace(
    "  uses-material-design: true\n",
    "  uses-material-design: true\n"
    "  assets:\n"
    "    - assets/localhost-cert.pem\n"
    "    - assets/localhost-key.pem\n",
)
path.write_text(text)
PY
