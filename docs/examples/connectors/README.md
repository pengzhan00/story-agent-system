# 连接器示例

每个示例是一对文件:`<名>.models.toml` 追加到 `$STORY_AGENT_HOME/config/models.toml`,`<名>.services.toml`(只有 HTTP 服务才有)
追加到 `services.toml`。它们是**起点**:接口细节(路径、字段名、参数范围)以你装的版本为准,先对着工具的文档核一遍;
许可字段(`license_tag`、`commercial_ok`)留空/`false`,读过许可再填。说明见 [../../CONNECTORS.md](../../CONNECTORS.md)。

| 示例 | 类型 | 做什么 |
|---|---|---|
| `openai-images` | http,同步 | 兼容 OpenAI 的 `/v1/images/generations` 服务(本地的 LocalAI、各类网关…)出图,答案里是 base64 |
| `openai-speech` | http,同步 | 兼容 OpenAI 的 `/v1/audio/speech` 服务合成语音,答案本身就是音频文件 |
| `a1111-forge` | http,同步 | AUTOMATIC1111 / Forge 的 `/sdapi/v1/txt2img` 出图 |
| `async-music` | http,异步 | 提交任务 → 轮询状态 → 下载结果的一类音乐/视频服务的写法模板 |
| `piper-tts` | cli | Piper 命令行合成语音,文本走标准输入 |
| `ffmpeg-loudnorm` | cli | 用 ffmpeg 把一段音频响度归一(`audio.enhance`),演示参考文件 `{ref}` |
