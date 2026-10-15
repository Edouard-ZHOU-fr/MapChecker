#!/usr/bin/env python3
"""
训练告警打分模型 (logistic regression)，生成 config/model.yaml

读取 build_dataset.py 生成的 data/*.csv 中有 label 的告警，每个检查项 (turn_direction / right_of_way) 训练一个模型:
  p = sigmoid(intercept + Σ coef * (x - mean) / scale)，p 为告警是真问题 (之后会被修复) 的概率

只有一种 label 的仓库 (例如全部告警都被修复了) 不参与训练: 它们无法说明同一张地图里哪些告警会被修复，
只会让模型学到 "哪张地图" 的差别。

utils/features.py 中 NEGATIVE_FEATURES 列出的特征 (领域知识: 值越大越可能是误报) 的系数限制为 <= 0:
方向由领域知识决定，大小由数据决定。sklearn 的 LogisticRegression 不支持限制系数，
这里用 scipy 的 L-BFGS-B 求解同样的目标函数 (L2 正则，class_weight=balanced)，只是加了系数的上下界。

评估用 leave-one-map-out 交叉验证: 每次留出一个仓库，用其他仓库训练，再给留出仓库的告警打分，
相当于检验模型在一张新地图上的效果。同时列出只用单个特征排序的 AUC，看模型是否比简单规则更有用。
AUC: 随机取一条真问题和一条误报，真问题得分更高的概率；0.5 等于乱猜，1 为完美排序。

只有训练需要 scikit-learn，check_lanelet_map.py 打分只用 config/model.yaml 中的数字。

用法:
  python3 train_model.py                        # data/ -> config/model.yaml
  python3 train_model.py --data other_dir -o other_model.yaml --C 0.1
"""

import argparse
import csv
import os
import sys
import time

import numpy as np
import yaml
from scipy.optimize import minimize
from scipy.special import expit
from sklearn.base import BaseEstimator, ClassifierMixin
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from utils.features import FEATURES, NEGATIVE_FEATURES

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MIN_PER_CLASS = 10  # 每个 label 至少需要的样本数


def load_samples(path, features):
    with open(path, newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r["label"] in ("0", "1")]
    x = np.array([[float(r[name]) for name in features] for r in rows]).reshape(-1, len(features))
    y = np.array([int(r["label"]) for r in rows])
    groups = np.array([r["repo"] for r in rows])
    return x, y, groups


class SignedLogisticRegression(BaseEstimator, ClassifierMixin):
    """
    与 LogisticRegression(C=C, class_weight="balanced") 的目标函数相同:
      min 0.5 * |w|² + C * Σ 样本权重 * log loss
    upper: 每个系数的上界 (None 表示不限制，0 表示系数 <= 0)
    class_weight="balanced": 两类样本数不同时按比例加权，避免模型偏向多的一类
    """

    def __init__(self, C=1.0, upper=None):
        self.C = C
        self.upper = upper

    def fit(self, x, y):
        sw = (len(y) / (2.0 * np.bincount(y, minlength=2)))[y]

        def objective(theta):
            w, b = theta[:-1], theta[-1]
            z = x @ w + b
            g = self.C * sw * (expit(z) - y)
            loss = 0.5 * w @ w + self.C * np.sum(sw * (np.logaddexp(0.0, z) - y * z))
            return loss, np.r_[w + x.T @ g, g.sum()]

        upper = self.upper or [None] * x.shape[1]
        bounds = [(None, u) for u in upper] + [(None, None)]  # 截距不限制
        theta = minimize(objective, np.zeros(x.shape[1] + 1), jac=True, method="L-BFGS-B", bounds=bounds).x
        self.coef_, self.intercept_, self.classes_ = theta[None, :-1], theta[-1:], np.array([0, 1])
        return self

    def predict_proba(self, x):
        p = expit(x @ self.coef_[0] + self.intercept_[0])
        return np.c_[1.0 - p, p]


def make_model(category, c):
    negative = NEGATIVE_FEATURES.get(category, ())
    upper = [0.0 if name in negative else None for name in FEATURES[category]]
    return make_pipeline(StandardScaler(), SignedLogisticRegression(C=c, upper=upper))


def auc(y, score):
    return roc_auc_score(y, score)


def best_single_feature(x, y):
    """AUC 最高的单个特征: (AUC, 列号, 方向)，方向 -1 表示值越小越可能是真问题"""
    return max((max(a, 1 - a), j, 1 if a >= 0.5 else -1) for j in range(x.shape[1]) for a in [auc(y, x[:, j])])


