# LongCat-Video-Avatar:数字人(人像 + 语音 → 说话/唱歌视频)

LongCat 把一张**人像**和一段**语音**变成这个人说话(或唱歌)的视频。系统里的模型是 `longcat-avatar-1.5-q4`(`video.avatar`),
适配器 `storyagent/media/adapters/longcat.py`。它是**重任务**(480p 的 bf16 峰值约 50 GB 内存),一次只跑一个。

相关页面:[工具总览](../TOOLS.md) · [LTX-2.5](LTX.md)(口型对话 Lip-sync 是更快的替代,但只有约 5 秒) · [CosyVoice](COSYVOICE.md)(先生成语音)

## 装在哪

| 项目 | 内容 |
|---|---|
| 项目 | `~/myworkspace/longcat-avatar-mlx`(约 69–70 GB),`services.toml` 的 `[tools.longcat]` |
| 环境 | 自带 `.venv`:python 3.11.17、mlx 0.32.3、transformers 5.19.0、PIL、librosa、imageio、soundfile、torch,约 1.3 GB |
| 权重 | `weights/`;只有 `q4-merged`(默认 `LongCat-Video-Avatar-1.5-q4-dmd-merged`)和 bf16 在位,`merged` / `q8-merged` 没有(缺的变体报缺模型,**不会自动下载**) |
| 许可 | MIT |

## 怎么跑

```bash
env -u PYTHONPATH HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
  ~/myworkspace/longcat-avatar-mlx/.venv/bin/python \
  ~/myworkspace/longcat-avatar-mlx/scripts/run_inference.py \
  --weights ~/myworkspace/longcat-avatar-mlx/weights \
  --variant q4-merged --out <out.mp4> \
  --height 480 --width 832 --num-frames 93 --seed 1262727502
```

人像、语音、提示词通过**环境变量**传入脚本:`LONGCAT_IMAGE`、`LONGCAT_AUDIO`、`LONGCAT_PROMPT`(系统的适配器就是这样做的)。

| 参数 | 含义 | 默认 |
|---|---|---|
| `width` `height` | 输出尺寸 | 832×480 |
| `num_frames` | 帧数 | 93 |
| seed | 来自任务 | |

输入:`image`(人像,或 `refs` 里第一张图)、`audio`(语音,或 `refs` 里第一段音频)、`prompt`(这个人在做什么,**英文效果最好**)。

## 经验

- **参考图必须是横构图**,提示词里把场景写全(你的“修正配方”)。竖构图的人像效果差。
- 整段生成较慢、较短;短片已经跑通。需要更快的口型对话,先看 LTX 的 Lip-sync(图 + 音轨,约 5 秒一段)。
- 调用时一定要 `env -u PYTHONPATH`:解释器混搭(Homebrew python 3.11 配上别处 python 3.14 的 `PYTHONPATH`)会让二进制扩展崩溃(`cannot import name '_imaging' from 'PIL'`)。

## 卡在“加载模型”怎么办

症状:进程“一直在加载”,运行了十几分钟,CPU 约 3%,没有读任何模型文件。
真因是一个**陈旧的 HuggingFace 锁**:`~/.cache/huggingface/hub/.locks/models--openai--whisper-large-v3/` 下一个 0 字节的 `.lock` 文件,
huggingface_hub 的文件锁默认没有超时,永远在等。锁文件名是哈希,按 “whisper” 搜不到,要直接 `ls` 目录。

```bash
# 诊断三件套:CPU 低 + 没有模型文件句柄 + 没有 TCP 连接 = 卡在锁,不是在加载
ps -Ao pid,etime,%cpu,rss,state,command | grep <关键词>
lsof -p <pid> | grep -vE "\.dylib|/System/|/usr/lib"
lsof -nP -p <pid> | grep TCP

# 处置
kill <pid>
rm -rf ~/.cache/huggingface/hub/.locks/models--openai--whisper-large-v3
# 重试时别联网
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 …
```

同类的坑:两个 `hf download` 抢同一批文件会互相等锁,表面“进程在跑”但没有字节增长。杀干净 → 删锁 → 只留一个实例;
判断是否真在下载的唯一标准是目录大小(`du`)在增长,不是 `pgrep`。

## 系统里怎么用

- 提交前检查变体目录存在;输出 mp4 登记为素材;模型是重任务,由 broker 串行。
- 想要对口型:先用 [CosyVoice](COSYVOICE.md) 生成语音,再把人像和语音交给 LongCat;口型对话也可以用 `ltx-2.5-lipsync`。
