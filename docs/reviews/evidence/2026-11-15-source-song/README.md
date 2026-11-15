# 完整原曲与视频提示词修复验证

2026-11-15，基于 main `01ccdce33e91a48016e5c5966289e286365c5492`。

已验证事实：
- 项目 7 导入原曲 `afa35ea7a2cb496aab9de0b84daaab82` 实测 241.478667 秒，文件存在。
- 后续主题曲规格及两个 ACE-Step/YuE2 作业均请求 150 秒；旧 composer.theme 模板要求 60–180 秒。
  这是另生成短主题曲，不是原曲文件被物理截断。
- 用户选择完整原曲做视频。备份 `data/storyagent.db.bak-20261115-original-song` 后，
  通过 ProjectStore 为项目 7 设置 source_music 引用。旧资产与记录未删除。
- 第 1 集无镜头；当前第 2 集有 35 镜头。直接执行 build_episode_timeline、未入队，
  返回 241.479 秒，原曲为唯一 primary 音轨、loop=false、gain=1，无配音和音效。
- 导入镜头没有结构化 subject，直接保留其 visual_prompt；模板修复不会自动改写这些历史文本。

验证：
- tests/test_compose_timeline.py、test_adapter_assemble.py、test_workflow_media.py、test_detail.py、test_i18n.py 通过。
- tests/test_source_music.py、test_keyframe.py、test_workflow.py、test_pipeline_choices.py、test_api_brief.py 通过。
- 修改后再次执行 compose/source/keyframe 29 个测试通过。
- 新增 source_music 项目边界、强制生产跳过、无 LLM 重写测试。
- 真实 ffmpeg 合成 4 秒哨兵音并保留最后一秒，验证前段静音、末段有声、附加结束段静音。
- Ruff、Mypy（190 源文件）、git diff --check 通过。
- 前端全量 114 个测试通过，随后新增原曲播放器/显式取消测试，WorkflowSettings 共 3 个测试通过。
- API OpenAPI/TS 类型已重新生成；前端 typecheck、lint、build 通过。
- Vite 既有大包提示和 React Router future 提示仍存在，不影响本次检查通过。

规范见 docs/standards/MEDIA_PROMPTS.md。系统性缺口由 source_music 统一策略收敛到计划、生产、估算、装配，
不另起未经授权的外部模型测试。未运行全量后端或真实神经模型，不声称发布级完整审查或视觉质量已验收。
