# Changelog

## v1.1.2 - 2026-11-08

### 修复

- 系统自检在 Windows 11 上误报 "Windows 10"：Win11 内部版本号仍是 10.0，Python 3.11 的 `platform.release()` 不识别；改为按 build 号（≥22000 即 Win11）判断，并补充显示 build 号。

### 本地 MiniMax H3（ComfyUI + GGUF）

- 视频工作流的分辨率/步数改由 `MINIMAX_VIDEO_WIDTH` / `MINIMAX_VIDEO_HEIGHT` / `MINIMAX_VIDEO_STEPS` 单独控制，不填就沿用工作流自带的值；不再误用关键帧出图的 `COMFYUI_WIDTH/HEIGHT`（此前会把 H3 的 608×352 顶成 1344×768 直接爆显存）。
- H3 帧数自动对齐 4n+1（时序 VAE 按 4 帧下采样，120 帧会被截成 117），并按 H3 上限 15 秒截断。
- `MINIMAX_LOCAL_IMAGE_MODE=skip` 时提交前自动摘掉首帧连线，支持纯文生视频。
- `BasicScheduler` 步数可通过 `MINIMAX_VIDEO_STEPS` 覆盖（H3 是 4 步 turbo LoRA，别套 SD 的 28 步）。
- 通道体检不再在 `mode=comfyui` 时去 ping `api.minimax.io`。
- README 补充「本地 H3（ComfyUI + GGUF 量化）」章节与踩坑说明，`.env.h3.example` 同步更新。
- 测试数从 48 增至 53：新增分辨率沿用/覆盖、帧数对齐与上限、调度器步数、纯文生视频等用例。

## v1.1.1 - 2026-11-07

### 安全

- 本地 API 默认启用随机 token 鉴权：`run/token` 仅存本机，所有 `/api/*` 请求必须携带 `X-VF-Token` 或 `Authorization: Bearer`。
- 新增 Host 白名单（`127.0.0.1` / `localhost` / `[::1]`），拦截 DNS rebinding；移除跨域 CORS 许可。
- `/media/*` 需要同源 Cookie 或 token 参数，外部网页不能直接读取项目素材。
- API Key 默认写到用户目录（Windows：`%APPDATA%\VisionForge\.env`；macOS/Linux：`~/.config/visionforge/.env`），并收紧到当前用户可读；exe 旁的老 `.env` 继续兼容。
- 桌面端增加单实例保护，避免两个窗口同时写同一份 `project.json`。

### 成本护栏

- 新增单项目预算上限 `VF_BUDGET` 与价格表 `VF_PRICES`。
- DeepSeek 剧本、MiniMax 视频、MiniMax TTS 的每次付费调用都会记录到 `project.json` 的 `meta.costs`。
- 超出预算时在调用前阻断，并在界面弹出错误；成片页可导出成本小结。

### 稳定性

- 打包版优先使用内置、版本锁定的 ffmpeg；所有 ffmpeg 候选启动时先跑最小 `drawtext` 自检。
- 子进程登记到 `run/child-*.json`，下次启动自动清理崩溃遗留的孤儿 ffmpeg/ffprobe。
- 设置页/系统自检展示最近的警告，减少“失败被吞掉、用户只看到没动”的情况。
- 日志输出兼容非 UTF-8 控制台：英文版 Windows 的 cp1252 终端下，中文日志不再抛 `UnicodeEncodeError` 打断提交任务、切换 provider 等关键路径。

### 本地 MiniMax H3

- 新增 `workflows/minimax_h3_gguf_i2v.json`：适配 ComfyUI 内置 H3 节点 + GGUF 量化模型。
- 视频工作流提示词字段兼容 `text` 与 H3 使用的 `prompt`。
- 支持 `RandomNoise.noise_seed`、`BasicScheduler.steps` / `cfg`，并同步 `CreateVideo` / `SaveVideo` 帧率。

### 测试

- 测试数从 22 增至 48：新增本地 API 防护、预算预留/并发护栏、断点续跑、任务状态流转、Provider 降级链、Mock H3 网关端到端测试。
- `tools/mock_h3_server.py` 的实现移入 `tests/fixtures/mock_h3_server.py`，命令行工具保留为入口包装。

### 文档

- README、使用手册、介绍词同步更新到 v1.1.1。
- Release 分发说明补充 SHA256SUMS 校验步骤。
