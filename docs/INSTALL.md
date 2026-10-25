# 新机器安装指南:从一台空的 Apple Silicon Mac 到出片

目标:换新电脑时,按引导一步步装好依赖、配好管线,最终能做出和参考机(2026-10-24 清查的那台)一样的成果——项目「小霸王」那样的 idea → 短剧 MP4 全流程。

**引导本身只做四件事:检测、列清单、写本机配置、跑体检。它从不自动下载模型权重,也不替你启动或结束任何进程。**
命令只打印出来给你;要执行,在终端里 `storyagent setup run 步骤编号`,看完命令(下载还要看来源、文件、大小)、输入 `yes` 才会跑,一次一步。网页「安装引导」页只读,不执行任何步骤。

- 命令行:`storyagent setup`(整套引导)· `setup check | plan | config | run | verify | profiles`
- 网页:顶部「安装引导」(`/setup`)——机器检查、按能力分组的组件表(状态/大小/许可/步骤)、待确认的下载清单、配置预览与写入、体检
- 清单数据:`storyagent/config/defaults/setup.toml`(可在 `$STORY_AGENT_HOME/config/setup.toml` 里加自己的组件,表会合并)

## 1. 先看你的机器够不够

| 项 | 要求 | 说明 |
|---|---|---|
| 芯片 / 系统 | Apple Silicon(arm64)、macOS 14+ | MLX 需要;Intel 或 Linux 不在支持范围(引导会标红) |
| 内存 | 视频 LTX-2.5 至少 36 GiB,建议 64 GiB;只写故事 24 GiB 起,建议 32 GiB;图片 16/24 GiB;音乐 16/20 GiB | 队列一次只跑一个重任务,所以按最大的算而不是相加。参考机 128 GiB。引导会把 `[runtime] memory_budget_gib` 写成内存的 75% |
| 磁盘 | 完整管线要下载约 100 GiB(见 §5),再留 20 GiB 余量;含 H3 约 +196 GiB | 引导按「待下载总量 + 余量」对照安装目录的剩余空间 |
| 网络 | 下载 Hugging Face / Ollama / GitHub | 下载走你自己的网络,引导不碰代理设置 |

## 2. 选一个方案(profile)

不必装全。`storyagent setup profiles` 列出:

| 方案 | 装什么 | 适合 |
|---|---|---|
| `story` | 基础 + 语言模型(Ollama + gemma4:31b) | 只写故事/剧本/分镜计划 |
| `stills` | story + 图片(FLUX.2 klein)+ 配音(CosyVoice 3);建议再装 whisper、ACE-Step 配乐、ComfyUI 音效 | **图+配音、没有视频**:镜头是关键帧加缓慢运镜。引导会把方案的 `video_share` 写成 0、从 `requires` 里去掉 phosphene,体检和生产计划都不再要求视频 |
| `full` | stills 必需 + ACE-Step + Pinokio/Phosphene(LTX-2.5);建议 whisper、音效 | **同参考机**,能做出项目 3 那样的整集 |
| `everything` | full + whisper、音效 + 快速关键帧(klein 蒸馏版)+ Seed-VC | 全家桶;LongCat 数字人、H3、Draw Things 仍是可选 |

任何方案都可以用 `--with 能力,能力` 把某个可选部分算成必需(例如 `--with voice_convert`),能力名见 `setup.toml` 的 `[capabilities.*]`。

## 3. 从零到出片(按顺序)

下面每一步,先看 `storyagent setup check`(状态)和 `storyagent setup plan`(缺什么、命令)。**标 ✋ 的必须人工**:要密码、登录、接受许可、或点图形界面。

### 步骤 0 ✋ 取得代码、建 Python 环境(引导之前)

```bash
xcode-select --install                      # ✋ 弹窗里点安装(命令行工具:git、编译器)
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"   # ✋ 官方安装命令,会要密码
brew install uv git
git clone <本仓库地址> story-agent-system && cd story-agent-system
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e '.[dev]'
```

(`storyagent` 本身只依赖 requests / fastapi / uvicorn,装完就能跑 `setup`。)

### 步骤 1 跑引导

```bash
.venv/bin/python -m storyagent setup                       # 整套:检查 → 缺什么 → 配置预览 → 体检(不写、不装、不下)
.venv/bin/python -m storyagent setup plan --profile full   # 只看清单:每项的命令、来源、文件、大小
```

