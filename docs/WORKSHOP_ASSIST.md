# 工坊 AI 助手:每个模块一个会查 wiki 的设计助手

> **状态:已实现(2026-10-30)。** 在工坊的每个模块(故事、剧本、图片、歌曲、配音、视频……)表单上方有一个折叠的「AI 助手」卡片。
> 你写下这个模块**长期的要求**(每个模块各一份)和**这一次的要求**,助手读 wiki 里和所选模型相关的条目,给出一份**提案**:
> 提示词、歌词、文本字段,以及模型参数的取值。提案先展示,勾选后一键填入表单;**它不会自己提交作业,也不会保存任何东西**。

## 怎么用

1. 打开工坊的某个模块,展开「AI 助手」。
2. 「这个模块的长期要求」:写给这个模块的固定交代,比如歌曲模块写"歌词用中文,每段 4 句,风格标签不要用吉他",图片模块写"写实摄影,不要文字水印"。点「保存要求」;每个模块独立保存,以后每次都会带上。
3. 「这一次的要求」:写这次想要什么。可以空着(只按已有表单内容和长期要求优化)。
4. 勾选「参考 wiki」(默认勾选)时,系统用所选模型、作品类型、模块名和你的要求去 wiki 检索,把最相关的几段交给助手。
5. 点「让 AI 设计」。结果里每个字段、每个参数都有勾选框,旁边是"现在"和"建议"的对比;wiki 来源列在下面,正文里引用的带「引用」标记,点标题就是 wiki 页。
6. 点「填入表单」,只有勾选的项会写进表单。再改、再试都是普通的表单操作。

## 助手能改什么、不能改什么

| 能改 | 不能改 |
|---|---|
| 文本字段(`text`、`longtext`)和作品类型(`workkind`),如提示词、歌词、故事前提 | 媒体输入(图片、音频、视频、分镜列表):这些是文件引用,不是助手能"写"的东西 |
| 所选模型适配器声明过的参数(`param_schema`),取值落在类型/范围/枚举之内 | 适配器没有声明的参数、超出范围的值、类型不对的值 |

不合格的建议**不会被丢掉而不告诉你**:它们列在「已忽略」里,写明原因(不能填的字段、未知参数、取值不合法、未知作品类型)。

提示词按所选模型的 `prompt_profile` 写成它吃的风格(标签式、自然语言长句、对白式等),这也是助手要读 wiki 的原因:模型的写法要点在 wiki 笔记里。

## 要求怎么存

表 `workshop_assist`(迁移 027):`module`(主键)、`requirements`(最长 4000 字)、`updated_at`、`updated_by`。保存空文本等于删除。
长期要求是**配置性的数据**,不是代码;它进入助手的系统提示里的 `requirements` 变量,和"这一次的要求"分开,方便以后只改其中一个。

## 受限内容

wiki 里受限分级的页面只有在**已解锁并有权查看**时才会交给助手;一旦用到了更高分级的页面,这次调用就走该分级自己的模型(和 wiki 问答、其他受限流程一样),模型调用日志里只留摘要。

## 接口

| 接口 | 说明 |
|---|---|
| `GET /api/workshop/assist/{module}` | `{module, requirements, updated_at, updated_by}`;从未保存过则 `requirements` 为空 |
| `PUT /api/workshop/assist/{module}` `{requirements}` | 保存(记审计人);超过 4000 字 422 `assist_too_long`;未知模块 422 `workshop_unknown_module` |
| `POST /api/workshop/assist` `{module, kind, model_ref, tier, inputs, params, request, use_wiki, language}` | 返回 `{inputs:[{name,value,current}], params:[{name,value,current}], dropped:[…], notes, sources:[…], backend, model, seconds}`;模型与种类不匹配 422 `workshop_wrong_model`,未知种类 422 `workshop_unknown_kind`;模型不可用 502 `llm_unreachable` / `llm_model_missing` / `llm_failed` |

## 配置与扩展

- 提示词是 `agents/specs/workshop.assist.toml`(角色 `prompter`,可在「AI 提示词」页改),输出结构是 `schemas/workshop_assist.json`。
- 助手用的模型是 `llm.toml` 里 `prompter` 角色的模型;换模型只改这里。
- 新增工坊模块或字段不用改助手:可填字段来自模块的字段表,参数来自适配器的 `param_schema`。

## i18n

界面文案 `web.workshop.assist.*`,提示词变量 `agent.var.*`,智能体说明 `agent.workshop.assist.*`,错误 `api.error.workshop_unknown_module`、`api.error.assist_too_long`(各带 `.remediation`),都在 `locales/{zh-CN,en}.json`。

## 还没做

- 没有用真实本地模型做过完整走查(测试用的是假 LLM 服务,检查的是提示组成、过滤和回填逻辑);实际效果取决于 prompter 模型和 wiki 里的笔记质量。
- 提案不记录历史;需要追溯时看「AI 调用」日志。
