# ComfyUI:节点式工作流引擎

ComfyUI 是节点式的图像/音频/视频生成引擎,生态最大。本机装的是 **Comfy Desktop 应用**(独立版,mac-mps),
系统通过它的 HTTP 接口跑**工作流模板**(`comfy` 适配器 `storyagent/media/adapters/comfy.py`)。
目前系统自带的模板有两个:`flux2_klein_edit`(FLUX.2 klein 4B 图像编辑,拼关键帧)和 `stable_audio_3`(音效)。
你自己的工作流可以导入成新模型,见 [接入外部工具](../CONNECTORS.md)。

相关页面:[工具总览](../TOOLS.md) · [ACE-Step](ACESTEP.md)(ComfyUI 出人声会是静音,别走这条) · [FLUX / mflux](FLUX.md)

## 装在哪

| 项目 | 内容 |
|---|---|
| 应用 | `/Applications/Comfy Desktop.app`;设置在 `~/Library/Application Support/Comfy Desktop/` |
| 安装 | `~/Documents/Comfy/ComfyUI`(`ComfyUI/` 是源码,`standalone-env/` 是自带的 python 3.13 + torch) |
| 启动参数 | 安装记录里是 `--enable-manager`(启用 ComfyUI-Manager);窗口模式 |
| 端口 | 8188(`services.toml` 的 `[services.comfy]`,体检 `GET /system_stats`) |
| 模型库 | `~/ComfyUI-Shared/models`(活库);输入 `~/ComfyUI-Shared/input`、输出 `~/ComfyUI-Shared/output` |
| 模型目录 | `checkpoints`、`diffusion_models`、`unet`、`text_encoders`、`clip`、`vae`、`loras`、`controlnet`、`upscale_models`、`frame_interpolation`、`insightface` 等 |

系统**不会替你启动或关闭** ComfyUI:打开 Comfy Desktop 应用即可(启动页的按钮也只是打开应用)。

## 命令行启动(想脱离桌面应用时)

```bash
cd ~/Documents/Comfy/ComfyUI/ComfyUI
../standalone-env/bin/python main.py --port 8188 --enable-manager
```

常用启动选项(`main.py --help` 的节选):

| 选项 | 含义 |
|---|---|
| `--listen [IP]` | 监听地址,默认 127.0.0.1;不带值是所有网卡(`0.0.0.0,::`),局域网访问才开 |
| `--port N` | 端口 |
| `--base-directory` / `--output-directory` / `--input-directory` / `--temp-directory` / `--models-directory` | 改各类目录;`--extra-model-paths-config` 追加模型搜索路径文件 |
| `--enable-manager` | 启用 ComfyUI-Manager(装自定义节点) |
| `--disable-all-custom-nodes` | 排查自定义节点导致的问题 |
| `--highvram` / `--lowvram` / `--novram` / `--gpu-only` / `--cpu` | 显存策略:`highvram` 用完不卸到内存;`lowvram` 让文本编码器跑 CPU;`gpu-only` 全放 GPU |
| `--reserve-vram GB` | 给系统和别的程序留多少显存 |
| `--disable-smart-memory` | 积极把模型卸到内存,少占显存 |
| `--cache-lru N` / `--cache-none` / `--cache-ram` | 节点结果缓存:LRU 保留 N 个、完全不缓存(省内存)、按内存压力缓存(默认) |
| `--preview-method auto\|taesd\|latent2rgb\|none` | 采样预览方式 |
| `--force-fp16` / `--bf16-unet` / `--fp8_e4m3fn-unet` … | 强制精度 |
| `--use-pytorch-cross-attention` / `--use-sage-attention` / `--use-flash-attention` | 选注意力实现 |
| `--deterministic` | 用更慢的确定性算法 |
| `--offline` | 不让前端联网(同时关闭伙伴节点) |
| `--multi-user` | 每用户独立存储 |
| `--enable-cors-header [ORIGIN]` | 允许跨域访问接口 |

## HTTP 接口(系统适配器用到的)

