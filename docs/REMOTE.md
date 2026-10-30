# 远程访问(手机 / iPad / 外出的电脑)

目标:人在外面,用浏览器登录这台 Mac 上的系统,下任务、看结果、审片。**重计算(出图、出视频、合成)仍在这台 Mac 上跑**,远程只是一个网页入口。

做法:账号登录(`[auth] mode = "token"`) + Tailscale(组网)+ `tailscale serve`(把本机的 HTTP 服务套成 tailnet 内的 HTTPS)。服务本身仍只监听本机回环地址,**不暴露在局域网,也不上公网**。

## 现状(2026-10-30,参考机)

| 项 | 状态 |
|---|---|
| 账号登录 | 已开。`data/config/auth.toml`:`[auth] mode = "token"`;管理员账号 `admin` |
| 服务监听 | `127.0.0.1:8765`(未改,`services.toml` 的 `[api]`) |
| Tailscale | Tailscale.app,tailnet `tailbc7386.ts.net`;设备:MacBook Pro、iPad、iPhone、Mac mini |
| HTTPS 入口 | 已设:`https://pengzhanmacbook-pro.tailbc7386.ts.net/`,仅 tailnet 内可见 |
| 在本机验证 | 页面、JS/CSS、翻译目录返回 200;不登录访问 `/api/projects` 返回 401;`/api/auth/mode` 返回 `token` |
| 手机登录 | 2026-10-30 用户确认手机可以登录使用(首次出现过「意外中止」,按「排障」处理后恢复) |
| 未做 | 关闭自助注册(`allow_registration`,默认开,见「收紧」) |

## 从零配置(新机器或重来)

1. **打开账号登录**。`$STORY_AGENT_HOME/config/auth.toml`:

   ```toml
   [auth]
   mode = "token"
   ```

   或环境变量 `STORY_AGENT_AUTH=token`。重启 `storyagent serve`(或用 `scripts/start.sh` 的热重启)。

2. **建第一个管理员**(在服务器上;要输入密码,或 `--password-env VAR`):

   ```bash
   .venv/bin/python -m storyagent user create 名字 --role admin
   .venv/bin/python -m storyagent user list
   .venv/bin/python -m storyagent user set 名字 --reset-password     # 忘记密码
   ```

   其他人在网页「团队」页建,或 CLI `user create NAME --role editor`。角色:admin / producer / editor / viewer。

3. **装 Tailscale,并让所有要访问的设备登录同一个账号**。手机/iPad 装 Tailscale App 并连上。

4. **在 tailnet 管理后台启用 HTTPS**:https://login.tailscale.com/admin/dns 里打开 MagicDNS 和 HTTPS Certificates。第一次执行 `serve` 时,若 tailnet 没启用 Serve,命令会打印一个 `login.tailscale.com/f/serve?...` 链接,点开授权后重跑一次。

5. **把本机服务转成 HTTPS**(Tailscale.app 自带命令行,不在 PATH 里):

   ```bash
   TS=/Applications/Tailscale.app/Contents/MacOS/Tailscale
   $TS serve --bg --https=443 http://127.0.0.1:8765     # 开(持久,重启后仍在)
   $TS serve status                                      # 查看
   $TS serve --https=443 off                             # 关
   $TS serve reset                                       # 清掉全部 serve 配置
   ```

   命令会打印访问地址 `https://<本机名>.<tailnet名>.ts.net/`。**只用 `serve`(仅 tailnet),不要用 `funnel`(那是公网)。**

6. **启动系统**(不变):`scripts/start.sh`(worker + server;退出码 75 = 两个一起重启)。这台 Mac 要保持开机、不睡眠(`[runtime] caffeinate = true` 默认已开)。

## 使用

手机/iPad:先打开 Tailscale App 确认已连接 → 用 **Safari**(不要用微信/QQ 等内置浏览器)打开地址 → 用账号登录。

## 为什么必须有 HTTPS

登录会话 cookie 只在请求走 https 时才带 `Secure` 标记(`api/routes_auth.py`)。服务默认只监听回环地址,想让它直接监听别的地址要改 `[api] host`,且前面必须有 HTTPS 反向代理(RUNBOOK 也这么要求)。`tailscale serve` 在本机转发并提供证书,服务配置一个字都不用改。

## 收紧(只有你一个人用时建议做)

- **关自助注册**:`auth.toml` 里加 `allow_registration = false`。现在登录页允许 tailnet 里的人自己注册(新账号是 `viewer`、年龄未审批,只能看没有年龄限制的内容)。改完要重启 `serve`。
- **管理员密码要强**。`/api/update`、`/api/config`、`/api/launch`、`/api/setup`、`/api/maintain` 等只有管理员能用(`auth.toml` 的 `admin_prefixes`),远程管理员能触发「更新并重启」和启动服务。
- 受限内容(restricted)在账号模式下需要账号 + 密码再次确认才解锁,并写审计;远程时同样适用。
- 所有写操作和登录事件都在审计里(网页「团队 > 审计」或 `storyagent audit`)。
- 不要开 `funnel`;不要把 `[api] host` 改成 `0.0.0.0`。

## 排障

| 现象 | 原因 / 办法 |
|---|---|
| 手机提示「意外中止」/ 打不开 / 一直转圈 | 多半手机没走进 tailnet:先开 Tailscale App 看开关和连接状态(iOS 会在后台断);用 Safari;App 设置里 "Use Tailscale DNS settings" 要开(域名靠它解析);关掉别的 VPN/代理;蜂窝网络不行就换 Wi-Fi 对比 |
| 证书/安全警告 | tailnet 里没启用 HTTPS Certificates(步骤 4);或 `serve` 刚开,证书几秒后才就绪,重试 |
| 看到的是 `login.tailscale.com` 的 "Start using Serve" 页 | 那是 Tailscale 的授权页,不是系统;看到 "Serve is ready to use" 说明授权完成,改打开系统地址 |
| 能看到登录页但登不进 | 看提示;忘记密码:`user set NAME --reset-password`;连错 5 次锁 10 分钟(`login_max_failures` / `lockout_minutes`) |
| 本机检查是否通 | `curl https://<地址>/api/auth/mode` 应返回 `{"mode":"token",...}`;`curl -o /dev/null -w '%{http_code}' https://<地址>/api/projects` 未登录应是 401 |
| Claude 内置浏览器窗格里是空白页 | 窗格拦截了页面的脚本/样式请求(`ERR_BLOCKED_BY_CLIENT`),只放行页面本身;用自己的 Safari/Chrome |
| `tailscale` 命令找不到 | 它不在 PATH,用 `/Applications/Tailscale.app/Contents/MacOS/Tailscale` |

## 与别的文档的关系

- 账号、角色、审计、受限内容:`docs/RUNBOOK.md`「Team access and audit」「Restricted content」。
- `[auth]` / `[api]` 各项:`docs/CONFIGURATION.md`(`auth.toml`、`services.toml` 行)。
- 在网页里更新并重启:`docs/RUNBOOK.md`「生产部署与更新」(远程管理员可用;要 `scripts/start.sh` 守护)。
