# P10 播放、保存与回收实测

日期：2026-11-18。复用 P09 的真实 v4 48k 当前结果，所有保存、回收和删除经实际本机HTTP执行；源录音、训练数据、模型权重未改。

`scripts/validate_p10.py` 实测：

- 独立保存 WAV `bfa6b69242ae406f9b04859837d763e3`、320kbps MP3 `7c4fff962a0a4f46b171b16f7c939e41`。均48000Hz单声道；MP3完整解码样本数与原生WAV一致。WAV哈希 `9d7f6a06d7b54bb052438745ea54cc07e41b8d31831882a79e950b72e0aa7186`，MP3哈希 `596018eccd87f1f77f4c4cd7f15e2e6b8b74abb1986e87e17adc0840c869e0b9`。
- WAV/MP3均验证Range206返回精确字节、越界Range416、HEAD媒体头及attachment下载。HTTP请求引用覆盖整个响应，断连在finally释放；相关真实ASGI测试通过。
- 用真实CPU队列锁让保存任务排队，再受理并完成新GPU合成。释放浏览器引用后，排队保存仍保护旧PCM；CPU任务完成后保留独立作品 `b2968e66c6294d3d9f654b2f0b2768b9`。
- 用真实Windows `CreateFileW` 不共享DELETE的句柄占用旧WAV，实际清理进入PURGE_PENDING且源文件仍在；关闭句柄后自动完成DELETED。旧音频与再次保存均HTTP410，历史原文可查。没有伪造文件锁失败。
- 非法新输入HTTP422后current不变。真实GPU任务 `ecd4b4ab7d6f4572a90952edf7462d07` 在已加载模型后取消，最终CANCELLED，监督器回收子进程与临时产物。
- 最后真实三句合成 `1ba9920f732a4eef902f6917b6c6a7b9` 为P10 current，原生48k、5.16秒，生成7.516秒、峰值2318460416字节。seed42起、sample_steps16、speed1、fp16，完整配置保存在报告。
- 创建12个带唯一验收前缀的作品，实际分页为10+2。跨页删除预览冻结12个ID；预览后另建第13个，保留其独立ID。给其中1个建立播放引用，确认返回11 deleted/1 protected，新增第13个不被删除；释放后仅确认这2个ID并删除。
- 安全停止PID5716（exit0）并重启PID3616后，`--verify-restart`验证两个永久作品的哈希、解码、Range及current恢复均PASS。

报告 `P10_HTTP.json` 包含请求、实际产物、精确删除集合及重启结果。P09历史临时音频现已按P10生命周期回收，原验收记录保留；永久作品在独立 `data/saved_speech/<id>`，不依赖旧临时文件。

回归首轮220 passed/6 skipped/1 failed。失败是历史FAILED合成快照新增可用媒体状态造成合同不一致；修复为仅完整音频结果附带媒体状态。修复后合成/媒体/作品定向56 passed。6项API环境无Torch的训练合同已有此前worker验证，未把跳过当PASS。更多租约、Windows句柄和受保护路径合同及浏览器结果在本阶段其他证据中记录。

最终全套：`.venv-api/Scripts/python.exe -m pytest tests -q --basetemp=data/pytest-p10-full --junitxml=data/p10-final-tests.xml`，222 passed / 6 skipped，96.97秒。随后补修外部FLAC媒体头，HTTP Range/HEAD/断连finally/数据引用保护/FLAC中的4项专项全部通过（`data/p10-media-header-tests.xml`，3.51秒）；没有重复执行GPU推理。唯一警告为已锁定Starlette的httpx TestClient弃用提示。

最终浏览器10项通过后经安全停止端点关闭PID3616，进程exit0。Windows异步socket日志留有一次远端关闭连接10054和15秒graceful shutdown上限提示（取消0个运行任务），应用shutdown完成；没有把此传输日志省略或称为无日志错误。P11代码更新前服务已退出。
