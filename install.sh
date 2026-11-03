#!/usr/bin/env bash
# Install the system packages and Python libraries needed by MapChecker (Ubuntu 22.04 + ROS 2 Humble)
#
# Usage:
#   ./install.sh
#   PYTHON=/path/to/python3 ./install.sh   # use another Python
#
#   apt: python3-pip, python3-tk (heatmap pop-up window), git (train_model.py reads the history of map repositories),
#        skipped if already installed
#   pip: numpy, PyYAML, matplotlib, scipy, scikit-learn and their dependencies all go into MapChecker/libs/,
#        without affecting the system and user directories;
#        check_lanelet_map.py / train_model.py put libs/ at the front of sys.path at startup.
#        Every run reinstalls and replaces libs/ as a whole (the existing libs/ is kept if the installation fails).
#        libs/ contains compiled libraries and can only be run with the Python version used for installation.
#        numpy is limited to < 2: the Python packages of ROS 2 Humble are compiled against numpy 1.x
#   ROS / Autoware are not installed: ~/ros/ad_ros2_ws needed by autoware_checking is present on every PC by default,
#        just source it before running (see README.md)

set -euo pipefail

PYTHON=${PYTHON:-python3}
LIBS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/libs"
APT_PACKAGES=(python3-pip python3-tk git)
PIP_PACKAGES=("numpy>=1.21,<2" "PyYAML>=5.4" "matplotlib>=3.5" "scipy>=1.8" "scikit-learn>=1.0")

if ! "$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
    echo "Python 3.10+ required, found: $("$PYTHON" --version 2>&1 || echo "no $PYTHON")" >&2
    exit 1
fi

missing=()
for pkg in "${APT_PACKAGES[@]}"; do
    if ! dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q "install ok installed"; then
        missing+=("$pkg")
    fi
done
if ((${#missing[@]})); then
    echo "Installing system packages: ${missing[*]}"
    sudo apt-get update
    sudo apt-get install -y "${missing[@]}"
else
    echo "System packages already installed: ${APT_PACKAGES[*]}"
fi

# install into a temporary directory first, replace libs/ only after success
tmp_dir=$(mktemp -d "$LIBS_DIR.XXXXXX")
trap 'rm -rf "$tmp_dir"' EXIT
echo "Installing Python packages into $LIBS_DIR: ${PIP_PACKAGES[*]}"
"$PYTHON" -m pip install --target "$tmp_dir" --no-warn-script-location --no-warn-conflicts "${PIP_PACKAGES[@]}"
rm -rf "$LIBS_DIR"
mv "$tmp_dir" "$LIBS_DIR"

echo
echo "Installed:"
PYTHONPATH="$LIBS_DIR" "$PYTHON" - <<'PY'
import importlib
import tkinter

for name, module in (("numpy", "numpy"), ("PyYAML", "yaml"), ("matplotlib", "matplotlib"), ("scipy", "scipy"),
                     ("scikit-learn", "sklearn")):
    print(f"  {name:13s} {importlib.import_module(module).__version__}")
print(f"  {'tkinter':13s} Tk {tkinter.TkVersion}")
PY

echo
echo "Done. For autoware_checking, source ROS first:"
echo "  source /opt/ros/humble/setup.bash"
echo "  source ~/ros/ad_ros2_ws/install/setup.bash"
