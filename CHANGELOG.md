# Changelog

## v1.2.3 - 2026-11-10

### 修复

- **补全漏翻文案**：成片页右栏「成本小结 / 生成参数」整块、分镜页「出关键帧 / 预览 / 编辑 / 重拍」、时间线「导入素材 / 用当前剪辑合成 / 补渲缺失镜头」、转场与滤镜名称、下载与上传按钮等 51 条此前未进词库；词库 318 → 384 条 × 8 语言。
- **拼接文案取词**：`${...}` 后紧跟的静态文本（如「8 素材」「1. 剧本与分镜」「剧本：DeepSeek」）此前无法整句匹配，改为 `t()` 模板取词。
- **阿拉伯语不再整体镜像**：改为「文本 RTL + 外壳 LTR」——侧边栏、时间线、检查器保持左到右，时间轴方向不再倒转；数字/时长/模型名加 bidi 隔离，修掉「8 / 8」错位。
- 深链 `#dashboard` / `#storyboard` / `#timeline` / `#export` 之前只在设置页生效，现已修复。

### 测试

- 新增 2 条用例：18 个高风险词条必须在 8 种语言里都存在；阿拉伯语必须保持 LTR 外壳（总数 85）。

## v1.2.2 - 2026-11-10

### 修复

- 思考型模型（qwen3 / deepseek-reasoner 等）只返回 `reasoning`、正文为空时，报错从「返回空文本」改为明确提示：调大 `max_tokens`、关闭思考模式或换非思考模型。
- 「测通道」的请求预算从 24 token 提到 512，避免思考过程把预算吃光导致误判为通道故障。

### 测试

- 新增 2 条用例：思考内容为空正文时报错可读、正常正文不受影响（总数 83）。

## v1.2.1 - 2026-11-10

### 修复

- 本地模型（Ollama / vLLM / LM Studio）的请求不再被塞进系统代理：此前 `LLMClient` 和云端一样跟随 `HTTP(S)_PROXY`，`127.0.0.1` 的请求会被代理拒掉并报 502/连接失败；现在按 `base_url` 是否为本机地址决定（`trust_env = not is_local`），云端厂商行为不变。

### 测试

- 新增 1 条用例覆盖「云端走代理、本地绕过」两种策略，测试总数 81。

## v1.2.0 - 2026-11-10

### 多语言界面（9 种）

- 新增运行时 i18n 引擎：简体中文、繁體中文、English、Français、Deutsch、日本語、한국어、Español、العربية。
- 侧边栏新增语言选择器，选择结果存 `localStorage`，下次启动沿用；首次启动按系统语言自动匹配（zh-TW/HK 走繁體，其余按主语言）。
- 阿拉伯语自动切换 `dir="rtl"`，侧边栏、时间线与表单镜像排布。
- 词库以中文原文为 key（gettext 风格），共 318 条；带占位符的文案（`{n}` / `{sec}` / `{label}`）在 8 种语言里逐一校验占位符不丢失。
- 支持 `?lang=en` 临时切语言（分享、截图、排错用）与 `#settings` / `#timeline` 深链。
- 繁体词库由 OpenCC `s2twp` 做词级转换（影片/設定/檔案/網路），避免逐字直译。

### 多模型接口（不再局限 DeepSeek）

- 新增厂商注册表 `videoforge/llm_providers.py`，内置 11 个通道：DeepSeek、OpenAI（ChatGPT）、Anthropic（Claude）、Google（Gemini）、xAI（Grok）、智谱（GLM）、月之暗面（Kimi）、通义千问、OpenRouter、Ollama 本地、自定义 OpenAI 兼容网关。
- 协议层一分为三：OpenAI Chat Completions、Claude Messages、Gemini generateContent；`LLMClient` 按 `protocol` 自动组装请求、归一化响应与用量。
- 设置页新增「厂商 → 模型 → API Key → 测通道」四步；每家 Key 单独存槽位（`LLM_KEY_<PROVIDER>`），切回某家不用重填。
- 新增 `POST /api/system/llm-check`：可用表单里尚未保存的值先测通，返回延迟、样例回复与用量。
- 环境变量统一为 `LLM_PROVIDER` / `LLM_MODEL` / `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODE`；老的 `DEEPSEEK_*` 继续兼容，现有安装无需改动。
- 模型不存在时按厂商错误格式识别并给出「检查模型名/账号权限」的明确提示；Claude 温度自动夹到 1.0，Gemini 用 `responseMimeType` 走 JSON 模式。
- 云端调用默认跟随系统代理（`HTTP(S)_PROXY`），方便国内访问 OpenAI / Claude / Gemini / Grok。

### 修复

- 受限环境（沙箱、杀软拦截删除）下子进程登记文件删不掉会卡住启动与自检；新增 `VF_NO_CHILD_REGISTRY=1` 可关闭登记，排障用。
- 系统自检在 Windows 11 上误报 "Windows 10"（随 v1.1.2 修复一并进入本版）。

### 测试

- 测试数从 53 增至 80：新增多厂商注册表/配置解析/三协议请求组装/响应归一化/模型回退，以及词库完整性、占位符一致性、语言列表与脚本加载顺序。

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
