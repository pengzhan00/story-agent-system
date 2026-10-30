# 本机能力真实实测（2026-10-30）

> 最新人工验收：用户反馈口型不准，a2v 口型质量未通过。下表 succeeded 仅为技术执行状态；中文支持度原因待验证，暂不重测。

结论：真实服务、小样媒体和统一图执行已验证，详见逐项状态；这是运行时功能测试，不是正式分辨率的产能或人工质量验收。测试始于 2026-10-29 晚，2026-10-30 继续。基线 main@b5df9e3 + 未提交工作树；未部署/未推送。

测试数据家目录：`data/run/live-control-20261029`。每个主样例由 GraphStore → 原 media worker → 实际 adapter/服务运行，未使用 fake adapter。生产数据库未写入测试资产；只有已安装服务上的测试队列及文件产物。

| 样例 | 状态 | 实测耗时 | 产物与媒体探测 |
|---|---|---:|---|
| DT / FLUX.2 Klein 出图 | succeeded | 15.43 s | [查看产物](/Users/pengzhan/myworkspace/projects/story-agent-system/data/run/live-control-20261029/projects/_unassigned/media/image.generate/87f6fc2253c34db69ac1aa28a25c9963.png)：512×768，图片/无声视频 |
| CosyVoice3 配音 | succeeded | 38.78 s | [查看产物](/Users/pengzhan/myworkspace/projects/story-agent-system/data/run/live-control-20261029/projects/_unassigned/media/audio.tts/ca015348bd4343fe88122de24cfd0d37.wav)：7.760 s，带音轨 |
| ComfyUI / Stable Audio 3 音效 | succeeded | 14.18 s | [查看产物](/Users/pengzhan/myworkspace/projects/story-agent-system/data/run/live-control-20261029/projects/_unassigned/media/audio.sfx/62cca713d2c7473198a32c18a118d18b.flac)：2.043 s，带音轨 |
| Phosphene t2v | succeeded | 21.32 s | [查看产物](/Users/pengzhan/myworkspace/projects/story-agent-system/data/run/live-control-20261029/projects/_unassigned/media/video.generate/92c3798062924f2a99b1b4be9470f390.mp4)：384×640，1.042 s，图片/无声视频 |
| Phosphene i2v | succeeded | 21.23 s | [查看产物](/Users/pengzhan/myworkspace/projects/story-agent-system/data/run/live-control-20261029/projects/_unassigned/media/video.generate/fdb51341349c46f9b87f4850200a1e90.mp4)：384×640，1.042 s，图片/无声视频 |
| Phosphene 单片 a2v + 原音拼接 | succeeded | 128.12 s | [查看产物](/Users/pengzhan/myworkspace/projects/story-agent-system/data/run/live-control-20261029/projects/_unassigned/media/compose.assemble/a0b357a22ba74e7bb5bd75c15fb7f232.mp4)：384×640，2.000 s，带音轨 |
| DT / LTX-2.3 备选 i2v | succeeded | 58.62 s | [查看产物](/Users/pengzhan/myworkspace/projects/story-agent-system/data/run/live-control-20261029/projects/_unassigned/media/video.generate/12628e4276b949e1a830c895e833ddc3.mp4)：320×576，1.042 s，图片/无声视频 |
| YuE2 写歌 | succeeded | 21.24 s | [查看产物](/Users/pengzhan/myworkspace/projects/story-agent-system/data/run/live-control-20261029/projects/_unassigned/media/audio.music/a2e69ffbc2f64c238386be39f4f7697d.wav)：14.999 s，带音轨 |
| YuE2 cover | succeeded | 76.66 s | [查看产物](/Users/pengzhan/myworkspace/projects/story-agent-system/data/run/live-control-20261029/projects/_unassigned/media/audio.music/429dd74ca3af43a2a432587f712e316d.wav)：14.999 s，带音轨 |
| ACE-Step t2a | succeeded | 7.11 s | [查看产物](/Users/pengzhan/myworkspace/projects/story-agent-system/data/run/live-control-20261029/projects/_unassigned/media/audio.music/4aed37d9a4294147ad4514e4f0614b69.mp3)：10.000 s，带音轨 |
| ACE-Step cover | succeeded | 19.31 s | [查看产物](/Users/pengzhan/myworkspace/projects/story-agent-system/data/run/live-control-20261029/projects/_unassigned/media/audio.music/3dfb06c78b3b49af8f28d0c6d03c5aed.mp3)：10.000 s，带音轨 |
| ACE-Step 局部 repaint | succeeded | 4.09 s | [查看产物](/Users/pengzhan/myworkspace/projects/story-agent-system/data/run/live-control-20261029/projects/_unassigned/media/audio.music/33dfce96ff8c49f1b6c6bbec08862120.mp3)：10.000 s，带音轨 |
| Demucs 已配置目录 / 人声与伴奏 | succeeded | 6.16 s | [查看产物](/Users/pengzhan/myworkspace/projects/story-agent-system/data/run/live-control-20261029/projects/_unassigned/media/audio.separate/f98cb63c13f745f5b1a94c742819b5fd.wav)：14.999 s，带音轨 |
| 多片 a2v / 锚点续接 / 拼接 | succeeded | 208.54 s | [查看产物](/Users/pengzhan/myworkspace/projects/story-agent-system/data/run/live-control-20261029/projects/_unassigned/media/compose.assemble/ab2d4516ea3d4e6e9aac8c0052651a84.mp4)：384×640，3.500 s，带音轨 |

## 额外实证