| 接口 | 作用 |
|---|---|
| `GET /system_stats` | 体检 |
| `GET /object_info`、`/object_info/<节点类>` | 当前装了哪些节点、节点输入里可选的模型文件(提交前用来检查缺节点/缺模型) |
| `POST /prompt` | 提交 **API 格式**的工作流,返回 `prompt_id` |
| `GET /history/<prompt_id>` | 查结果:输出节点、文件名 |
| `GET /view?filename=…&subfolder=…&type=output` | 下载生成的文件 |
| `POST /upload/image` | 上传参考图(系统给每个任务起不重名的文件名) |
| `GET /queue`、`POST /interrupt` | 看队列、中断(取消不会中断已开始的任务,它自己跑完) |
| `POST /free` | `{"unload_models": true, "free_memory": true}` 让它卸掉缓存的模型(系统在出完一条后会调,避免占着内存) |

## 工作流格式:一定要 API 格式

在 ComfyUI 里菜单 **Save (API Format)** 导出(没有这个选项时,在设置里打开 Dev mode)。编辑器自己的保存格式(含 `nodes` / `links`)不能提交。
系统的模板在 API 图旁加一张 `bindings` 表(参数名 → 节点号 + 输入名),所以不靠猜图结构:适配器只往这些输入里写任务的值。

模板里常见的绑定:`prompt`、`negative_prompt`、`seed`、`steps`、`cfg`、`sampler_name`、`scheduler`、`width`、`height`、`duration`、`checkpoint`、`text_encoder`、`vae`、`ref_image`、`ref_image_2`、`ref_audio`。
参考图有一个 `references` 块时,适配器会为每张图接上 `LoadImage → ImageScaleToTotalPixels → VAEEncode → ReferenceLatent`,
`ref_megapixels` 决定参考图缩到多大(越小越快、保留的细节越少)。

导入自己的工作流:

```bash
.venv/bin/python -m storyagent tool import comfy my-flow.json --kind image.generate --name my-sd --family sd15 --dry-run
.venv/bin/python -m storyagent tool import comfy my-flow.json --kind image.generate --name my-sd --family sd15
```

也可以在网页「工具接入」页选文件、看识别结果、保存。写入的是你自己的配置目录,系统自带文件从不被改;许可字段留空、`commercial_ok = false`,读过模型许可再填。

## 系统自带的两个模板

| 模型 | 类型 | 说明 |
|---|---|---|
| `flux2-klein-4b-edit-comfy` | `image.edit` | FLUX.2 klein 4B 蒸馏版,4 步、不用 CFG,把人物和场景参考图拼成关键帧。需要 `flux-2-klein-4b`(checkpoints)、`qwen_3_4b`(text_encoders)、`flux2-vae`(vae)。实测 720×1280 约 56 秒。许可 Apache-2.0 |
| `stable-audio-3-medium` | `audio.sfx` | Stable Audio 3 做音效。需要 `stable_audio_3_medium` 和文本编码器 `t5gemma_b_b_ul2`。默认 5 秒、8 步、`cfg 1.0`、`lcm` + `simple`。许可 Stability AI Community(低于一定收入免费商用,需署名 “Powered by Stability AI”)。**别把音效路由到 ACE-Step**,它是音乐模型,出音效是噪声 |

## 什么时候不要用 ComfyUI

- **带人声的歌**:原生 ACE-Step 节点没有 5Hz LM,输出静音。走独立服务,见 [ACE-Step](ACESTEP.md)。
- **LTX 视频**:本机首选 Phosphene 面板,不是 ComfyUI 里的 LTX 节点(见 [LTX-2.5](LTX.md));ComfyUI 里部分量化路径在 MPS 上会报错。
- 取消不会打断 ComfyUI 里已经开始的任务;自定义节点装没装、模型文件在不在,提交前由适配器检查(缺节点报 `missing_tool`,缺模型文件报 `missing_model`)。

## 常见问题

| 现象 | 原因和办法 |
|---|---|
| `ComfyUI has no node X` | 工作流用了没装的自定义节点,在 Manager 里装,或换节点 |
| `… is not in ComfyUI` | 模型文件不在 `~/ComfyUI-Shared/models` 对应目录,或文件名不一致 |
| 提交后立刻报错 | 导出的不是 API 格式 |
| 出片后内存一直占着 | ComfyUI 缓存了模型;系统会调 `POST /free`,手动也可以 |
| 音频全静音 | 先 `ffmpeg … -af volumedetect` 判定,别在参数上反复试(见 [ACE-Step](ACESTEP.md)) |

## 你的实测笔记(Obsidian)

- [[本机模型能力矩阵]]
- [[本地 AI 工具链清单]]
- [[ACE-Step 1.5 XL SFT ComfyUI 工作流]]

(这些链接在知识库里指向同步进来的笔记;没同步笔记时只显示名字。)
