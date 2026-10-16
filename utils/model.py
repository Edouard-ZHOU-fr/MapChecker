"""Logistic regression 模型 (train_model.py 生成的 model.yaml)：给告警打分，p 为告警是真问题的概率"""

import math

import yaml

from .features import FEATURES


class Model:
    def __init__(self, path):
        self.path = path
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        self.trained = data.get("trained", "")
        self.params = {c: data[c] for c in FEATURES if c in data}
        for category, m in self.params.items():
            if list(m["features"]) != list(FEATURES[category]):
                raise ValueError(f"{path}: features of '{category}' differ from utils/features.py, "
                                 f"run train_model.py again")

    def score(self, category, features):
        """
        p = sigmoid(intercept + Σ coef * (x - mean) / scale)。返回 None (不打分) 的情况:
          - 该检查项没有模型
          - 某个特征在训练数据中始终不变 (min == max)，而这条告警的值不同: 模型没有见过这种告警，打分只是外推
        """
        m = self.params.get(category)
        if m is None:
            return None
        if any(lo == hi and features[f] != lo for f, lo, hi in zip(m["features"], m.get("min", ()), m.get("max", ()))):
            return None
        z = m["intercept"] + sum(c * (features[f] - mu) / s
                                 for f, c, mu, s in zip(m["features"], m["coef"], m["mean"], m["scale"]))
        return 0.5 * (1.0 + math.tanh(0.5 * z))  # 即 1 / (1 + e^-z)，z 很大时也不会溢出
