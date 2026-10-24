# h3.c:MiniMax H3 的命令行程序

`h3.c` 是一个自带命令行的 C/Metal 推理程序,直接用 MiniMax H3 的权重在 Apple Silicon 上出**视频加同步音频**,不需要任何服务或面板。
系统里对应的模型是 `minimax-h3`(类型 `video.av`),适配器是 `storyagent/media/adapters/h3.py`。
更细的实测记录在你的 Obsidian 笔记《H3本地CLI_h3.c用法》;这一页是“怎么用、每个开关什么意思、系统怎么调它”的速查。

相关页面:[工具总览](../TOOLS.md) · [Phosphene 面板](PHOSPHENE.md)(它的 One Shot 也能用 h3 引擎) · [LTX-2.5](LTX.md)

## 它是什么,装在哪

| 项目 | 位置 / 内容 |
|---|---|
| 程序 | `~/myworkspace/h3.c/h3`(546 KB,2026-10-01 构建),系统读 `services.toml` 的 `[tools.h3] binary` |
| 权重 | `~/myworkspace/MiniMax-H3`,共约 196 GB:`FL2VA/`(首末帧、文生音视频,134 GB)和 `Ref2VA/`(参考图/参考视频,62 GB) |
| 依赖 | PATH 里要有 ffmpeg(适配器会自动把 ffmpeg 所在目录加进去) |
| 运行目录 | 必须在 `h3` 二进制所在目录运行,它在旁边找 Metal 着色器源码(适配器已处理) |
| 许可 | MiniMax H3 Community License:权重**排除欧盟、英国、韩国、美国**,要求标注 “MiniMax H3”。系统里模型记录是 `commercial_ok = false`,合规闸门会要求显式确认 |

## 最常用的几条命令

```bash
cd ~/myworkspace/h3.c

# 看设备和模型头信息,不加载权重(几秒)
./h3 -d ~/myworkspace/MiniMax-H3 --info

# 文生音视频:5 秒
./h3 -d ~/myworkspace/MiniMax-H3 -p "提示词" -o out.mp4 --seconds 5 --steps 20

# 首帧生成(图生视频)
./h3 -d ~/myworkspace/MiniMax-H3 -p "提示词" --first-frame start.png -o out.mp4 --seconds 5

# 首末帧都给
./h3 -d ~/myworkspace/MiniMax-H3 -p "提示词" --first-frame a.png --last-frame b.png -o out.mp4

# 参考图(Ref2VA 变体,和首末帧不能同时用)
./h3 -d ~/myworkspace/MiniMax-H3 -p "提示词" --ref-image face.png --ref-image outfit.png -o out.mp4

# 交互模式(不带 -p)
./h3 -d ~/myworkspace/MiniMax-H3
```

出片前先看内存:文本编码器(约 62 GB)和 DiT(约 62 GB)不会同时驻留,是先编码文本再换入 DiT,内存会顶到 80 GB 以上,
**这期间不要再开别的大模型**(系统里 h3 是“重任务”,一次只跑一个,由 broker 串行)。

## 参数表,按用途分组

### 基本

| 选项 | 含义 | 默认 |
|---|---|---|
| `-d, --model-dir PATH` | 模型目录(必填) | |
| `-p, --prompt TEXT` | 提示词 | |
| `-o, --output PATH` | 输出 mp4 | `outputs/h3.mp4` |
| `--width N` `--height N` | 输出尺寸 | 864×480 |
| `--render-width N` `--render-height N` | 模型内部用更低的分辨率渲染,省算力(再放大到输出尺寸) | 不设 |
| `--frames N` | 帧数,24 fps。**必须是 5+17n**(22、39、56、73、90…) | 56 |
| `--seconds N` | 按 24 fps 折算时长,和 `--frames` 二选一 | |
| `--steps N` | 去噪步数 | 20 |
| `--seed N` | 随机种子 | 42 |

### 条件输入(给它看的东西)

| 选项 | 含义 |
|---|---|
| `--first-frame PATH` / `--last-frame PATH` | 首帧 / 末帧图(FL2VA 变体)。系统的适配器要求末帧必须配首帧 |
| `--ref-image PATH` | 参考图(Ref2VA 变体),可重复,按给的顺序 |
| `--ref-image-size S` | 参考图尺寸:`match`(默认,跟随输出)或 `max` |
| `--ref-video PATH` | 参考视频,连它的音轨一起用 |
| `--ref-silent-video PATH` | 参考视频,丢掉音轨 |
| `--ref-video-audio VIDEO AUDIO` | 视频加一条独立音轨 |
| `--ref-audio PATH` | 单独的音频参考 |

