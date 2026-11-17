# P06 独立恢复协议复核

日期：2026-11-17（Asia/Hong_Kong）。本复核只读训练 worker，由负责该模块的实施者修复；另添加 CPU 合同测试和前端状态说明。真实 GPU 暂停恢复以阶段实测报告为准。

## 已核对

- P05 提交 `f7286a1` 的完整检查点已经包含 `initial_weights`、优化器、调度器、scaler、RNG、batch 顺序签名和消费游标；不需要降低校验要求。
- P06 新检查点嵌入阶段交接状态与推理快照。旧 P05 点缺嵌入交接时，从来源运行读入之前已完成阶段，并验证对应恢复点与快照哈希，不继承所选历史点之后的阶段。
- 派生运行使用独立 `run_dir`，新快照使用不可覆盖文件名；所选父恢复点与父模型文件只读。
- 完整恢复与新权重初始化不同：恢复点配置、数据、上游、资产、初始化权重、可训练参数、采样器顺序均经过一致性校验。
- 不同恢复请求并发时，数据库事务内重新检查状态，已经变为 `QUEUED` 的运行不会再受理第二次恢复；同 request_id 返回原结果。

## 发现并修复

1. **派生运行自身暂停点续训。** 永久 `parent_run` 谱系不能同时充当每次恢复来源。增加 `resume_source_run`，允许当前运行或指定父运行；从自身新点恢复时仍保留父谱系。
2. **S1 轮边界 Python RNG。** 上游真实 sampler 在新轮次会 seed/shuffle 全局 Python RNG，恢复第一批前不能再次回退到上一轮边界状态。重新捕获新轮 sampler 后 RNG，保持不中断轨迹。
3. **极短 CPU 步骤计时。** 低精度计时可能令首步 elapsed 为零。worker 改用高精度计时并保护速率分母。

## 独立 CPU 测试

`tests/test_s1_resume_rng.py` 共两项，在 worker 环境执行通过。GPU 边界用 CPU 代替，未占用 GPU；不能替代真实模型验收。

- 使用锁定上游真实 S1 sampler 和实际 `train_stage`，在已消费完一轮的边界恢复；数据加载逐样本消费 Python RNG，结果与不中断的下一轮轨迹完全一致，累计 batch/更新/轮次正确。
- 使用实际 `train_stage`、真实 CPU 优化器和实际检查点 payload/index 逻辑，目标 101 次更新；仅第 100 次触发周期保存、第 101 次触发最终保存。保存时实时梯度均已清除，payload 的 `grads_clear=True`、`accumulation_pending=0`。仅替换张量序列化，磁盘故障与真实模型序列化由其它测试覆盖。

执行入口（worker 没有安装 pytest，临时读取 API 环境已有 pytest；没有安装或修改环境）：

```powershell
.\.venv-worker\Scripts\python.exe -c "import sys; sys.path.append(r'E:\DictionForge\.venv-api\Lib\site-packages'); import pytest; raise SystemExit(pytest.main(['tests/test_s1_resume_rng.py','-q','--basetemp=data/pytest-s1-rng-periodic-review']))"
```

前端 `pnpm run build` 与 `node --test tests/progress.test.mjs` 通过。恢复点卡区分文件校验与训练结构校验；继承父起点单列且不提供派生按钮；终态不会继续显示等待暂停。
