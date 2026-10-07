# configs/

iacoder 评测用的 `config.toml` 模板，通过 `IACODER_CONFIG` 指定。**文件里不放任何密钥**：
`api_key` 一律写成 `${ENV:变量名}`，运行时从宿主环境变量读取。

| 文件 | 模型 | 端点 | 密钥变量 | 计价文件 |
|---|---|---|---|---|
| `deepseek-flash.toml` | deepseek-flash | DeepSeek 官方 `api.deepseek.com` | `DEEPSEEK_API_KEY` | [`scoring/pricing/deepseek-flash.toml`](../scoring/pricing/deepseek-flash.toml) |
| `glm-5.3.toml` | glm-5.3 | 智谱 Coding 端点 `open.bigmodel.cn/api/coding/paas/v4` | `GLM_API_KEY` | 未填（TODO） |

约定：

- 不设步数上限：不要写 `[settings] max_turns`（`./bench preflight` 发现正数会拒绝启动）。
- 每轮运行把实际使用的配置文件 sha256 记进 `runs/<id>/manifest.json`。
- 改用中转端点时，绝对分数与官方端点不可比；同时要把域名加进 Pier egress 白名单（见 [harness/README.md](../harness/README.md)）。
