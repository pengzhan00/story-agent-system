# 漫剧故事工坊 - 多 Agent 架构 (2026-04-25)

## Agent 角色
│Agent          │角色                         │职责                                     │
│导演(Director) │统筹调度/任务分发              │理解需求、分解任务、编排管线、项目全局管理   │
│编剧(Scribe)   │剧本创作(故事线)               │生成大纲、幕场结构、扩写场景对话            │
│角色设计师     │角色设计                       │创建角色卡、关系网、声线设定                │
│场景设计师     │场景设计                       │场景描述、氛围、关键元素                   │
│音乐师         │BGM/主题曲/音效               │创作配乐描述、音景设计                     │
│美术指导       │视觉风格统一                   │镜头语言、色彩、风格一致                   │

## 技术栈
- 后端: Python3 + SQLite (core/) 
- Agent: 所有 Agent 基于本地 Ollama (gemma4/deepseek-r1)
- 管线: pipelines/ 连接 ComfyUI (AnimateDiff → 视频生成)
- UI: Gradio >=4.0 (ui/) 绑定 127.0.0.1:7860
- 视频: VAEDecodeTiled (规避 MPS INT_MAX)
