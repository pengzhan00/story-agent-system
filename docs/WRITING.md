# 编剧提示词:作品形式与题材

> **状态:已实现(2026-10-30)。** 解决的问题:以前所有写作环节的系统提示都写死了同一句"竖屏短剧:节奏快、强反转、每幕有爽点",
> 单本短剧、电视剧、长片、广告、慢热爱情都被写成同一种节奏。现在**形式(作品类型)和题材的写法是配置,不是提示词里的字**。

## 谁决定怎么写

| 层 | 在哪里 | 决定什么 |
|---|---|---|
| 作品类型(形式) | `kinds.toml` 的 `writing`、`dialogue_chars`、`[kinds.X.shot_sec]` | 节奏与结构(开场、转折、结尾怎么写)、台词密度、单句台词上限、镜头时长范围 |
| 题材 | `genres.toml` | 这类故事的驱动力、反转方式、台词风格、要避免的套路 |
| 画幅 | 项目的画幅(竖屏/横屏) | 构图提示(竖屏:一次一个主体、人脸大;横屏:可用宽度铺场景) |
| 提示词本体 | `storyagent/agents/specs/*.toml` | 只写"怎么做事",**不写**任何一种形式的风格 |

写作环节(季规划 `screenwriter.season`、大纲 `screenwriter.outline`、改编 `screenwriter.adapt`、分镜 `screenwriter.shots`、
工坊故事/剧本 `workshop.story` / `workshop.script`、剧本评审 `reviewer.script`)在提示词里引用这些变量:

| 变量 | 内容 |
|---|---|
| `$form_guidance` | 该作品类型的 `writing`;没有类型规则时是 `(none)` |
| `$genre_guidance` | 项目题材文本命中的题材注释,最多 2 条;没有命中是 `(none)` |
| `$dialogue_chars` | 单句台词上限(字数) |
| `$shot_sec` | 一句话的镜头时长要求,如"between 2 and 8 seconds, usually about 4" |
| `$frame_note` | 画幅提示;没有项目(工坊)时是 `(none)` |

工作流里这些变量由 `Ctx.run_agent` 自动带上(`workflow/stages.py`),评审由 `workflow/engine.py` 带上,工坊文字作业由
`media/adapters/llm.py` 带上(可选输入 `kind`,不填就没有形式规则,只有题材注释)。变量的查找与措辞在
`storyagent/agents/craft.py`。

## 题材怎么选中

项目的"题材"是自由文本(创作者填的,或故事分析给出的),`genres.toml` 里每个题材有一串 `aliases`,**中英文都可以**,
不区分大小写、按子串匹配;文本里出现哪个别名就带上哪个题材的注释,最多 `max_matches`(默认 2)条,按文件顺序。
"古装 复仇"会同时带上古装和复仇两条。故事分析(`director.analyze`)被要求用常见词命名题材(romance、mystery、thriller、
historical、comedy、workplace、campus、science fiction/fantasy、family、revenge、horror),所以模型给出的题材也能命中。

自带 11 个:`romance`(言情/爱情)、`mystery`(悬疑/推理)、`thriller`(惊悚/犯罪/动作)、`historical`(古装/宫廷/武侠)、
`comedy`(喜剧)、`workplace`(职场/商战)、`campus`(校园/青春)、`scifi_fantasy`(科幻/奇幻/穿越)、`family`(家庭/伦理)、
`revenge`(复仇/逆袭/爽文)、`horror`(恐怖/灵异)。

## 自带的作品类型

| 类型 | 写法要点 | 台词上限 | 镜头时长(最短/常用/最长) |
|---|---|---|---|
| `drama_series` 连续短剧 | 第一拍就是钩子;每场都改变点什么;每幕至少转折一次;每集结尾留悬念 | 25 字 | 2 / 4 / 8 秒 |
| `drama_single` 单本短剧 | 钩子、一条递进的冲突、一次反转、结尾落地;不为下一集留尾巴 | 30 字 | 2 / 4 / 8 秒 |
| `tv_series` 电视剧 | A/B 故事交叉;冷开场抛问题,中点加复杂度,结尾回答并推动全季主线;场景可以长、有潜台词 | 45 字 | 3 / 6 / 12 秒 |
| `advert` 广告 | 一条信息;开场记忆点,产品在小故事里起作用,收在品牌与行动号召;不超过两个说话人 | 20 字 | 1 / 3 / 6 秒 |
| `feature_film` 长片 | 三幕;场景有呼吸感,台词自然可重叠,靠潜台词、沉默和画面;主题反复出现 | 60 字 | 3 / 8 / 15 秒 |
| `custom` 自定义 | 不强加风格,按创意、题材和立项说明走 | 40 字 | 2 / 5 / 10 秒 |

## 与下游模型对得上

写作规则不能写出下游做不了的东西,所以几个数字是**同一份配置**里联动的:

- **镜头时长**:`shot_sec` 同时是(1)写作环节被要求的时长,(2)镜头计划(`shotplan`)里每个镜头被夹住的范围,
  (3)按目标时长算幕数/场数/镜头数时的"平均镜长"(取 `default`)。一次运行仍可用参数 `min_shot_sec` / `max_shot_sec` 覆盖。
  上限要在计划里的视频模型一段能渲染、或静帧加运镜能撑住的范围内(LTX 一段约 5 秒;视频只占一部分镜头,其余是静帧)。
- **台词上限**:配音按约每秒 4.5 个字估时(`estimate_speech_sec`),一句最长的台词要能放进最长的镜头。
  `tests/test_craft.py` 对每个类型检查这一点,改数字时会被拦住。
- **输出结构**:提示词只增加纯文本变量,不改 JSON 模式(`agents/specs/schemas/*.json`),所以本地模型的结构化输出要求不变。
  新增变量约 150–300 个词的提示,在本地模型的上下文(32k)里余量很大。
- **语言**:提示词本身是英文,故事内容按项目的内容语言写(`$language`);题材别名中文/英文都能命中。

## 怎么改

- 改某个类型的节奏:`$STORY_AGENT_HOME/config/kinds.toml` 里覆盖那个类型的 `writing`(改一段英文即可)。
- 加题材:`$STORY_AGENT_HOME/config/genres.toml`,`[genres.my_genre]` + `aliases` + `guidance`;不用改代码。
- 加作品类型:`kinds.toml` 里加 `[kinds.X]`,同时写 `writing`、`dialogue_chars`、`[kinds.X.shot_sec]`;
  不写这三项时用默认(无形式规则、25 字、2/4/6 秒)。
- 改某个写作环节自己的提示词:`storyagent/agents/specs/<name>.toml`,或在项目里按环节覆盖(改了立即生效,长驻进程也不用重启)。
- 配置有错(别名为空、注释为空、`dialogue_chars` 太小、`shot_sec` 不合法)会在加载时被拒绝并说明原因。
