"""Validated application training parameters; no implicit batch-size changes."""
import hashlib
import json
import math


def config_fingerprint(config):
    return hashlib.sha256(json.dumps(config, sort_keys=True, allow_nan=False).encode()).hexdigest()


def normalize_config(payload):
    allowed = {"version_id", "initial_model", "stages", "seed", "s1", "s2"}
    if set(payload) - allowed:
        raise ValueError("未知训练配置字段：" + ",".join(sorted(set(payload)-allowed)))
    version = payload.get("version_id")
    if not isinstance(version, str) or not version:
        raise ValueError("必须选择数据版本")
    stages = payload.get("stages", ["s2", "s1"])
    if not isinstance(stages, list) or not stages or set(stages)-{"s1", "s2"} or len(set(stages)) != len(stages):
        raise ValueError("训练阶段必须是 s2、s1 的非空无重复列表")
    seed = payload.get("seed", 42)
    if type(seed) is not int or not 0 <= seed <= 2**32-1:
        raise ValueError("seed 必须是 0..4294967295 的整数")
    initial = payload.get("initial_model", "official")
    if not isinstance(initial, str) or not initial:
        raise ValueError("初始化模型无效")
    result = dict(version_id=version, initial_model=initial, stages=stages, seed=seed)
    for stage in ("s1", "s2"):
        defaults = dict(batch_size=1, precision="fp16", stop_mode="epochs", target=1,
                        learning_rate=.01 if stage == "s1" else .0001)
        defaults.update({"accumulate": 4} if stage == "s1" else {"rank": 32})
        supplied = payload.get(stage, {})
        if not isinstance(supplied, dict) or set(supplied)-set(defaults):
            raise ValueError(f"{stage} 包含未接通参数")
        defaults.update(supplied)
        for key in ("batch_size", "target", "accumulate" if stage == "s1" else "rank"):
            value = defaults[key]
            if type(value) is not int or value < 1:
                raise ValueError(f"{stage}.{key} 必须为正整数")
        if defaults["batch_size"] > 64 or defaults.get("accumulate", 1) > 64:
            raise ValueError(f"{stage} batch/accumulate 最大 64")
        if stage == "s2" and defaults["rank"] not in (8, 16, 32, 64, 128):
            raise ValueError("s2.rank 支持 8/16/32/64/128")
        if defaults["precision"] not in ("fp16", "fp32"):
            raise ValueError(f"{stage}.precision 支持 fp16/fp32")
        if defaults["stop_mode"] not in ("steps", "epochs"):
            raise ValueError(f"{stage}.stop_mode 支持 steps/epochs")
        lr = defaults["learning_rate"]
        if type(lr) not in (int, float) or not math.isfinite(lr) or not 0 < lr <= .1:
            raise ValueError(f"{stage}.learning_rate 必须在 (0, 0.1]")
        result[stage] = defaults
    return result
