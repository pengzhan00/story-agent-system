# 云端视频生成(预留接口)

本机视频生成很慢(见 HANDOFF.md §6),以后算力可能放到云端。系统已为此留好接口:**换算力 = 改配置,不改代码**。

## 原则

- 云端模型只是 `models.toml` 里的一个条目:`provider = "remote_video"`、`remote = true`。工作流、工坊、规格、台账、成片完全不知道它在云端。
- **数据离开本机要显式许可**:`[runtime] allow_remote = true`(全局)或项目设置 `allow_remote`。没许可时作业以 `remote_not_allowed` 失败并说明怎么办。独立于合规开关(这是隐私,不是 AIGC 合规)。
- **密钥只从环境变量读**(`api_key_env` 写的是变量名),不进配置文件、数据库、日志。缺了 → `missing_credentials`。
- **不重复付费**:服务端的任务号就是作业的 `external_id`;worker 重启后继续轮询同一个任务(reattach),不会再提交一次。
- **不占本机重任务锁**:云端作业不算本机内存/GPU,可以和本机重任务并行;并发上限 `[runtime] max_remote_jobs`(默认 4)。
- 溯源照常记录(`provenance.remote = true`、服务名);资产元数据带任务号和估算费用(`price_per_second`)。
- 结果下载只对服务自己的域名带密钥,不把密钥发给结果链接指向的别处。

## 配置示例

`services.toml`(服务地址,默认不存在 = 不启用):

```toml
[services.cloudvideo]
url = "https://api.your-vendor.example"
health_path = "/"
enabled = true
probe_only = true
```

`models.toml`:

```toml
[models."cloud-video-a"]
kind = "video.generate"            # 也可以是 video.av / video.lipsync 等
provider = "remote_video"
remote = true
service = "cloudvideo"
api_key_env = "CLOUDVIDEO_API_KEY"  # 变量名;值只在 worker 的环境里
auth_header = "Authorization"
auth_prefix = "Bearer "
heavy = false
est_memory_gib = 0
price_per_second = 0.05             # 只用于估算,可省
currency = "USD"
timeout_sec = 3600
license_tag = "vendor-terms"        # 按厂商条款填写
commercial_ok = true
[models."cloud-video-a".params]
duration = 5
[models."cloud-video-a".tiers.draft]
duration = 3
[models."cloud-video-a".api]
submit_path = "/v1/videos"
submit_body = { model = "vendor-model-name", prompt = "${prompt}", image = "${image}", duration = "${duration}", seed = "${seed}" }
task_id_path = "data.task_id"        # 点号路径,数组用数字下标
status_path = "/v1/videos/${task_id}"
status_field = "data.status"
done_values = ["succeeded"]
failed_values = ["failed", "cancelled"]
progress_field = "data.progress"     # 可省;progress_scale = 100
result_url_field = "data.video_url"
error_field = "data.error"
cancel_path = "/v1/videos/${task_id}/cancel"   # 可省
image_mode = "data_url"              # data_url | base64 | none
```

把 `[defaults]` 里的 `"video.generate"` 指到它,或在工坊视频模块里直接选它。

## 厂商差异怎么办

- 请求体/字段名不同:改 TOML 里的 `submit_body` 和各 `*_field`。
- 需要先上传图片拿 URL、或多步握手:写一个很小的子类,覆盖 `RemoteVideoAdapter.build_body` / `submit` / `read_status`(`storyagent/media/adapters/remote_video.py`),在 `registry.py` 注册新的 provider 名。
- 同样的思路可用于云端图片/配音:新建同形的适配器,模型条目标 `remote = true` 即享受同一套许可、并发和溯源规则。

## 测试

`tests/test_remote_video.py` 用本地假服务验证:提交→轮询→下载、密钥不泄露给别的域名、没许可/没密钥时的失败、重启后接管同一任务、云端作业与本机重任务并行。
