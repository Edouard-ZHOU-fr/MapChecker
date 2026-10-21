#!/usr/bin/env python3
"""
生成告警打分模型 (logistic regression): 先生成训练数据，再训练，结果保存到 config/model.yaml

  1. utils/build_dataset.py: 在 config/dataset.yaml 列出的地图仓库的每个历史版本上运行检查 1 和检查 2，
     跟踪每条告警之后是否被修复并标注 label，输出 data/turn_direction.csv 和 data/right_of_way.csv
  2. utils/tranning.py:      用这些数据训练模型，输出 leave-one-map-out 交叉验证的结果和模型系数

需要 scikit-learn、scipy 和 git；dataset.yaml 中 autoware_checking 为 true 时还需要先 source ROS
(见 utils/validate_autoware_map.py)。检查阈值使用 config/config.yaml 中的设置。详见 README.md。

用法:
  python3 train_model.py                    # config/dataset.yaml -> data/ -> config/model.yaml
  python3 train_model.py --C 0.1            # 加强正则化
  python3 train_model.py --skip-build       # 不重新生成训练数据，直接用已有的 data/ 训练 (例如只调整 --C)
  python3 train_model.py -c other.yaml -o other_model.yaml
"""

import argparse
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# install.sh 把第三方库装在 libs/ 中，放在最前面，优先于系统和用户目录中的版本
sys.path.insert(0, os.path.join(BASE_DIR, "libs"))

from utils.build_dataset import DEFAULT_DATASET_CONFIG, build_dataset, load_dataset_config
from utils.tranning import train_models


def main():
    parser = argparse.ArgumentParser(description="Build the training data from map git histories, then train the "
                                                 "logistic regression that scores warnings")
    parser.add_argument("-c", "--config", default=DEFAULT_DATASET_CONFIG,
                        help="dataset config (repositories, labeling rules), default: config/dataset.yaml")
    parser.add_argument("-o", "--output", default=os.path.join(BASE_DIR, "config", "model.yaml"),
                        help="model file, default: config/model.yaml")
    parser.add_argument("--C", type=float, default=1.0, help="inverse of regularization strength (default 1.0)")
    parser.add_argument("--skip-build", action="store_true",
                        help="train on the existing data (output_dir in the dataset config) without replaying "
                             "the map histories")
    args = parser.parse_args()
    if not os.path.isfile(args.config):
        parser.error(f"dataset config not found: {args.config}")

    if args.skip_build:
        data_dir = load_dataset_config(args.config)[1]
    else:
        try:
            data_dir = build_dataset(args.config)
        except RuntimeError as e:
            parser.error(str(e))
    return 0 if train_models(data_dir, args.output, args.C) else 1


if __name__ == "__main__":
    sys.exit(main())