要把工具装在一个干净的目录而不是默认位置(默认位置见 `defaults/services.toml`,即参考机的布局:`~/cosyvoice`、`~/ACE-Step-1.5`、`~/models/...` 等),加 `--root ~/storyagent-tools`:所有命令里的路径都会指到那里,`setup config --write` 会把对应的 `[tools.*]` / `[models.*]` / `[launch.acestep]` 路径写进你的配置。

### 步骤 2 逐项安装

对 `plan` 里每个缺的项,三种做法任选:
1. 自己复制命令到终端执行;
2. `storyagent setup run 步骤编号`(如 `uv.1`、`flux_klein_base_4b.2`),看完确认;
3. 手动步骤(✋)照说明做。

**下载类步骤**(`[download]`,模型权重)一律先显示 来源 / 文件 / 大小,要你输入 `yes`(或显式 `--yes`)才执行;`setup plan` 末尾有「等你确认的下载」总清单。

下表是参考机的依赖清单与对应步骤(大小为参考机实测,带 ~ 为估计):

| 能力 | 组件 | 怎么装(`plan` 里的步骤) | 大小 | 许可 |
|---|---|---|---|---|
| base | Homebrew ✋ | 官方安装脚本 | ~0.5 GiB | BSD-2 |
| base | uv | `brew install uv` | 小 | MIT/Apache-2.0 |
| base | **ffmpeg-full(带 libass)** | `brew install ffmpeg-full`(keg-only,二进制在 `$(brew --prefix ffmpeg-full)/bin`;`setup config` 会把它写进 `[tools.ffmpeg]`) | ~2 GiB | GPL-3.0+ 构建 |
| base | 网页(`web/dist`) | `brew install node pnpm`;`pnpm install --frozen-lockfile`;`pnpm build`(在 `web/`) | 0.3 GiB | MIT |
| base | 数据库 | `storyagent db migrate` | - | - |
| hub | `hf`(Hugging Face 命令行) | `uv tool install 'huggingface_hub[cli]'` | 小 | Apache-2.0 |
| story | Ollama | `brew install ollama`(或 ollama.com 的应用) | ~0.2 GiB | MIT |
| story | 语言模型 | `ollama pull gemma4:31b`(需要 Ollama 在跑:`storyagent ops launch ollama`) | **20 GiB** | Apache-2.0(Gemma 4) |
| image | mflux | `uv tool install mflux==0.22.0`(参考机版本;装到 `~/.local/bin`,PATH 里没有就 `uv tool update-shell`) | ~1 GiB | MIT |
| image | FLUX.2 klein base 4B 权重 | ✋ 先 `hf auth login`(若仓库要求接受条款);`hf download black-forest-labs/FLUX.2-klein-base-4B --local-dir ~/models/flux2-klein-base-4B` | **22 GiB** | Apache-2.0(9B 版是非商用,本系统不用) |
| voice | CosyVoice 代码 + Python 3.10 环境 | `git clone --recursive https://github.com/FunAudioLLM/CosyVoice.git ~/cosyvoice/CosyVoice`;`uv venv --python 3.10 ~/cosyvoice/venv`;`uv pip install --python ~/cosyvoice/venv/bin/python -r storyagent/setup/recipes/cosyvoice-venv.txt`;✋ pynini(见 §6) | 2 GiB | Apache-2.0 |
| voice | CosyVoice 3 权重 | `hf download FunAudioLLM/Fun-CosyVoice3-0.5B-2512 --local-dir ~/models/cosyvoice3/Fun-CosyVoice3-0.5B` | **9.1 GiB** | Apache-2.0 |
| voice | 资产库里至少一个声线 ✋ | `storyagent library add REF.wav --role voice --name 名字 --text "录音里说的话" --consent`(3–20 秒清晰人声,说话人同意) | - | 自备素材 |
| asr | whisper.cpp | `brew install whisper.cpp` | 小 | MIT |
| asr | whisper 模型 | `hf download ggerganov/whisper.cpp ggml-large-v3-turbo.bin --local-dir ~/myworkspace/models/whisper` | 1.5 GiB | MIT |
| music | ACE-Step 1.5 | `git clone https://github.com/ACE-Step/ACE-Step-1.5.git ~/ACE-Step-1.5`;`uv sync`(在该目录);权重 `uv run acestep-download` | ~2 + **9.4 GiB** | MIT |
| comfy | ComfyUI(Comfy Desktop)✋ | 从 comfy.org 下载安装,打开一次(端口 8188) | ~5 GiB | GPL-3.0 |
| sfx | Stable Audio 3 ✋ | 把 `stable_audio_3_medium.safetensors`(checkpoints)和 `t5gemma_b_b_ul2.safetensors`(text_encoders)放进 ComfyUI 模型目录;链接在 ComfyUI 模板浏览器(Audio) | **9.7 GiB** | Stability AI Community(年收入门槛内可商用,需署名) |
| keyframe_fast | klein 蒸馏版 ✋(可选) | `flux-2-klein-4b.safetensors`、`qwen_3_4b.safetensors`、`flux2-vae.safetensors` 放进 ComfyUI 模型目录 | 15 GiB | Apache-2.0 |
| video | Pinokio ✋ | pinokio.computer 下载应用 | 0.4 GiB | MIT |
| video | Phosphene + LTX-2.5 ✋ | Pinokio → Discover → Download from URL → `https://github.com/mrbizarro/phosphene` → Install;装完在 Pinokio 里点 Start(端口 8198) | **~27.5 GiB**(+后台 ~10.7) | MIT(面板);LTX-2.x Community License(模型,年收入门槛内免费商用) |
| voice_convert | Seed-VC(可选) | 代码 + `uv venv --python 3.11` + `-r storyagent/setup/recipes/seedvc-venv.txt`;✋ 检查点 `ar_base.pth`/`cfm_small.pth`;HuBERT、BigVGAN 用 `hf download` | ~1.7 + 3.7 GiB | 代码 GPL-3.0(独立进程运行);权重条款未核实 |
| avatar | LongCat(可选) | 代码 + venv + `hf download mlx-community/LongCat-Video-Avatar-1.5-q4-dmd-merged`(+ whisper-large-v3 配置) | 23 GiB | MIT |
| h3 | MiniMax H3(可选) | `git clone https://github.com/antirez/h3.c.git`;`make`;权重 `hf download MiniMaxAI/MiniMax-H3` | **196 GiB** | 代码 MIT;权重 MiniMax H3 社区许可(**地域限制:欧盟/英国/韩国/美国除外;商用需署名**) |
| drawthings | Draw Things(可选,受限内容方案) ✋ | App Store 安装;设置 → Advanced → API Server 打开 | 0.3 GiB | 见应用条款 |

