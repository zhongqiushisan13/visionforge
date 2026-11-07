安全加固 + 成本护栏版本。本地 API 默认开启 token 鉴权，花钱的地方开始记账，崩溃遗留的 ffmpeg 会被回收。

## 下载

| 文件 | 说明 |
| --- | --- |
| `VisionForge-v1.1.1-win64.zip` | 便携包（约 59 MB），解压后双击 `VisionForge.exe`，免安装 |
| `VisionForge-v1.1.1-docs.zip` | 文档包：使用手册 8 篇 + 界面截图 + 技术报告，不装也不影响使用 |
| `SHA256SUMS.txt` | 校验值，用法见文末 |

系统要求：Windows 10/11 x64，需要 WebView2 运行时（Win11 自带；Win10 一般已随 Edge 安装）。

## 本版重点

### 安全

- 本地 API 默认启用随机 token 鉴权，所有 `/api/*` 请求必须带 `X-VF-Token`（或 `Authorization: Bearer`）；token 只落在本机 `run/token`，权限收紧到当前用户。
- 新增 Host 白名单（`127.0.0.1` / `localhost` / `[::1]`），拦截 DNS rebinding；移除了跨域 CORS 许可。
- `/media/*` 需要同源 Cookie 或 token 参数，外部网页无法直接读取你的项目素材。
- API Key 默认写入 `%APPDATA%\VisionForge\.env`（macOS/Linux 为 `~/.config/visionforge/.env`），不再明文躺在 exe 旁边；旧位置的 `.env` 继续兼容。
- 桌面端加入单实例保护，避免两个窗口同时写同一份 `project.json`。

### 成本护栏

- 新增单项目预算上限 `VF_BUDGET` 与价格表 `VF_PRICES`。
- DeepSeek 剧本、MiniMax 视频、MiniMax TTS 的每次付费调用都会写入 `project.json` 的 `meta.costs`。
- 并发镜头在真正花钱前先预占额度，超预算在调用前阻断，不会先烧后报；成片页可以导出成本小结。

### 稳定性

- 打包版优先使用内置、版本锁定的 ffmpeg，启动时先跑最小 `drawtext` 自检，PATH 里的 ffmpeg 只作后备。
- 子进程登记到 `run/child-*.json`，下次启动自动清理崩溃遗留的孤儿 ffmpeg/ffprobe。
- 设置页与系统自检展示最近的警告，减少「失败被吞掉、界面只是不动」的情况。

### 本地 MiniMax H3

- 新增 `workflows/minimax_h3_gguf_i2v.json`，适配 ComfyUI 内置 H3 节点 + GGUF 量化模型（Q2_K 级别显存友好）。
- 视频工作流提示词字段兼容 `text` 与 H3 使用的 `prompt`，支持 `RandomNoise.noise_seed`、`BasicScheduler.steps` / `cfg`，并同步 `CreateVideo` / `SaveVideo` 帧率。
- 需要自备 ComfyUI 与量化模型权重，仓库只提供工作流与配置样例（`.env.h3.example`）。

### 测试

- 测试数 22 → 48：新增本地 API 防护、预算预占与并发护栏、断点续跑、任务状态流转、Provider 降级链、Mock H3 网关端到端。

## 升级提示

- 从 v1.1.0 升级：直接解压覆盖，`projects/` 与 `logs/` 保留即可。
- 首次运行会在 `%APPDATA%\VisionForge\` 生成 `.env`（若无）与 `run/token`；设置页会显示 Key 文件位置与防护状态。
- 想关掉预算护栏：`VF_BUDGET=0`（0 表示不限）。
- exe 尚未做代码签名，SmartScreen 可能提示「未知发布者」，属预期；介意的话可自行校验 `SHA256SUMS.txt`。

## 校验下载

```bash
sha256sum -c SHA256SUMS.txt        # macOS/Linux
Get-FileHash .\VisionForge-v1.1.1-win64.zip -Algorithm SHA256   # Windows PowerShell
```

**Full Changelog**: https://github.com/zhongqiushisan13/visionforge/compare/v1.1.0...v1.1.1
