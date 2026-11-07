# Contributing to VisionForge

先谢过。为了让 review 不至于变成互相折磨，麻烦遵守下面几条。

## 本地开发

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # 填 key；不填也能用 mock 跑通
python run_app.py             # 桌面窗口
python run_app.py --serve     # 只起服务，方便调接口
```

跑测试：

```bash
python -m unittest discover -s tests -v
python tools/e2e_test.py      # 需要先 --serve 起服务；走 mock，不花钱
```

## 代码结构

```
videoforge/
  provider 层   providers/     一个外部服务一个文件，统一 ProviderResult / ProviderError
  pipeline 层   pipeline/      编剧 → 导演 → 剪辑，互不依赖 UI
  接口层        web/           FastAPI + 后台任务队列 + 单页前端
  环境层        config.py / sysprobe.py / encode.py
```

改动的判断标准：

- **能在没有 API Key 的情况下跑通吗？** 所有新功能必须有 mock/本地兜底。
- **失败会不会拖垮整片？** 单镜头失败只标记该镜头，不许抛到顶层。
- **路径能不能在打包后工作？** 可写文件进 `APP_ROOT`，只读资源进 `BUNDLE_ROOT`。

## 不要做的事

1. **别加假按钮。** 界面上出现的功能必须真的能执行；没实现的写进 README 的「没做的部分」。
2. **别提交密钥、`config.json`、`projects/`、成片。** `.gitignore` 已经挡了，别用 `-f` 绕过。
3. **别把 ffmpeg 命令拼成一整条巨型 `filter_complex` 还不写注释。**
4. **别改 `xfade duration` 为 0。** ffmpeg 会吞掉后续片段（这个坑踩过一次）。

## 提交信息

```
feat(editor): 支持 11 种 xfade 转场
fix(comfy): 本地请求不走系统代理
docs(readme): 补本地模型部署说明
```

## Pull Request

- 一个 PR 只做一件事，附带「为什么」而不是「改了什么」。
- 涉及渲染的改动，请贴一张成片抽帧或时长对比。
- 新增 provider 请同时更新 `videoforge/sysprobe.py` 的探测与设置页入口。

## 行为准则

技术讨论可以尖锐，人身攻击不行。对事不对人。