def leave_one_map_out(category, x, y, groups, c):
    """
    每次留出一个仓库，用其他仓库训练后给它打分。
    返回 ({仓库: (模型 AUC, 单特征基线 AUC, 基线特征列号)}, 每个样本的 out-of-fold 概率)；
    单特征基线同样只用其他仓库选择特征和方向
    """
    result, oof = {}, np.empty(len(y))
    for repo in np.unique(groups):
        test, train = groups == repo, groups != repo
        oof[test] = make_model(category, c).fit(x[train], y[train]).predict_proba(x[test])[:, 1]
        _, j, sign = best_single_feature(x[train], y[train])
        result[repo] = (auc(y[test], oof[test]), auc(y[test], sign * x[test, j]), j)
    return result, oof


def train(category, x, y, groups, c):
    features = FEATURES[category]
    print(f"\n=== {category} ===")
    one_label = [r for r in np.unique(groups) if len(np.unique(y[groups == r])) < 2]
    if one_label:
        print(f"Excluded (only one label): {', '.join(f'{r} ({(groups == r).sum()})' for r in one_label)}")
        keep = ~np.isin(groups, one_label)
        x, y, groups = x[keep], y[keep], groups[keep]
    print(f"{len(y)} samples, {y.sum()} label 1 (fixed), {len(y) - y.sum()} label 0 (false positive)")
    if min(y.sum(), len(y) - y.sum()) < MIN_PER_CLASS or len(np.unique(groups)) < 2:
        print(f"Skipped: need at least {MIN_PER_CLASS} samples of each label in at least 2 repositories")
        return None

    per_repo, oof = leave_one_map_out(category, x, y, groups, c)
    print("Leave-one-map-out (each repository scored by a model trained on the others):")
    for repo, (a, b, j) in per_repo.items():
        m = groups == repo
        print(f"  {repo:36s} {m.sum():4d} samples, {y[m].sum():4d} label 1   "
              f"model AUC {a:.2f}   best single feature AUC {b:.2f} ({features[j]})")
    cv_auc, base_auc = np.mean([v[0] for v in per_repo.values()]), np.mean([v[1] for v in per_repo.values()])
    print(f"  {'mean of repositories':36s} {'':33s}model AUC {cv_auc:.2f}   best single feature AUC {base_auc:.2f}")
    low = oof < 0.5
    print(f"  with p < 0.5 as false positive: {(low & (y == 0)).sum()}/{(y == 0).sum()} false positives caught, "
          f"{(low & (y == 1)).sum()}/{(y == 1).sum()} real issues wrongly scored low")

    model = make_model(category, c).fit(x, y)
    scaler, lr = model[0], model[-1]
    negative = NEGATIVE_FEATURES.get(category, ())
    print("Coefficients (standardized, + means more likely real issue):")
    for w, name in sorted(zip(lr.coef_[0], features), key=lambda t: -abs(t[0])):
        note = ""
        if name in negative:
            note = "  (constrained <= 0" + (", at the bound: the data does not support lowering p)" if w > -1e-4 else ")")
        print(f"  {name:20s} {w:+.2f}{note}")
    return {
        "features": list(features),
        "mean": [round(float(v), 6) for v in scaler.mean_],
        "scale": [round(float(v), 6) for v in scaler.scale_],
        "coef": [round(float(v), 6) for v in lr.coef_[0]],
        "intercept": round(float(lr.intercept_[0]), 6),
        "min": [round(float(v), 6) for v in x.min(axis=0)],  # 训练数据的取值范围，见 utils/model.py
        "max": [round(float(v), 6) for v in x.max(axis=0)],
        "samples": int(len(y)),
        "positives": int(y.sum()),
        "cv_auc": round(float(cv_auc), 3),
    }


def main():
    parser = argparse.ArgumentParser(description="Train the logistic regression that scores warnings")
    parser.add_argument("--data", default=os.path.join(BASE_DIR, "data"), help="output_dir of build_dataset.py")
    parser.add_argument("-o", "--output", default=os.path.join(BASE_DIR, "config", "model.yaml"))
    parser.add_argument("--C", type=float, default=1.0, help="inverse of regularization strength (default 1.0)")
    args = parser.parse_args()

    result = {"trained": time.strftime("%Y-%m-%d %H:%M:%S")}
    for category, features in FEATURES.items():
        path = os.path.join(args.data, f"{category}.csv")
        if not os.path.isfile(path):
            print(f"{path} not found, run build_dataset.py first")
            continue
        m = train(category, *load_samples(path, features), args.C)
        if m:
            result[category] = m

    if len(result) == 1:
        print("\nNo model trained")
        return 1
    with open(args.output, "w", encoding="utf-8") as f:
        f.write("# 由 train_model.py 生成，不要手动修改\n"
                "# p = 1 / (1 + exp(-(intercept + Σ coef * (x - mean) / scale)))，p 为告警是真问题 (之后会被修复) 的概率\n"
                "# 特征的含义见 utils/features.py；cv_auc 为 leave-one-map-out 交叉验证的 AUC\n")
        yaml.safe_dump(result, f, sort_keys=False, default_flow_style=None, allow_unicode=True)
    print(f"\nModel saved to: {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