### 步骤 3 写本机配置

```bash
.venv/bin/python -m storyagent setup config --profile full                     # 预览,什么都不写
.venv/bin/python -m storyagent setup config --profile full --write             # 写入 $STORY_AGENT_HOME/config/*.toml
.venv/bin/python -m storyagent setup config --llm-model qwen3:8b --write       # 小内存机器换一个小语言模型(所有角色)
```

只写与当前生效配置**不同**的部分:ffmpeg-full 路径、mflux 目录、`--root` 带来的路径、`[healthcheck] required`、`memory_budget_gib`、语言模型、以及按方案裁剪的管线 `requires`(没有视频的方案还会写 `video_share = 0`)。写之前先在临时目录里用真正的加载器试读一遍,读不进就一个文件都不写;被替换的文件保留成 `*.bak-setup-<时间>`(合并时原文件里的注释不会保留在新文件里,在备份里)。运行中的网页服务会自己热加载;worker 在空闲时加载。

### 步骤 4 启动、体检

```bash
.venv/bin/python -m storyagent ops launch --all        # 启动 [launch.*] 里列出且 bulk 的服务(Ollama、ACE-Step、打开 Pinokio);只启动、不停止
.venv/bin/python -m storyagent setup verify --profile full       # = media healthcheck,方案需要的算必需项;退出码 0 = 都可用
scripts/start.sh                                       # worker + 网页服务(端口见 [api]);也是重启守护,之后可在网页「系统」页「更新并重启」
```

`setup verify --start` 会先替你启动方案需要且在 `[launch.*]` 里列出的服务(同样只启动)。Phosphene 要在 Pinokio 里点 Start;ComfyUI 要自己打开;这两个系统不会替你启动。

### 步骤 5 做第一集

