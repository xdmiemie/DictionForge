# P06 实机暂停、重启、故障恢复与派生

2026-11-17，Windows / RTX 5080 16GB / Torch 2.9.0+cu128，固定上游与资产锁沿用 P02。输入为景元已发布版本 `76b5a08af4ca471f9c6fa9c903347e69`，5个训练片段，1个留出片段。没有修改原录音。

执行：`.venv-api/Scripts/python.exe scripts/validate_p06.py`，HTTP 调用真实服务与独立 GPU worker。原始结构化记录为 `P06_HTTP.json`；完整训练状态 CPU 复读另见 `P06_TRAINING_INSPECTION.*`。首次测试脚本错误及真实运行失败都保留在原始记录内。

| 项目 | 实际结果 |
| --- | --- |
| 父运行 | `be4045700e60491b95efb7101aaf411a`，双阶段各20次实际优化器更新 |
| 配置 | batch1 / fp16 / seed42；S2 rank32、初始LR 0.0001、每完整轮指数衰减；S1 LR 0.002恒定、累积4（尾组1） |
| S2 暂停 | u3，epoch0、cursor3，快照与完整点先落盘后 PAUSED |
| S1 暂停 | u3，epoch1、cursor4，已消费9批，完整累积周期后暂停 |
| 整服务重启 | S2与S1分别暂停后调用安全停止，等待服务退出，再启动独立进程并续训 |
| 阶段交接中断 | S2 u20点已登记、S1尚未更新时终止本运行worker，登记 INTERRUPTED，恢复后S2不重复step |
| 轮内中断 | S1恢复后产生未落点更新，再终止worker；从最后确认点重放未提交工作，累计目标仍20 |
| 最终计数 | S2 20更新/20批/4完整轮；S1 20更新/50批/10完整轮 |
| 训练峰值 | S2 4089878016字节，S1 2325302784字节，真实Torch allocated峰值 |
| 训练循环耗时 | S2累计14.422秒、S1累计8.063秒；不包含资产校验、模型载入、检查点写盘与服务重启 |
| GPU释放 | PAUSED返回后本运行worker退出，模型子进程已由engine回收；WDDM nvidia-smi记录N/A，不将其解释为0字节 |
| 历史派生 | 子run `a1e76ef07ae34bedb9dbb45590216c2f` 从父S2 u3点启动，再暂停并从子自身点续训，各20更新完成 |
| 父产物保护 | 派生前已登记的checkpoint/snapshot SHA全部不变，run_meta、stage_state与父完成快照一致 |

恢复明确核对全部优化器、scheduler、scaler、Python/NumPy/Torch CPU/CUDA RNG、批序及消费位置。S1新epoch的上游sampler具有Python RNG副作用，恢复时已保留该副作用，使用真实sampler的CPU回归验证。父子训练结果不宣称任意硬件位级确定性，具体差异见CPU检查报告。

本轮真实发现并修复：HTTP轮询短暂持有Windows文件读句柄，导致 `stage_state.json` 的原子替换返回 WinError5。现在仅对Windows 5/32/33做总计不到1秒的有限重试，复用已经fsync的临时文件；失败仍保留旧代。使用真正不共享删除权限的Windows句柄复现测试通过，原运行从最后有效点成功恢复。

测试脚本另外修复两处验证问题：Windows虚拟环境启动器PID与实际server PID不同，改为核验受控后代身份；原先对父目录所有文件做哈希比较，包含并行CPU检查器重写的 `checkpoint-inspection.json`，改为核对不可变训练产物与冻结状态。没有以重跑覆盖实际失败记录。

磁盘满、半写、坏点与清理保护测试见 `P06_CHECKPOINT_IO.md`。周期保存策略通过实际训练循环的CPU合同测试核验，故障夹具不冒充GPU训练。

前端已接暂停等待、恢复点文件校验、具体错误、父谱系与继承起点提示，生产构建/SSR通过。CUA访问权限阻塞仍未解除，浏览器操作补验留待P12。A26涉及新数据和来源发布模型的重新初始化，需要P07与P11完成后实测，当前不提前标PASS。

收尾竞态已处理：最终安全边界检查之后、任务终态落库之前受理的控制命令，在同一终态事务登记 `control_finished`（包含终态与错误），避免界面永久等待。针对性回归18 passed/2 skipped（API环境无Torch的实际循环测试另由worker环境通过）；新增晚到暂停、继承父点和Windows句柄用例另作单项核验。
