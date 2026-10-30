# 漫剧故事工坊 · Story Agent System (v2)

本地、离线的「一句话 → 竖屏短剧成片」系统。LLM 写剧本和分镜,本机的图像/语音/音乐/视频模型逐项生成素材,ffmpeg 合成带字幕、配乐、响度归一的成片,并附 AIGC 标识与溯源。每一个产物(人物形象、声线、台词、关键帧、镜头视频、配乐、主题曲)都有**可编辑、可版本化、可重新生成**的规格。

```
一句话 → 策划 → 角色 → 剧本 → 场景 → 配乐 → 分镜 → 规格 → 生成素材 → 合成成片 → 交付包
         └────────── 故事(可全部手改) ──────────┘   └ 作业队列 + worker ┘   └ ffmpeg ┘
```

## 快速开始

```bash
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e '.[dev]'   # 第一次
(cd web && pnpm install && pnpm build)                                                  # 第一次:Web 界面
scripts/start.sh                       # worker + 网页服务;浏览器打开 http://localhost:<端口>(启动时打印)
.venv/bin/python -m storyagent media healthcheck   # 看看本机哪些模型/服务可用
```

在网页里:新建项目 → 写一两句故事前提 → 「开始制作」(进度条、剩余时间实时显示) → 在「故事」「创作」页随意修改 → 「生成素材」→「合成成片」→ 播放、下载成片或交付包。也可以全程命令行:

```bash
.venv/bin/python -m storyagent project create "雨夜"
.venv/bin/python -m storyagent workflow run --project 1 --inline        # 故事阶段,画进度条
.venv/bin/python -m storyagent workflow run --project 1 --stage produce # 素材(需要 worker 在跑)
.venv/bin/python -m storyagent workflow run --project 1 --stage compose
.venv/bin/python -m storyagent export episode --project 1 --out out/    # 成片 + 字幕 + 分轨 + 溯源
```

## 它由什么组成

| 部分 | 位置 | 说明 |
|---|---|---|
| 配置 | `storyagent/config/` | 内置 TOML 默认值 < `$STORY_AGENT_HOME/config/*.toml` < 环境变量 < 项目 < 作业;代码里没有写死的路径/端口 |
| 台账与规格 | `storyagent/media/` | 作业(幂等、租约、重启接管)、版本化资产、可编辑规格、重/轻任务仲裁、AIGC 合规 |
| 适配器 | `storyagent/media/adapters/` | mflux(图)、cosyvoice、seedvc、whisper(语音)、acestep(音乐)、phosphene / h3(视频)、lora_train、assemble(ffmpeg) |
| 故事流水线 | `storyagent/workflow/`、`storyagent/agents/` | 九阶段可恢复流水线;agent 是 TOML 数据,多后端 LLM |
| 成片 | `storyagent/compose/` | 先量语音再排时间轴、字幕、混音与闪避、QC、交付包 |
| 接口 | `storyagent/api/` | FastAPI + SSE(`/api/docs`) |
| 界面 | `web/` | React + TypeScript,中英文,生成自 OpenAPI |

换新机器:[docs/INSTALL.md](docs/INSTALL.md)(`storyagent setup` 检测、列清单、写配置、体检)。
远程使用(手机/iPad 网页登录):[docs/REMOTE.md](docs/REMOTE.md)(账号登录 + Tailscale HTTPS)。

文档:[docs/WORKFLOW.md](docs/WORKFLOW.md)(流水线)· [docs/MEDIA_CONTRACT.md](docs/MEDIA_CONTRACT.md)(媒体契约)· [docs/CONFIGURATION.md](docs/CONFIGURATION.md)(所有旋钮)· [docs/API.md](docs/API.md) · [docs/RUNBOOK.md](docs/RUNBOOK.md)(排障)· [docs/USER_GUIDE.md](docs/USER_GUIDE.md)(使用)· [docs/PARITY_MATRIX.md](docs/PARITY_MATRIX.md)(v1 功能去向与已知缺口)。

## 开发

```bash
.venv/bin/pytest                                                   # 单元/契约测试(live 测试需要 -m live)
.venv/bin/ruff check storyagent tests && .venv/bin/mypy            # 静态检查
cd web && pnpm typecheck && pnpm lint && pnpm test && pnpm build  # 前端
```

约束由 `tests/test_guards.py` 强制:`storyagent/` 内不得出现 `/Users/`、`localhost`/`127.0.0.1` 字面量、`shell=True`、`pkill`、中文字符串字面量(提示词放 TOML,界面文案走 `storyagent/locales/{zh-CN,en}.json`)。

## 约定

* **离线**:不联网抓取、不下载模型(缺什么由 `media healthcheck` 指出,下载由你决定)。
* **不动别人的进程**:只探测外部服务,绝不 `pkill`;`scripts/start.sh` 只管自己启动的两个进程。
* **AIGC 合规默认开启、可关闭**(全局 / 环境变量 `STORY_AGENT_COMPLIANCE` / 每个项目):显式标识、元数据、声音克隆授权、受限许可确认;溯源记录永远写入台账。
* v1 旧实现保留在 git tag `v1-final`。
