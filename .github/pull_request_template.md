## 这个 PR 做了什么

<!-- 一句话说清，别写「优化代码」 -->

## 为什么

<!-- 解决什么问题；关联 issue 用 #123 -->

## 怎么验证的

- [ ] `python -m unittest discover -s tests -v` 通过
- [ ] mock 模式能完整出片（附时长/大小）
- [ ] 无 API Key 时仍能降级运行

## 截图 / 抽帧（涉及界面或渲染时必填）

## 检查清单

- [ ] 没有提交 `.env`、`config.json`、`projects/`、成片
- [ ] 新增配置项已加入 `.env.example` 与设置页
- [ ] 打包后路径仍然正确（可写用 `APP_ROOT`，只读用 `BUNDLE_ROOT`）
