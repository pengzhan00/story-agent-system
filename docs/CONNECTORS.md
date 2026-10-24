# 接入外部工具(连接器)

系统不绑定某一个工具:每个“模型”在 `models.toml` 里声明它的 `provider`(由哪个适配器执行),换工具只改配置。
这份文档讲怎么把你已有的工具接进来,按**不用写代码 → 写一点配置 → 写 Python** 的顺序。

| 你有什么 | 做法 | 状态 |
|---|---|---|
| ComfyUI 里做好的工作流(图、声、乐、视频) | 导出 API 格式 JSON → 导入(网页「工具接入」或命令行) | ✅ |
| 一个命令行程序(TTS、生成器、ffmpeg 滤镜…) | `provider = "cli"`,在 `[models.X.connector]` 里写命令模板 | ✅ |
| 一个 HTTP 服务(同步或异步任务) | `provider = "http"`,写请求/轮询/取结果的模板 | ✅ |
| 以上都不合适 | 在 `<config>/plugins/` 放一个 Python 文件,注册自己的适配器 | ✅ |

配置目录是 `$STORY_AGENT_HOME/config/`(下文 `<config>`)。**改了配置不用重启**:服务每几秒看一眼配置目录(`[api] reload_seconds`,默认 3),
变了就重新读;worker 在手上的任务都做完之后(空闲时几秒内)换上新配置,从不在一个任务中途换。读不进去(比如 TOML 写坏了)时
沿用旧配置,在日志里说明原因,下次文件再变会重试。也可以马上读:`POST /api/config/reload`(管理员,返回是否成功和原因)。
插件是 Python 代码,和别的配置一样会被重新载入。

## ComfyUI 工作流

### 1. 导出

在 ComfyUI 里打开工作流,菜单 **Save (API Format)**(没有这个选项时,在设置里打开 Dev mode)。
编辑器自己的保存格式(含 `nodes` / `links`)不能用,导入时会明确拒绝。

### 2. 导入

```bash
.venv/bin/python -m storyagent tool import comfy my-flow.json --kind image.generate --name my-sd --family sd15 --dry-run
.venv/bin/python -m storyagent tool import comfy my-flow.json --kind image.generate --name my-sd --family sd15
```

或者网页 **工具接入**:选文件、选用途、先看识别结果、保存。(接口:`POST /api/tools/import/comfy` 预览,
`POST /api/tools/comfy` 保存,`GET /api/tools` 列表;`/api/tools` 只有管理员能用。)

保存会写两样东西,都在你自己的 `<config>` 下,系统自带的文件从不被改:

- `<config>/workflows/<name>.json`:模板(节点图 + `bindings` 表 + 结果节点)。
- `<config>/models.toml` 里的一段 `[models."<ref>"]`:`provider = "comfy"`、`workflow = "<name>"`、`kind`、`family`、
  `timeout_sec`,以及从工作流里读到的默认值(`[params]`:步数、cfg、采样器、尺寸、帧数…)。**许可字段留空、
  `commercial_ok = false`,请读过模型的许可再填。**

已存在同名模板或模型条目时拒绝,加 `--force`(网页上的“替换”)才覆盖;系统自带的模型名不能占用。

### 3. 识别规则(读懂图,不靠猜)

只按已知的节点类和输入名绑定,认不出的会在“识别结果”里提示,不会悄悄放过。

| 绑定名 | 来自 |
|---|---|
| `prompt` / `negative_prompt` | 顺着采样器/引导器的 `positive` / `negative` / `conditioning` 连线找回去的文本编码节点(`CLIPTextEncode`、Flux、SDXL、Qwen…)。没有采样器时按节点顺序猜,并提示。ACE-Step 的 `tags` → `prompt`,`lyrics` → `text` |
| `seed` | `KSampler.seed`、`KSamplerAdvanced` / `SamplerCustom` / `RandomNoise` 的 `noise_seed` |
| `steps` `cfg` `sampler_name` `scheduler` | 采样器、调度器、`CFGGuider`、`KSamplerSelect` |
| `width` `height` `frames` `fps` `duration` | 名字含 `Latent` / `Scheduler` / `Resolution` 的节点上的 `width` `height` `length`/`frames` `frame_rate` `seconds`;`LTXVConditioning` 的帧率 |
| `checkpoint` `text_encoder` `vae` `lora` | `CheckpointLoaderSimple` / `UNETLoader`、`CLIPLoader` / `DualCLIPLoader`、`VAELoader`、`LoraLoader` |
| `ref_image`, `ref_image_2`… / `ref_audio` | `LoadImage`(按节点号排序)/ `LoadAudio`。任务里 `refs` 的文件被上传到 ComfyUI,名字写进这些输入 |
| 结果 | `SaveImage` `SaveVideo` `VHS_VideoCombine` `SaveAnimatedWEBP` `SaveAudio`…,按用途挑;视频会按 `gifs` / `videos` / `images` 依次找 |

一个绑定名可以指向多个节点(例如同一个 `width` 写进两个节点)。模板是普通 JSON,识别得不对时直接改 `bindings`。

