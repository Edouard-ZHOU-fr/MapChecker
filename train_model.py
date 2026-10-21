#!/usr/bin/env python3
"""
Generate the warning scoring model (logistic regression): build the training data first, then train; the result is saved
to config/model.yaml

  1. utils/build_dataset.py: runs Check 1 and Check 2 on every historical revision of the map repositories listed in
     config/dataset.yaml, tracks whether each warning is fixed later and assigns its label,
     outputs data/turn_direction.csv and data/right_of_way.csv
  2. utils/tranning.py:      trains the models on this data, outputs the leave-one-map-out cross-validation results
     and the model coefficients

Requires scikit-learn, scipy and git; when autoware_checking is true in dataset.yaml, ROS must also be sourced first
(see utils/validate_autoware_map.py). The check thresholds are taken from config/config.yaml. See README.md for details.

Usage:
  python3 train_model.py                    # config/dataset.yaml -> data/ -> config/model.yaml
  python3 train_model.py --C 0.1            # stronger regularization
  python3 train_model.py --skip-build       # train on the existing data/ without rebuilding it (e.g. only tuning --C)
  python3 train_model.py -c other.yaml -o other_model.yaml
"""

import argparse
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# install.sh installs third-party libraries in libs/; put it first, ahead of the versions in system and user directories
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
