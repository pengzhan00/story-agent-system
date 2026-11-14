# HANDOFF · 把 H3-Image 接成 storyagent 的一个 image backend

> 发起人：Hermes（只做事实采集与运行，不改 storyagent 代码）
> 执行人：Claude（storyagent 代码唯一写者）
> 日期：2026-11-14　状态：待接线（本机已跑通，未接入管线）
> 相关笔记：`~/Documents/Obsidian Vault/技能库/AIGC-图像/H3-Image 本地落地与出图对照（h3-image-mlx）.md`

## 1. 目标

把本地 **MiniMax H3-Image**（MLX 量化实现）接成 image backend，供给关键帧/参考图编辑场景。
它在本机唯一不可替代的两项能力：**原生 4096²** 与 **最多 16 张参考图的自然语言编辑**。
**不要**把它当成"中文文字渲染主力"——本机 Z-Image Turbo 已覆盖且快一倍（25.3 s vs 52–58 s）。

## 2. 已就绪的事实（Hermes 已实测，可直接依赖）

| 项 | 值 |
|---|---|
| 可执行文件 | `~/myworkspace/h3-image-mlx/.venv/bin/h3-image-mlx`（`h3-image-mlx 0.1.0a4`，mlx 0.32.4） |
| 权重目录 | `~/myworkspace/h3-image-mlx/models`（INT8，46 GB，`verify` exit 0） |
| 子命令 | `fetch` / `verify` / `generate` |
| generate 必需参数 | `--models DIR --output PATH.png --prompt TEXT` |
| 可选参数 | `--width/--height`（32 的倍数，256–4096，默认 1024）、`--seed`（默认 42）、`--reference PATH`（可重复，≤16，顺序即 `<Picture n>`）、`--dit-mode {auto,stream,resident}`、`--precision {int8,bf16}`、`--backend {auto,w8a8,portable}` |
| 采样 | 固定 6 步采样器，**没有步数/cfg 参数**（不要照搬 Krea/SDXL 的 steps/cfg 口径） |
| 输出 | 一律 RGB PNG，写进 `--output` 指定路径 |
| 成功判据 | 退出码 0 且输出文件存在；stdout 只打印输出路径 |
| 实测耗时（ComfyUI 已停，本机） | **1024² resident 24.2 s**（★推荐档）；1024² stream 39.2 s；2048² resident 82.9 s；参考图编辑 1024×1152 stream 86.1 s；4K 4096² stream ≈8 分钟 |
| 内存 | resident 1024² 峰值约 21 GB；stream 1024² 约 3.2 GB、4K 约 7.2 GB |

## 3. 建议的接入形态（供 Claude 决定实现细节）

- **一图一进程**：`subprocess.run([...], timeout=..., check=False)`，串行执行，**禁止并发**。
- **档位默认 `--dit-mode resident`**（1024² 24.2 s，约为 stream 的 2.4 倍速）；只有在 swap 打满、
  或要与 ComfyUI 等大内存进程并存时才传 `--dit-mode stream`（1024² 3.2 GB，慢但省内存）。
  建议把它做成配置项而不是写死，方便按机器状态切换。
- 建议 `timeout` 分级：1024²/2048² 给 600 s；4096² 给 1200 s（本机 stream 实测约 8 分钟）。
- 参考图模式与文生图模式走**同一张参数表**，只是 `refs` 非空时追加 `--reference`。
- 失败重试上限 1 次（代价是一分钟级），不要循环重试。
- 输出目录按项目约定落 `<project>/renders/` 之下，文件名带 `--seed` 便于复现。

## 4. 需要 Claude 实现的（代码层，Hermes 不改）

1. image backend adapter：拼参数、跑子进程、校验产物存在与 PNG 可读。
2. 模型注册表条目：`h3-image`（backend=local-cli，可执行路径、models 目录、默认 `--dit-mode stream`）。
3. tier 参数：`draft` = 1024² / seed 随机；`final` = 2048² 或 4096²（**由 Claude 定档位命名**）。
4. 参考图能力的入参映射：有序参考列表 → 重复 `--reference`，并把 prompt 里的 `<Picture n>` 序号与之一一对应。
5. 错误分类：权重缺失（`models` 目录不完整）/ 尺寸非法（非 32 倍数或超 4096）/ 超时 —— 这三类要能与"模型算坏了"区分。

## 5. Hermes 侧可配合的（非代码）

- `data/config/*.toml` 里的**管线参数**（路径、尺寸、超时、tier 默认值）可以由我加，代码改动不碰。
- 需要新的采样验证、耗时复测、样图主观评分，我随时跑。

## 6. 硬约束（写错就是秒退或白跑）

- 宽高必须是 **32 的倍数**，范围 256–4096；`1920x1080` 这类直接失败。
- 参考图上限 **16 张**（PNG/JPEG/WebP），`<Picture 1>` 对应第一个 `--reference`。
- **串行**；不要与 ComfyUI 大任务同时跑（本机 ComfyUI 常驻 12.3 GB）。
- prompt 用**自然语言长句**（该模型吃长描述），不要塞 ComfyUI 风格的标签串/权重语法。
- 参考图编辑场景下，prompt 里显式写"保持 <Picture 1> 的脸型/服装不变"，否则身份会漂。

## 7. 未验证 / 有风险

- **4K 已验证可跑**：4096² / stream / ComfyUI 已停 = **约 8 分钟**，产物 4096×4096、23 MB、无结构崩坏。
  4K 的 `resident` 档未测（预计 40 GB+ 驻留），接线先按 stream 排产。
- 多人多参考（3 张以上）的身份一致性未测。
- `--seed` 复现性未逐位核验（同 seed 两次是否逐像素一致）。
- 输出色调**默认偏灰、对比低**，管线里若需要统一观感，得加后处理（建议由 Claude 在 final 档接一步 curves/对比，不要改模型参数）。

## 8. 验收标准（建议）

1. 文生图 1024²、2048² 各 1 张：产物存在、PNG 可读、耗时记录。
2. 参考图编辑：1 张参考改场景 + 2 张参考合成，各 1 张；人工确认"人物身份与服装守住"。
3. 4K：**先关 ComfyUI**，跑 1 张，记录耗时与 `sysctl vm.swapusage` 前后值；跑不动就把 4K 档标记为"暂不可用"。
4. 失败路径：故意传 1000×1000（非 32 倍数）与不存在的参考图，确认报错可读、不重试循环。

## 9. 边界

- 本次交接**不含任何代码改动**；Hermes 只提交了本节事实与 `~/myworkspace/scripts/` 之外零改动。
- 权重与 venv 在 `~/myworkspace/h3-image-mlx`，属"本机资产"，不要放进 storyagent 仓库。
