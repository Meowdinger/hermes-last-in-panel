# Hermes last_in 舰队面板（桌面插件）

Hermes Agent 桌面 App 的插件：一屏显示**每个 profile 的 Bot Chat 上下文实长**（`last_in` = 末回合送入模型的输入 token，含缓存命中），超阈值红色警示。给「多 BOT 会话轮换」工作流用——谁快撑爆上下文、谁空闲可轮换，一眼可见。

> 配套轮换 cron 脚本同口径：本面板的数据与「按 token 阈值轮换 Bot Chat」脚本完全一致，可直接互相对照。

## 它长什么样

- **状态条 chip**：`fleet N`，N = 超阈席位数（>0 红色，0 绿色）
- **右侧面板**：逐席位一行——席名 / last_in（如 `97k`）/ 最后调用相对时间；超阈整行红字，回合进行中显示绿点
- 5s 自动刷新，纯只读，不写任何文件

## 数据口径

| 指标 | 来源 | 说明 |
|---|---|---|
| canonical Bot Chat | 各 profile `state.db` | `WHERE title='Bot Chat' AND archived=0 ORDER BY started_at DESC LIMIT 1` |
| `last_in` | 各 profile `logs/agent.log*` | 该会话最后一笔 `API call #N: … in=<n> out=` 的 `in=`（含缓存命中 ≈ 上下文实长） |
| 忙碌 | `state.db` `session_turn_leases` | 存在未过期租约 = 回合进行中（重启/轮换会打断它） |
| 阈值 | **按模型上下文窗口取比例**：`threshold = context_length(model) × 0.80` | 窗口从 Hermes `agent.model_metadata.get_model_context_length()` 取（本地缓存，毫秒级）；取不到时回退 `ROTATE_TOKEN_THRESHOLD`（默认 250,000）。比例可用 `LAST_IN_THRESHOLD_RATIO` 覆盖 |
| 排除名单 | 默认 `default,gameplayer` | 环境变量 `LAST_IN_EXCLUDE`（或 `ROTATE_EXCLUDE`）覆盖，逗号分隔 |

只读实现：`state.db` 以 `mode=ro` 打开；日志只读；文件级 mtime/size 缓存避免重复解析。

## 安装（两个半边）

```
~/.hermes/plugins/last-in/dashboard/plugin_api.py   ← 后端 API（FastAPI router）
~/.hermes/plugins/last-in/dashboard/manifest.json
~/.hermes/desktop-plugins/last-in/plugin.js         ← 桌面 UI（独立 plugin.js 半边）
```

1. 按上面路径放文件（目录名必须 = 插件 id `last-in`）。
2. 在 **根** `~/.hermes/config.yaml` 的 `plugins.enabled` 里加 `last-in`（后端 Python 半边的导入门）。
3. **重启桌面后端**（`hermes serve`）让 router 挂载：`/api/plugins/last-in/snapshot`。
4. 桌面半边热重载：保存即生效，或 ⌘K → **Reload desktop plugins**。

## API

`GET /api/plugins/last-in/snapshot`（需 dashboard 会话 token，`Authorization: Bearer <token>`）

```json
{
  "bots": [{"profile": "pm", "sid": "2026…", "model": "gpt-6.1-sol",
            "last_in": 514016, "threshold": 840000, "window": 1050000, "pct": 61.2,
            "busy": false, "last_call_at": 1793…, "source": "log"}],
  "threshold_fallback": 250000, "threshold_ratio": 0.8,
  "excluded": ["default", "gameplayer"],
  "count": 36, "over_threshold": 2, "busy_count": 1,
  "generated_at": 1793…, "scan_ms": 2045
}
```

每席的 `threshold` 由该席 canonical Bot Chat 的 `model` 列查窗口后算出（`window × 0.8`）；
`pct` = last_in / threshold。前端按 `pct` 排序（最危险的排最上），≥85% 提示色、≥100% 红色。

## 已验证环境

- Hermes 0.21.5（macOS 26.7 x86_64）；36 个 profile、33 MB 日志全扫约 2s（冷）、带缓存更快。

## 开发注意（踩坑记录）

- 桌面 `plugin.js` 是**纯 ESM、不编译**：用 `jsx()/jsxs()` 调用，禁 JSX 语法；只 import `@hermes/plugin-sdk`、`react`、`react/jsx-runtime`。
- 加载器按 **ES module** 解析：验证语法用 `node --check file.mjs`（`.js` 会走 script 目标，漏掉模块级严格错误，如 `] })` 紧跟 `}),` 的 ASI 形态）。
- 加载失败的报错位置常指向 SDK bundle 行号，不可信——用 `.mjs` 复现才是真位置。

## License

未指定（默认版权保留）。需要 MIT/Apache 等许可请提 issue。
