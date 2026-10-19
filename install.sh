#!/usr/bin/env bash
# 安装 MapChecker 需要的系统包和 Python 库 (Ubuntu 22.04 + ROS 2 Humble)
#
# 用法:
#   ./install.sh
#   PYTHON=/path/to/python3 ./install.sh   # 使用另一个 Python
#
#   apt: python3-pip、python3-tk (热力图弹出窗口)、git (train_model.py 读取地图仓库的历史)，已安装的跳过
#   pip: numpy、PyYAML、matplotlib、scipy、scikit-learn 及其依赖全部装到 MapChecker/libs/，不影响系统和用户目录；
#        check_lanelet_map.py / train_model.py 启动时把 libs/ 加到 sys.path 最前面。
#        每次运行都重新安装并整体替换 libs/ (安装失败时保留原来的 libs/)。
#        libs/ 中有编译好的库，只能用安装时的 Python 版本运行。numpy 限制为 < 2: ROS 2 Humble 的 Python 包是用 numpy 1.x 编译的
#   不安装 ROS / Autoware: autoware_checking 需要的 ~/ros/ad_ros2_ws 默认每台 PC 上都有，运行前 source 即可 (见 README.md)

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

# 先装到临时目录，成功后再替换 libs/
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
