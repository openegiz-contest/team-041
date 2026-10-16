#!/usr/bin/env bash
# Rebuilds every model of the 3D view: .glb files and models.json in
# mine/ui/site/models/. BLENDER names the Blender binary (4.2 LTS; default:
# `blender` on the PATH). Extra arguments go to build.py, e.g. --only cat785c.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
blender="${BLENDER:-blender}"
exec "$blender" -b --factory-startup --python-exit-code 1 -P "$here/build.py" -- "$@"
