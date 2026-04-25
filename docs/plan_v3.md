# 漫剧故事工坊 v3 — 完整实施方案

## 当前状态
- √ 4个Agent: 导演、编剧+角色+场景、音乐+音效、美术指导
- √ 8个DB表, SQLite持久化
- √ AnimateDiff管线 → ComfyUI → mp4 (已验证可用)
- √ Gradio UI 7个Tab (缺渲染/美术指导/导出)
- × 缺少：一键全流程、批量渲染、启动脚本、完整文档

## 实施步骤

### Step 1: 美术指导 Tab (ui/app.py)
- 添加"🎨 美术指导"Tab
- 功能：色调板生成、镜头语言设计、视觉一致性检查
- 调用 art_director.py 已有函数

### Step 2: 渲染 Tab (ui/app.py)
- 添加"🎬 渲染"Tab
- 功能：选择项目→选择场景→渲染→进度→预览→视频库
- 调用 pipelines/animate_pipeline.py
- 显示ComfyUI队列状态

### Step 3: 导出 Tab (ui/app.py)
- 添加"📦 导出"Tab
- 功能：ffmpeg合并场景、打包项目、导出剧本文本

### Step 4: 一键全流程 (core/orchestrator.py)
- 新建总控模块
- 流程：创意构思→导演分析→编剧→角色→场景→美术→音乐→音效→渲染→导出
- 全部保存DB + 自动触发管线

### Step 5: 批量渲染 (pipelines/batch_renderer.py)
- 多场景顺序渲染
- 自动ffmpeg合并
- 进度跟踪

### Step 6: 环境完善
- start.sh / start.py
- .env配置文件
- README.md
- ComfyUI健康检查
- 后台服务管理

### Step 7: 全流程验收
- 创建测试项目
- 一键全流程运行
- 输出验证