FL2VA(首末帧)和 Ref2VA(参考图)是**两个不同的模型变体**,不能混用。

### 提速(用精度换速度,一级一级往上开)

| 选项 | 含义 |
|---|---|
| `--reuse 1\|2\|3` | 去噪结果复用:1 最接近精确,2 快,3 激进 |
| `--layers 50\|45\|40` | 用多少个 DiT 块:50 全部,45 快,40 激进 |
| `--core-reuse 1\|4\|6` | 核心刷新频率:1 每步,4 快,6 激进 |
| `--token-reduction` | 中间的块把视频 token 两两合并 |
| `--ssd-streaming` | bf16 的 DiT 层从 SSD 流式读入,内存紧时用(会慢) |
| `--use-int8-row-fc2` | 更快的单尺度 int8 FC2,M5 专用 |

系统里的草稿档(`tiers.draft`)就是 `frames = 22, layers = 40, reuse = 3`:最快、最糙,用来看构图和动作。

### 诊断和对照(一般不用动)

| 选项 | 含义 |
|---|---|
| `--info` | 只读模型头和设备信息 |
| `--profile` | 打印每个阶段的 Metal 耗时和内存分配 |
| `--show` | 每步显示一帧(M5,终端里) |
| `--zoom N` | 终端图像缩放 |
| `--frames-dir PATH` | 同时把帧写成 PPM 序列 |
| `--use-slower-*`、`--use-reference-rope` | 一系列“强制走参考实现”的对照开关,查画质差异时才用 |

## 在系统里怎么用

- 模型 `minimax-h3`:默认参数 480×864、56 帧、20 步。适配器 `h3.py` 把任务参数翻成上面的命令行,并且:
  - 帧数自动凑成 5+17n(`snap_frames = false` 可关);
  - 参考文件按后缀分流:第一张图当首帧、第二张当末帧;`inputs.mode = "ref2va"` 时图片都当参考图;音频给 `--ref-audio`,视频给 `--ref-video`;
  - 首末帧和参考图同时出现会在提交前被拒绝(`invalid_input`);
  - 进度从日志里读 “step n/N”。
- 支持的任务参数:`width height render_width render_height frames seconds steps reuse layers core_reuse token_reduction ssd_streaming int8_row_fc2 ref_image_size snap_frames`。
- 想用 h3 的某个系统没暴露的开关(比如 `--profile`),在「工具接入」页的**命令行向导**里读它的 `--help` 生成一个 `cli` 连接器即可,见 [接入外部工具](../CONNECTORS.md)。

## 能做、不能做

| | |
|---|---|
| 能做 | 长一点的镜头(一次 362 帧约 15 秒)、画面质感好、速度比 Draw Things 快很多;有原生同步音频 |
| 不能做 | 没有 LoRA(参数里没有 `--lora`),所以社区的 4–6 步 Turbo LoRA 在本机用不上;没有原生对白(对白要后配或走 LTX);没有原生 1080p,要放大 |
| 已知耗时 | 5 秒 512×512 约 204 秒;整片 13 镜 / 2 分 09 秒 / 1080p 约 3.5 小时(旧记录);更多数字以你笔记里的实测为准 |

## 常见问题

| 现象 | 原因和办法 |
|---|---|
| `h3 binary not found` | `[tools.h3] binary` 路径不对,或文件没有执行权限 |
| `MiniMax-H3 weights not found` | `[tools.h3] model_dir` 下没有 `model_index.json` |
| 提交被拒:首末帧和参考图不能混用 | 换成只给首末帧,或只给参考图 |
| 内存顶满、别的程序卡 | 正常:出片期间要独占约 80 GB。内存实在紧再试 `--ssd-streaming` |
| 想在欧盟/美国出片或商用 | 许可不允许,见上表;系统会在提交前要求你确认许可 |

## 还没验证的

这些在你的笔记里也标为“待验证”:5 秒 / 480p / 20 步的实际耗时和内存峰值、`--reuse 2 --layers 45` 的画质损失、
`--ssd-streaming` 是否真的降峰值、Ref2VA 的参考图一致性。项目文档里对 h3 是否实机验证过的说法不一致(`PARITY_MATRIX.md` 写已验证,`HANDOFF.md` 列为未验证),所以先别把它当默认视频引擎,用 1 个镜头实测后再定。