按 [USER_GUIDE.md](USER_GUIDE.md):网页新建项目 → 「开始制作」→ 给角色上传声线 → 「生成素材」(先看生产计划、确认)→ 「合成成片」。或命令行见 README「快速开始」。首次跑某个模型会加载权重,比后面慢;heavy 任务一次一个,这是设计。

## 4. 必须人工的步骤(为什么)

| 步骤 | 原因 |
|---|---|
| Homebrew、Xcode 命令行工具 | 要管理员密码 / 图形安装窗口 |
| Hugging Face 登录与接受模型条款 | 令牌只能你自己粘贴;条款要你本人接受 |
| 声线素材(参考录音 + 同意) | 版权/肖像权由你负责;系统只记录同意。参考机用 AISHELL-3 说话人(Apache-2.0);仅测试可用 macOS `say` 生成合成声线 |
| ComfyUI、Pinokio、Draw Things | 图形应用:自己安装、自己打开(系统不启动它们:ComfyUI/Draw Things 不在 `[launch]` 里;Pinokio 由启动台「打开应用」) |
| Phosphene 安装与 Start | 在 Pinokio 里完成;权重由 Pinokio 的安装流程下载,在那里确认 |
| ComfyUI 的音效/蒸馏关键帧模型 | 下载链接和许可在 ComfyUI 的模板里,文件名必须与 `setup.toml` 一致(本仓库没记录这两个模型的仓库地址,所以不替你猜) |
| Seed-VC 检查点 | 同上:先读权重条款 |
| 许可确认 | LTX-2.x、Stable Audio 3、MiniMax H3、FLUX 等的条款随模型页变化,以你下载时的页面为准 |

## 5. 大小汇总(参考机实测)

| 方案 | 待下载(权重/应用,不含 brew 小工具) |
|---|---|
| story | ≈ 20 GiB |
| stills | ≈ 20 + 22 + 9.1 ≈ 51 GiB;加 whisper 1.5、ACE-Step 9.4+2、音效 9.7 ≈ 74 GiB |
| full | stills(含音乐、音效、whisper)+ Phosphene/LTX-2.5 ≈ 27.5 → **≈ 102 GiB**(+ Pinokio 之后后台再下 ~10.7) |
| everything | full + klein 蒸馏版 15 + Seed-VC ~5 ≈ 122 GiB |
| 可选大件 | LongCat 23 GiB、H3 196 GiB |

(参考机上这些目录实际占用:`~/models/flux2-klein-base-4B` 22 G、`cosyvoice3` 9.1 G、`ACE-Step-1.5/checkpoints` 19 G(含 0.6B/4B 语言模型等额外的)、LTX-2.5 q4 19 G + q8 55 G(Pinokio 里)、LongCat 70 G(含 bf16 46 G)、H3 196 G、ComfyUI-Shared 167 G(含大量与本系统无关的检查点)。)

## 6. 已知不确定处(新机器上第一次会碰到)

- **CosyVoice 的 Python 环境**:`recipes/cosyvoice-venv.txt` 是参考机 venv 的 `pip freeze`(Python 3.10),只去掉了 `pynini`——那一项在参考机上是 conda 构建的(`file:///.../conda-bld/...`)。新机器先试 `uv pip install pynini==2.1.5`,装不上就 `conda install -c conda-forge pynini==2.1.5` 装进该环境。其余版本是否在新机器的 PyPI 上全部可得,**只能在新机器上验证**。
- **Seed-VC 环境**:`recipes/seedvc-venv.txt` 同理(Python 3.11);另需 `python -m ensurepip` 与 `pip install sphn julius lameenc openunmix einops`(Demucs 的依赖,见 RUNBOOK)。
- **ACE-Step**:`uv sync` 与 `uv run acestep-download` 取自它自己的 README(参考机上的目录是同样方式装的),下载哪些模型以它的下载器为准;启动台用 `--lm-model-path acestep-5Hz-lm-1.7B`,所以 1.7B 语言模型必须在 `checkpoints/` 里,引导检测 turbo、1.7B、vae、Qwen3-Embedding 四项。
- **Hugging Face 仓库名**:FLUX.2 klein base 4B、CosyVoice 3、whisper.cpp 的仓库名取自本机目录名与模型登记(`models.toml` 的 `version`),是否需要登录/接受条款以页面为准;`hf download` 失败时先打开仓库页面。
- **`brew install` 的公式名**(`ffmpeg-full`、`whisper.cpp`、`ollama`、`node`、`pnpm`、`uv`)已用参考机的 `brew info` 核对存在于 homebrew-core(`whisper-cpp` 只是旧名);Homebrew 公式以后可能改名。
- 引导**不检查**:各服务的真实可用性(那是 `setup verify` / 启动台的事)、模型权重是否完整无损(只看关键文件在不在)、你的网络。

