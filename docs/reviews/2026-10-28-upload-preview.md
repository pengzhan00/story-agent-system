# 上传图片预览恢复（2026-10-28）

范围：资产库共享预览组件；Git baseline 6d01896。原有 H3 和重启修复改动保持原状。

根因：卡片调用共享 AssetPreview，而它只按 image/video 开头判断；upload.image / upload.video
落到普通文件链接分支。截图中三个上传 PNG 的实际文件均存在，不是文件丢失或权限拒绝。
使用既有 mediaOfAsset 归一化类型；遮掩仍在原文件预览之前判断，未新增权限或文件访问接口。
该组件由资产库、工坊结果、候选素材与关键帧条共用，因此修复覆盖这些入口。

真实浏览器另复现：StrictMode 的 effect 清理关闭 dialog，重开后收到迟到 close 事件，导致放大闪退。
MediaLightbox 与同模式的 PreviewDialog 仅在当前节点确实已关闭时通知父组件，避免误消费旧事件。
两种弹窗都有回归，真实 Escape / 关闭按钮继续有效。

规则：媒体类别与播放器使用同一归一化 helper，不各自维护上传类型特例；dialog 的 close 事件不代表
节点在处理事件时仍关闭，必须检查当前状态。锁定素材不可生成原文件预览或放大按钮。

验证：
- 修改前上传图片/视频放大回归失败；修改后前端全量 106 项通过，typecheck / lint / build 通过。
- 普通/上传图片和视频、音频、未知文件链接、锁定图片 mask、迟到及真实关闭事件覆盖。
- Browser plugin not available；bundled Playwright + 已安装 Chromium，隔离 FastAPI/SQLite + Vite。
  上传 1024×1024 PNG 在资产库自然尺寸加载成功、放大保持打开、Escape 关闭，控制台无运行错误。
- 生产静态构建已更新，通过 HTTP 验证 /library 和新 bundle；旧构建已备份。
- 本轮仅前端变更，无新数据库迁移或后端修改，未重复跑后端全量，不作整个产品发布就绪判断。

证据：/tmp/storyagent-upload-preview-web-tests.log、/tmp/storyagent-upload-preview-build.log、
/tmp/storyagent-upload-preview-browser/（脚本、缩略图/放大截图、浏览器结果）。
用户原素材未修改；生产后端与生成服务未重启。源码变更待提交。