- 最终单片 a2v 精确 2.000 s；与原始配音前2秒波形相关度 0.999187，RMS 比 0.997326，说明用了原音且没有重复叠加混音。
- 多片续接必须分别检查视频流与音频流的时长；只读容器长度不能验收。末段帧数向上取整，最终 stream-qc.json 记录实际流时长。
- 实际图片已通过已启用鉴权的隔离 API 采用到镜头，项目图预演成功；真实视频/歌曲的精确版本进入 LOCAL-TEST-ONLY 发布准备台账并完成资产反查，未向平台发帖。
- 从实际 a2v 视频提取闭口锚点成功，时间为 1.625 s；该路径是原 media worker 的 control-anchor utility job。
- 已真实拆出约15秒歌曲的人声与伴奏；重复提交同一配置命中缓存，run 成功且没有创建新的 job，两个输出 ID 保持一致。
- 音视频使用 ffmpeg 解码，记录非静音电平、尺寸、时长、帧率及不同帧数量。具体值见 evidence。口型自然度、音色和歌曲审美没有替代人工试听/试看。

## 首轮失败及闭环

| 发现 | 原因 | 修复与复测 |
|---|---|---|
| 音效不能进入统一图 | 历史 MODULES 漏 audio.sfx | 补共享工坊/控制目录与双语标签；Comfy 真实2秒音效成功；加入全内置创作模型契约覆盖测试 |
| DT 参考图视频 422 | init_images 是512×768，请求却是320×576 | 最终参数合并后等比缩放并补边适配，不改原文件；图片/视频 HTTP 尺寸回归；DT LTX-2.3 真实25帧成功 |
| ACE-Step cover/repaint 400 | 服务禁止客户端绝对文件路径 | 分开 multipart 上传源与参考音频，保留服务路径保护；真实 cover/repaint 均成功 |
| Demucs 找不到955717e8 | 旧目录权重格式不适用；嵌套 .extra 未被 adapter 消费 | 复用 Phosphene 已有80 MiB左右 .th，校验 SHA256 前缀8726e21a，旧目录增加指向它的符号链接，无权重下载/复制；兼容 metadata，明确目录在派发前检查格式；真实人声/伴奏成功 |
| 多片成片容器3.5秒但视频只有3.375秒 | 尾段帧数按8n+1向下取整，容器音轨掩盖短视频流 | frames_for改为向上覆盖；重写有界 overlap；utility分别验视频/音频时长；加入真实 ffmpeg 负例及短尾段回归，并重新运行整链 |
| 视频首轮等待内存；YuE2 首轮主动停止 | 多服务加载时可用内存约30 GiB，LTX 估计64+余量8；YuE2 检测到换页增长 | 未关闭保护；资源回落后 t2v/i2v/a2v 与 YuE2 成功。多片测试经用户同意临时退出 DT，可用内存约85 GiB，再测试并恢复 DT；恢复状态见 evidence |

## 验证与边界

- 本轮末次相关后端回归141 passed（76.54 s）；Ruff、Mypy（183源文件）、i18n通过。前端108测试、typecheck/lint/build通过；JS大包提示仍存在。没有把此前938项全量测试称作本次修改后的全量结果。
- 没有下载模型。约80 MiB 的 PyTorch Demucs 文件原本就存在于 `phosphene/mlx_models/demucs/hub/checkpoints/955717e8-8726e21a.th`；`~/models/demucs/` 的新增 .th 是符号链接，原 safetensors 保留。
- ACE-Step 原先离线，通过系统现有 launcher 启动，保持在线；用户同意后临时退出并重开 Draw Things。未改默认生成模型、运行内存门槛或生产 schema。
- 视频小样为384×640、DT备选320×576，1–3.5秒，draft质量；不能据此推算正式832×1536/1080p成片时长。
- 未对图片编辑、ASR、数字人、声音转换、增强、训练等所有目录能力逐一跑实测；本报告的通过范围以表格为准。
- 源码、测试与报告均仍在未提交工作树。完整 registry/时间线/旧入口迁移等剩余范围沿用实施报告，系统发布结论仍 partial。

## 复测入口

```sh
PYTHONPATH=. <dev-python> scripts/live_control_smoke.py --home <绝对隔离目录> --cases image,speech,sfx,t2v,i2v,a2v,dtvideo,music,cover,ace,acecover,acerepaint,stems_configured,a2v_sequence
```

脚本复制既有配置到独立目录，复用已有服务；连续排队30秒会取消自己的测试运行并记录阻塞，最多等单例30分钟；成功样例默认不重复跑。需要重测同名成功样例时使用新隔离目录，或保留原记录后显式管理数据；不要删除生产数据。

运行证据在 [evidence目录](evidence/2026-10-30-live/result.json)，后续治理规则在 [能力运行时验收约束](../specs/capability-runtime-verification.md)。


<!-- lipsync-human-review-20261030 -->
## 2026-10-30 用户口型验收：未通过，暂停重测

用户反馈：“口型还是不太对”。本轮 a2v 样例的**技术链路通过，口型质量验收未通过**。文件可解码、音视频流等长、原音一致，并不能证明嘴形与音素对齐；原先的 succeeded 只代表任务执行成功，不代表口型质量已接受。a2v 当前按实验可选能力记录，不能列为已验证的稳定口型方案；本次仅更新验收记录，不修改运行默认值。

“中文支持度不足”是待验证假设，现有证据不足以归因。需要区分语言/音素适配、固定音画偏移、源音频起点与切片接缝、语速/停顿及渲染参数等候选原因；本轮不开展调查或重测，不新增模型下载。

按用户要求先不重测，也不派发后台测试。后续需要测试时，由 Codex 下发小范围任务给其他执行工具，执行方负责长任务、采样、指标与日志，Codex 只制定对照条件、审阅证据和更新结论，不再亲自长时间轮询生成。最小对照先比较同人像/参数下的中英文单片，再比较单片与拼接，避免同时更换语言、模型和管线导致无法归因；得到明确执行指令前保持待办。
