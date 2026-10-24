# 本地大模型:Ollama、llama.cpp 与系统里的角色

故事、剧本、分镜、提示词都由本地大模型写。系统把“谁来写”做成**角色**(`director`、`writer`、`reviewer`、`character`、`scene`、`sound`、`composer`、`prompter`、`translator`、`default`),
每个角色可以指向不同的后端和模型,配置在 `llm.toml`;后端有 `ollama`、`llamacpp`(llama-server)和任意 OpenAI 兼容服务。
**系统默认只探测、不启动也不关闭这些服务**,除非你显式运行 `storyagent ops llm …`。

相关页面:[工具总览](../TOOLS.md) · [接入外部工具](../CONNECTORS.md) · 你的笔记《本地大模型部署与能力分布表》(2026-10-24 核验)

## 现在装了什么(2026-10-24 实测)

| 模型 | 体积 | 定位 | 局限 |
|---|---|---|---|
| `gemma4:31b` | 20 GB | 通用对话、原生多模态视觉、中文长文写作;**系统里所有角色目前都用它** | 吞吐慢,不适合高频补全;工具调用弱 |
| `qwen3.8-27b-abliterated:q6k` | 23 GB | 本地去拒答模型,中文好;**受限分级的项目用它**(见下节「按内容分级选模型」) | 去拒答模型没有统一评测,指令遵循和事实性可能下降;会思考,系统对 qwen3 默认关思考 |
| `nomic-embed-text` | 274 MB | 向量化 | |

存储:`~/.ollama/models` 67 GB,磁盘可用约 448 GB。`gpt-oss-20b` 与 `~/models/gguf` 下的 gpt-oss GGUF 在 10-24 删除,共释放约 81 GB,所以推理/工具调用的位置目前是空的。
`llama4:scout` 官方不支持中文,已淘汰。

候选(没有拉取):`qwen3.6:35b-coding`(约 23–24 GB,SWE-bench Verified 73.4%、Terminal-Bench 51.5%)补编码/agent 位;
替换 uncensored 的 `mgraffam/gemma4-agentic-fable-abliterated`(14 GB,MoE 最快)、`FableForge-AI/mythos-v2-9b`(5.8 GB,最轻);
**已试过、不好用,不要再选** `abliterant/qwen3.8-rana`(你的实测)。**不要选** 112 GB 的 `qwen3.8-flash-next-abliterated`(128 GB 机器上余量只有约 6 GB)。去拒答模型没有统一评测,指令遵循和事实性通常会下降,许可要回各自 README 核。

## 按内容分级选模型

受限分级(`content.toml` 里 `allow_remote = false` 的那种)的项目只走本机模型,并且可以和普通项目用不同的模型。在你自己的 `data/config/llm.toml` 里写:

```toml
[llm.ratings.restricted]            # 受限项目的所有角色
backend = "ollama"
model = "qwen3.8-27b-abliterated:q6k"
```

某个角色想单独换,再加 `[llm.ratings.restricted.roles.<角色>]`(同样的 `backend`/`model`)。选择顺序:调用时指定的模型 > 这个项目在「语言模型」页的选择 > 分级路由 > 页面上的全局选择 > `[llm.roles.*]`。
语言模型页里这样的角色显示来源「按内容分级(llm.toml)」。分级不允许离开本机时,如果路由到的后端不在本机(地址不是本机回环;或后端表里写了 `local = false`),调用会被拒绝(`remote_forbidden`)。
这只是接线:不改变任何提示词。故事怎么写由你的项目设定和角色文字决定,系统里的提示词模板仍是通用的。

## 命令

```bash
ollama list                      # 唯一可信的存量来源
ollama ps                        # 现在驻留在内存里的模型
ollama run gemma4:31b            # 对话试一下
ollama stop gemma4:31b           # 卸载(让出内存给视频/图像模型)
ollama pull <name>               # 拉取。>20 GB 的先确认;体积/上下文回 ollama.com/library/<name>/tags 核
```

系统里:

```bash
.venv/bin/python -m storyagent llm check          # 各角色指向的模型在不在、后端通不通
.venv/bin/python -m storyagent ops llm-unload     # 让出内存
```

- Ollama 的 `keep_alive` 在 `llm.toml` 里设成 `30s`:一个阶段内连续调用复用已加载的模型,之后释放 25 GB 以上的内存给媒体模型。
- 媒体重任务开跑前按 `llm_unload_policy`(`never`/`ask`/`auto`,默认 `never`)处理驻留的语言模型:`ask` 会让作业排队并说明原因,不替你卸载。
- 指定某个角色用别的模型:在自己的 `$STORY_AGENT_HOME/config/llm.toml` 里覆盖 `[llm.roles.writer]` 的 `backend`/`model`;网页「语言模型」页也能选。

## llama.cpp(可选后端)

`llama-server`(OpenAI 兼容,默认 `http://127.0.0.1:8080`,`/health` 体检)默认关闭,启用后才探测。它支持 JSON Schema 约束解码,
对“输出必须是合法 JSON”的结构化角色更稳。笔记里记录本机 llama.cpp 为 0.7.0,环境在 `~/venvs/llamacpp`。

## 内存口径(此处以 10-24 的核验为准)

- 旧表写“GPU 可用上限约 112 GB(`iogpu.wired_limit_mb=114688`)”**实测不成立**:`sysctl iogpu.wired_limit_mb` 是 0(走系统默认),没有做任何覆盖。
- 选型一律按保守口径:可用 ≈ 128 − 10 = 118 GB,权重之外留给 KV cache 和计算缓冲。
- 要改成 112 GB 需要你批准,且重启即失效(长期生效要写 LaunchDaemon),**没有执行**。

## 维护纪律

1. 每次变更后重跑 `ollama list` 核对,别凭记忆。
2. 拉取大于 20 GB 的模型先征求同意;不擅自删除在用的模型。
3. 竞品对比表(厂商自评)只能当结构参考,要交叉第三方榜单。
4. 视觉分析(`vlmshot`)用 gemma4:31b;MiniCPM-o 已移除。

## 你的实测笔记(Obsidian)

- [[本地大模型部署与能力分布表]]
- [[本机模型能力矩阵]]

(这些链接在知识库里指向同步进来的笔记;没同步笔记时只显示名字。)
