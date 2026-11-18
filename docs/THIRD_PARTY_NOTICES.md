# 第三方组件与分发来源

本仓库不提交第三方模型、录音、Python 包或二进制。准备脚本下载固定版本到被忽略的目录，保留分发包中的 LICENSE。完整来源、大小与 SHA256 以 configs/tools.lock.json、assets.lock.json 和 upstream.lock.json 为准；Python 包版本见 api.requirements.txt / worker.lock.txt，前端见 pnpm-lock.yaml。

- Python 3.12.10：CPython 官方 NuGet 分发，解释器和标准库位于 vendor/tools/python，许可证见 LICENSE.txt。[官方 Windows NuGet 说明](https://docs.python.org/3.12/using/windows.html#the-nuget-org-packages)。该分发支持在项目目录运行脚本，不需要替换系统 Python。
- uv：Astral 官方发布包；使用项目目录作为安装和缓存位置。[uv 发布源](https://github.com/astral-sh/uv/releases/tag/0.11.32)、[许可源码](https://github.com/astral-sh/uv/tree/0.11.32)。
- Node：官方 Windows x64 发布包，LICENSE 保留了 Node 与其依赖的声明。[固定版本分发](https://nodejs.org/dist/v24.22.3/)。
- pnpm：npm registry 固定 tarball，除 SHA256 外，本次也比对了 registry 的 SHA512 integrity；包内 LICENSE 保留。[pnpm 源码](https://github.com/pnpm/pnpm)。
- Git for Windows：官方 MinGit 发布包及 LICENSE.txt。[固定发布](https://github.com/git-for-windows/git/releases/tag/v2.55.0.windows.2)。
- FFmpeg：Gyan 的 8.1.2 full build，使用发布方 SHA256，运行时另核对 ffmpeg/ffprobe 二进制与 P02 记录一致。[分发、构建配置与许可证说明](https://www.gyan.dev/ffmpeg/builds/)。
- GPT-SoVITS：官方 20250422v4 源码与锁定补丁。模型资产许可证与适用范围以各资产上游仓库为准。[上游仓库](https://github.com/RVC-Boss/GPT-SoVITS/tree/20250422v4)。

用户原始录音和本机生成音频不会随 Git 推送。
