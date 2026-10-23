# 知识库(Wiki)

系统内嵌的知识库:把仓库自己的文档和你 Obsidian 里的笔记放进同一个可搜索、可互相跳转的页面(网页 **知识库**,命令行 `storyagent wiki ...`)。
笔记留在原处,系统只读取、从不修改;页面是复制进数据库的一份(`wiki_pages`),随时可以重新同步。

## 来源

| 来源 | 内容 | 开关 |
|---|---|---|
| `docs` | 仓库的 `docs/**/*.md` | `[wiki] docs = true`(默认) |
| 自己的笔记文件夹 | 例如 Obsidian vault | `$STORY_AGENT_HOME/config/wiki.toml` 的 `[[wiki.sources]]` |

```toml
[wiki]
docs = true

[[wiki.sources]]
name = "obsidian"                       # 页面来源名,不能含 "/",不能叫 docs
path = "~/Documents/Obsidian Vault"
include = [                             # 允许读取的清单:笔记路径或通配(* ? [abc]),相对 path
  "技能库/本机模型能力矩阵.md",
  "技能库/音视频*.md",
]
exclude = []
restricted = ["技能库/成人向*", "技能库/NSFW*"]   # 这些笔记按 rating 处理(默认 restricted)
rating = "restricted"
```

**只读 include 清单里的笔记**:清单为空就什么也不读(同步会提示),vault 里其余的笔记(工作、个人资料……)不会进入系统。
`.obsidian`、`.trash` 等隐藏目录不读;单个文件超过 2 MiB 跳过。

## 受限笔记

`restricted` 清单里的笔记带 `rating`(默认 `restricted`),和受限图片同一套门:

* 没解锁、或账号批准的年龄不够时,列表里看不到,搜索不出来,直接打开返回 403(`content_locked` / `age_restricted`);列表只告诉你"有 N 页被锁"。
* 解锁(顶栏)后可读,每次打开写一条审计记录。
* 受限页面里的图片不会被复制出来。

## Obsidian 语法

同步时转成普通 Markdown:`[[页面|别名]]`、`[[页面#标题]]`、`![[页面]]` 变成站内链接(找不到的目标留下纯文字);`![[图.png]]` 复制到
`$STORY_AGENT_HOME/wiki/assets/` 后以图片显示;`> [!tip] 标题` 变成带 **TIP** 的引用块;`%%注释%%` 去掉;`==高亮==` 变粗体;`^块标记` 去掉;
代码块和行内代码不动。frontmatter 的 `tags` 成为标签,`title` 作为标题(否则取第一个 `# 标题`,再否则取文件名)。
`docs` 里的相对 `.md` 链接也会变成站内链接。

## 用法

```bash
.venv/bin/python -m storyagent wiki sync                 # 读取来源,新增/更新/移除页面(幂等)
.venv/bin/python -m storyagent wiki list [-q 关键词] [--source obsidian] [--tag AI]
.venv/bin/python -m storyagent wiki show obsidian/技能库/本机模型能力矩阵
```

网页:**知识库** → 右上角"同步"按钮、搜索框(标题、标签、正文,中文可直接搜)、来源和标签过滤、本页目录、"哪些页面链接到这里"。
项目的**模型管线**面板里每个模型旁有"笔记"链接,直接搜这个模型的资料。

API:`GET /api/wiki/pages?q=&source=&tag=`、`GET /api/wiki/pages/{slug}`、`POST /api/wiki/sync`(需要写权限)、`GET /api/wiki/assets/{name}`。

## i18n

界面文字都是 `web.wiki.*` / `cli.wiki.*` 键;页面内容本身是你的笔记原文,不翻译。