### 4. 用起来

保存后(服务马上、worker 空闲时)新模型就是同类里的一个候选:项目页的**管线面板**里能选到它,工坊也能直接试。
同 `family` 的图像生成与图像编辑自动搭配;视频同理。

### 限制

- 只认 API 格式;自定义节点是否装在你的 ComfyUI 里,提交前由 `validate` 检查(缺节点是 `missing_tool`,缺模型文件是 `missing_model`)。
- 视频、音乐能不能跑通取决于你的节点;带人声的 ACE-Step 请走 `acestep` 适配器(ComfyUI 原生节点没有 5Hz LM,人声是静音)。
- 取消不会中断 ComfyUI 里已经开始的任务(它自己跑完)。
- 参考图只支持图片和音频两类文件。

## 命令行工具(`provider = "cli"`)

```toml
[models."piper-voice"]
kind = "audio.tts"
provider = "cli"
family = "piper"
timeout_sec = 300
[models."piper-voice".connector]
argv = ["piper", "--model", "~/piper/voice.onnx", "--output_file", "{out}"]
stdin = "{text}"          # 可选:写进程序的标准输入
ext = "wav"
```

| 键 | 含义 |
|---|---|
| `argv` | 命令行,列表,**不经过 shell**。一个字符串是一个参数;**嵌套列表是可选的一组参数**:组里任何一个值任务没给,整组去掉(`["--seed", "{seed}"]`)。字符串里的值缺失是错误(`invalid_input`,不会运行) |
| `cwd`、`env` | 工作目录;额外环境变量(值里也能用占位符) |
| `stdin` | 写给程序标准输入的文本 |
| `ext` | 程序写出的文件后缀(默认:音频 wav,视频 mp4,图片 png) |
| `output` | 结果文件,默认 `{out}`;可以写通配符(`result_*.wav`,取最新的),相对路径在任务的工作目录里 |
| `progress_regex` | 日志里最后一次匹配的第 1 组是进度(大于 1 当百分比) |

程序在自己的会话里运行,输出进日志;取消会终止它;失败时错误信息带日志末尾。找不到程序是 `missing_tool`;
跑完没写出文件(或是空文件)不算完成(`output_missing`)。**健康检查**会列出找不到的程序。

### 命令行向导(不用手写 argv)

网页「工具接入」页的**命令行向导**:填程序路径(或把它的用法文字贴进去,贴的时候不运行任何程序),点「读取用法」,
系统运行 `程序 --help`(不经过 shell、无输入、15 秒超时,仅管理员)并把每个选项解析成一行:标志、取值类型、默认值、说明,
并按名字和说明猜出它在系统里的角色,你可以逐行改:

| 角色 | 生成的 argv | 说明 |
|---|---|---|
| 参数 `param` / 开关 `switch` | `["--steps", "{steps}"]` / `{ if = "token_reduction", args = ["--token-reduction"] }` | 默认值进模型的 `[params]`;任务没给就整组去掉 |
| 提示词 / 反向提示词 / 种子 | `"--prompt", "{prompt}"` / `["--seed", "{seed}"]` | |
| 输入:图片 / 音频 / 视频 | `["--first-frame", "{images.0}"]`;“可重复”的选项(如 `--ref-image`)是 `["--ref-image", "{images.*}"]` | **不用写路径**:取任务参考素材里该类型的第 n 个文件(`asset:`/`library:` 已由 worker 解析成绝对路径),没有就整组去掉 |
| 输出文件 | `"--output", "{out}"` | **不用写路径**:`{out}` 在任务的工作目录里,程序写完、读得出来才登记成素材 |
| 输出文件夹 | `"--frames-dir", "{work}/frames"` | 工作目录下的子文件夹 |
| 固定值 | `"--model-dir", "/path/to/model"` | 像模型目录这样不随任务变的值,写一次 |

模板语言新增两个写法(向导生成的就是它们):`{images.*}` 在可选组里表示“对每个图片重复一次这一组”,
`{ if = "名字", args = [...] }` 表示该参数为真时才加这些参数。`{images}` / `{audios}` / `{videos}`(列表)和
`{image}` / `{audio}` / `{video}`(第一个)按文件后缀把参考素材分开。

“生成命令和配置”会给出**一个任务会运行的命令**(用示例值填好),并把 `[models."…"]` 文本填进下面的连接器编辑器,
检查、保存的流程不变。接口:`POST /api/tools/cli/parse`、`POST /api/tools/cli/build`。解析是按常见的 `--help` 写法猜的,
遇到特殊写法(一个选项要两个值、值有固定几个选项)就手改生成的文本。

## HTTP 服务(`provider = "http"`)

服务地址写在 `services.toml`(自己加一段 `[services.<名>]`,有 `url`、`health_path`),模型里用 `service = "<名>"` 引用。

