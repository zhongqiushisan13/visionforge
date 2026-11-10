# 本地出片：ComfyUI 与 H3

> 这条路的卖点不是「免费」，而是「可控」：模型、分辨率、步数、首帧都在你手里。代价是要自己把 ComfyUI 喂饱。

## 操作

### 1. 准备 ComfyUI

装好 ComfyUI（0.3.x 以上，需内置 MiniMax H3 节点），确认能打开 `http://127.0.0.1:8188`。

### 2. 放模型

```
ComfyUI/models/
├─ unet/  minimax_h3_fl2va_pruned-Q2_K.gguf
├─ loras/ minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors
├─ clip/  qwen3vl_32b_minimax_h3-Q2_K_M.gguf
└─ vae/   minimax_h3_video_vae_fp16.safetensors
```

文件名要和工作流里写的一致，改名就得改 `workflows/minimax_h3_gguf_i2v.json`。

### 3. 打开开关

把 `.env.h3.example` 的内容复制到 `%APPDATA%\VisionForge\.env`，至少这几行：

```bash
MINIMAX_MODE=comfyui
MINIMAX_VIDEO_WORKFLOW=minimax_h3_gguf_i2v.json
COMFYUI_BASE_URL=http://127.0.0.1:8188
MINIMAX_VIDEO_WIDTH=608
MINIMAX_VIDEO_HEIGHT=352
MINIMAX_VIDEO_STEPS=4
MINIMAX_VIDEO_TIMEOUT=5400
VF_FPS=24
VF_WIDTH=608
VF_HEIGHT=352
VF_WORKERS=1
```

改完重启应用。设置页「测通道」应显示 `comfyui-video` 在链上。

### 4. 跑一条试试

先只做一镜：分镜页选一镜「重拍」，或者「生成镜头」只补缺失的。5 秒镜头在 8G 显存上约 5~6 分钟。

### 5. 关键帧也接 ComfyUI（可选）

设置里把 `COMFYUI_WORKFLOW` 指向一个 SDXL 文生图工作流，并放好 checkpoint。没有 checkpoint 时关键帧会用本地占位图，H3 仍能跑，只是首帧是渐变卡片。

---

## 参数怎么调

| 参数 | 默认 | 调整建议 |
|---|---|---|
| `MINIMAX_VIDEO_WIDTH/HEIGHT` | 608×352 | 必须是 32 的倍数；8G 显存别超过 768×432 |
| `MINIMAX_VIDEO_STEPS` | 4 | 用了 turbo LoRA 就保持 4；调到 8 更慢但未必更好 |
| `MINIMAX_VIDEO_TIMEOUT` | 5400s | 本地慢，别按云端的 2400s 算 |
| `VF_WORKERS` | 1 | 单卡别并发，显存会爆 |
| `MINIMAX_LOCAL_IMAGE_MODE` | base64 | 写 `skip` 走纯文生视频 |

---

## 原理（为什么有这些限制）

### 为什么帧数必须是 4n+1

H3 的视频 VAE 在时间维度上按 4 帧下采样。帧数不落在 `4n+1` 上，尾部会被丢掉或补帧，表现为「最后半秒卡住」。程序按镜头时长自动对齐：5 秒 × 24fps = 120 → 121 帧。

同时 H3 单镜上限 15 秒，超了会按上限截断（`361` 帧 @24fps）。

### 为什么步数是 4 不是 28

工作流里挂的是 **4 步 turbo LoRA**。这类蒸馏模型就是为少步数训练的：4 步出片，28 步既慢又可能过曝。SD 生态的「28 步 + CFG 6.5」经验在这里不适用。

### 为什么分辨率要 32 的倍数

VAE 的空间下采样是 8，加上 patch 切分，宽高必须是 32 的倍数才能整除。608×352 ≈ 0.2MP，是 8G 显存的安全档；再大一档就容易 OOM 或被迫换页（速度断崖）。

### Q2_K 是什么

GGUF 量化格式。`Q2_K` 表示 2-bit 混合量化，把 30B 级模型压到能在消费级显卡上跑。代价是细节和稳定性略差；显存够的话优先 `Q4_K_M` 或 `Q8_0`。

### 为什么 i2v 比 t2v 稳

- **i2v（图生视频）**：给一张首帧，模型只需推断「接下来怎么动」，构图和人物长相由首帧锁定
- **t2v（文生视频）**：连构图都要模型自己想，同样提示词每次结果差异大

所以默认走 i2v；没有可用首帧时才用 `MINIMAX_LOCAL_IMAGE_MODE=skip` 走 t2v。

### 为什么占位首帧会「把字烙进画面」

本地占位图是用 `drawtext` 把提示词画在渐变上的。i2v 会把首帧当真实画面来延续，那行字自然就进了视频。要么装 SDXL 出真关键帧，要么用 `skip` 走 t2v。

### 为什么首帧上传而不是传路径

ComfyUI 跑在另一个进程里，读不到你项目的绝对路径。程序会先把图 `POST /upload/image` 传到 ComfyUI 的 `input/`，再把返回的文件名写进工作流的 `LoadImage` 节点。

### 工作流靠什么定位节点

不靠节点 id（每次导出都会变），靠 `_meta.title`：

| 标题 | 作用 |
|---|---|
| `POSITIVE` / `NEGATIVE` | 提示词，`text` 与 `prompt` 两种字段都认 |
| `LOAD_IMAGE` | 首帧，会自动上传 |
| 含 `width`+`height` | 分辨率（配了 `MINIMAX_VIDEO_*` 才覆盖） |
| 含 `length`/`num_frames` | 帧数 |
| `SAVE` / `CREATE_VIDEO` | 输出与帧率 |

自己换工作流时，把标题标好就能复用同一套适配逻辑。

### 为什么本地跑要等这么久

- GGUF 量化模型要边解压边算，比全精度慢
- 8G 显存放不下整模型，只能部分换页
- 视频是逐帧扩散，5 秒 121 帧就是 121 次前向

这也是为什么 `VF_WORKERS` 要设 1：并发只会让两张卡（其实是一张）来回换页，总时间更长。

### 升级 ComfyUI-GGUF 会踩的坑

GGUF 加载器对 H3 这种「无 KV 元数据」的模型需要额外兼容处理（按张量命名推断架构、补 5D 卷积形状、BF16 强制反量化）。升级 `ComfyUI-GGUF` 自定义节点会覆盖这些补丁，表现为加载即报错。升级后先跑一条单镜验证，报错就按原补丁重打。

---

## 常见问题

**报 `Value not in list ckpt_name`**：工作流里的 checkpoint 不存在。要么放一个 SDXL，要么换掉那个节点。

**H3 一直 queued**：ComfyUI 里有别的任务在跑。看 `http://127.0.0.1:8188/queue`。

**CUDA out of memory**：降分辨率（512×288）、关掉并发、确认没有别的程序占显存。

**出片是黑屏/纯色**：多半是首帧或 VAE 不匹配。先确认 VAE 文件名与工作流一致。

**跑完没有视频输出**：保存节点用了不认识的类。用 `SaveVideo` 或 `VHS_VideoCombine`，程序才认得出产物。
