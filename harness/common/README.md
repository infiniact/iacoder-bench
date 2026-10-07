# iacoder-harbor-base（快照）

所有 iacoder Harbor / Pier 适配器共用的 `BaseInstalledAgent`。来源与更新方法见 [../README.md](../README.md)。

- `iacoder_harbor_base/base.py`：安装、运行命令（含 `--no-web`）、config.toml 与密钥下发、收尾产物收集。
- `iacoder_harbor_base/install-iacoder.sh.j2`：Harbor 容器内的安装脚本模板。
- `iacoder_harbor_base/timeouts.py`：命令级超时与外层墙钟上限的推导。
- `iacoder_harbor_base/no-regression-*.sh`：上传进容器的回归检查包装脚本。
- `capability_probe.py`：在把失败归因给模型之前，探测 OpenAI 兼容端点支持哪些能力
  （工具调用、图像输入、流式、推理内容、长上下文召回）。密钥只从 `--api-key-env` 指定的环境变量读取，不写入报告。

```bash
python3 harness/common/capability_probe.py --base-url https://<endpoint>/v1 --model <model> \
    --api-key-env <KEY_VAR> --out probe.json
```