## 7. 引导没覆盖的参考机状态

这些是参考机上存在、但不是「依赖」的数据,不会被引导复刻:`data/` 里的数据库与媒体(项目 1~4)、`data/config/` 里的个人覆盖(Obsidian 知识库的 wiki 白名单、Lustify 受限内容方案)、资产库里的声线、Draw Things 的 163 GiB 模型、ComfyUI-Shared 里与本系统无关的检查点。要把旧项目带过去:`storyagent pack export|import`(素材包,见 API.md)或直接拷 `data/`(先 `sqlite3 data/storyagent.db ".backup 备份"`,不要拷正在写的 `-wal`)。

## 8. 引导怎么保证不越界

- 检测只读:`which`、读文件、`xcode-select -p`、`brew --prefix ffmpeg-full`、`ffmpeg -filters`、`uv tool dir --bin`、对已配置服务的健康 URL 发 GET、ComfyUI 的 `/object_info`、只读打开 SQLite。测试 `tests/test_setup.py` 断言这些命令之外没有任何调用,且检测不会在 HOME 里创建任何文件。
- 步骤是 argv 列表(无 shell),`setup run` 一次一步、显示完整命令,没有 `--all`;下载步骤必须带来源/文件/大小;测试断言凡是 `hf`/`ollama`/`acestep-download` 都标成 `download`。
- 不杀进程、不启动不属于系统的进程:`setup verify --start` 只调 `Launcher.start`(即 `[launch.*]`)。`tests/test_guards.py` 对 `storyagent/setup/` 同样生效(无 `/Users/`、无 `localhost`、无 CJK 字面量、无 `shell=True`)。
- 不碰生产 `data/`:配置写入 `$STORY_AGENT_HOME/config/`(即你当前的数据家目录),先在临时目录试读,旧文件保留备份。

## 9. 验证范围(诚实说明)

已验证(自动测试,假环境:空 HOME + 只含桩程序的 PATH + 预设的命令输出/服务应答):清单自洽;空机器全缺并给出解析好的步骤;完整机器各方案就绪;部分安装(无视频/无音乐等);各类检测(ffmpeg 无 libass、Ollama 模型、ComfyUI 文件已知/未知、HF 缓存);`--root` 改路径后写出的配置能被真正的加载器读回并让检测通过;无视频方案的 `video_share = 0` 确实让分镜不选视频;配置写入的备份、校验、拒绝坏配置;`run` 的确认/拒绝/argv 列表;API 与网页。**另外**在参考机本身上(只读)跑了检测:除了沙盒里本来就没有的网页构建、数据库、声线外,30 个组件里被检测为已就绪的与实情一致。

**只能在真新机器上验证**:各 `brew install` / `uv tool install` / `git clone` / `uv pip install -r recipes/...` 在干净系统上是否一路成功(尤其 CosyVoice 的 pynini 与整套固定版本);各 `hf download` 仓库名与登录要求;`ollama pull`;Pinokio 安装 Phosphene 的流程;ComfyUI 模型放置后 `/object_info` 能否看到;`ffmpeg-full` 路径写入后的字幕烧录;从「引导全绿」到真正出一集的端到端耗时。

## 10. 速查

```bash
storyagent setup                                  # 整套引导(交互时会问方案和是否写配置)
storyagent setup profiles                         # 方案列表
storyagent setup check   [--profile P] [--root DIR] [--with CAP,..] [--json]   # 退出码 0 = 方案就绪
storyagent setup plan    [...] [--all]            # 缺什么 + 命令 + 下载清单
storyagent setup config  [...] [--llm-model NAME] [--write]
storyagent setup run STEP [...] [--yes]           # 单步执行,先显示再确认
storyagent setup verify  [...] [--start]          # 体检(方案需要的算必需项)
```

API(管理员):`GET /api/setup?profile=&root=` · `GET|POST /api/setup/config` · `POST /api/setup/verify`。