```toml
[models."my-img"]
kind = "image.generate"
provider = "http"
[models."my-img".connector]
service = "mytool"
mode = "async"                      # 或 "sync":第一个请求的答案就是结果
ext = "png"
required = ["prompt"]               # 任务必须有的值;缺了就在发请求前拒绝
headers = { Authorization = "Bearer {env.MYTOOL_KEY}" }     # {env.X} 取环境变量,密钥不进配置文件
[models."my-img".connector.submit]
path = "/generate"                  # method 默认 POST
json = { prompt = "{prompt}", seed = "{seed}", steps = "{steps|20}" }   # 也可以 data / query
id_path = "job.id"                  # async:任务号在答案的哪里
[models."my-img".connector.poll]    # 仅 async
path = "/jobs/{id}"
status_path = "status"
done = ["done"]
failed = ["failed"]
progress_path = "progress"          # 可选;按百分比的写 progress_scale = 100
error_path = "error"                # 可选:失败时显示的原因
[models."my-img".connector.result]
url_path = "result.url"             # 或 b64_path = "images.0",或 path = "/jobs/{id}/file",或 content = "body"
```

- `json` / `data` / `query` 里,值缺失的**条目被省略**(可选参数不用管),不会发出空值。
- 路径用点分(`data.0.url`,数字是列表下标)。`content = "body"`:答案的原始字节就是文件。
- 重启后 async 任务靠任务号接回;sync 的答案在内存里,重启后重新提交。
- 取消不会通知服务。健康检查对用到的每个服务请求它的 `health_path`。

## 在网页上写(不用手改文件)

网页「工具接入」页下半部分是连接器编辑器:选一个**起点**(命令行工具 / HTTP 同步 / HTTP 异步任务),模板会填进两个文本框 ——
上面是 `models.toml` 里的这一段(一个 `[models."名字"]` 表加它的 `.connector` 子表),下面是 `services.toml` 里的这一段
(HTTP 服务要有 `[services.<名>]`,命令行工具留空)。改成你的工具后:

- **检查**只是试运行,什么都不写。它会逐条指出问题(TOML 写法、名字、provider、kind、`argv`/`submit`/`poll`/`result` 缺什么、
  services 里有没有这个服务),最后还把这一段放进配置目录的**临时副本**,用真正的加载器读一遍,所以整份配置读不进去也会在这里报出来。
  许可 `license_tag` 为空只是提醒,读过许可再填,`commercial_ok` 保持 `false`。
- **保存并载入**先检查,没有错误才写:同名的表被替换,别的内容原样保留;写完服务端马上载入,worker 在手头任务做完后换上。
  重名要勾「替换同名的模型」;系统自带的模型名不能占用。

对应的接口(管理员):`GET /api/tools/templates`、`POST /api/tools/connector/check`、`POST /api/tools/connector`
(请求体 `{models, services, force}`)。写的就是 `<config>/models.toml` 和 `services.toml`,以后想手改也行。

## 占位符

模板里 `{名字}` 的取值:`{prompt}` `{negative_prompt}` `{text}`(歌词/要念的话)、`{seed}`、`{ref}`(第一个参考文件的绝对路径)、
`{refs.0}`、`{param.steps}` 或直接 `{steps}`(参数优先于输入)、`{out}` / `{work}`(cli)、`{id}`(http 轮询)、
`{env.NAME}`。`{名字|备用值}` 在任务没有这个值时用备用值(JSON 或纯文本)。整个字符串恰好是一个占位符时保留类型
(`"{steps}"` 给出整数,不是字符串)。

任务的参数来自 `models.toml` 的 `[models.X.params]` 和档位(`tiers`),和别的模型一样分层覆盖。

## Python 插件

前面都不合适时,在 `<config>/plugins/` 放 `*.py`(下划线开头的忽略),每个文件提供 `register(registry, settings)`:

```python
from storyagent.media.contracts import AdapterBase, Capabilities
from storyagent.domain import MediaKind

class MyAdapter(AdapterBase):
    provider = "my-tool"
    def capabilities(self): return Capabilities(kinds=(MediaKind.AUDIO_MUSIC,))
    def submit(self, job, ctx): ...      # 开始工作,返回外部任务号
    def poll(self, external_id, job, ctx): ...     # Progress("running"|"succeeded"|"failed", ...)
    def fetch(self, external_id, job, ctx): ...    # [ArtifactSpec(path)]

def register(registry, settings):
    registry.register(MyAdapter())
```

模型里写 `provider = "my-tool"` 即可使用。接口约定见 `docs/MEDIA_CONTRACT.md` 和 `storyagent/media/contracts.py`;
`media/adapters/` 里的现成适配器可以照着写。载入失败(语法错误、没有 `register`、抛异常、`provider` 名已被占用)的插件会被跳过,
并在健康检查里列为 `plugin.<名>`,不会影响系统启动。**插件是你自己的代码,以服务和 worker 的权限运行,只放你写的或读过的文件。**

## 现成示例

`docs/examples/connectors/`:兼容 OpenAI 的出图与语音、AUTOMATIC1111/Forge、异步音乐服务的写法、Piper、ffmpeg 响度归一。
它们是起点,接口细节以你装的版本为准。

