# 历史导入与可验证统计

本工具只处理用户提供的本地文件，不读取 ChatGPT / Codex 账户数据库，也不能证明导出包含 chat/work 的全部记录。当前没有真实导出；测试数据完全合成，不是用户行为证据。

## 使用

从 `/workspace/ai-` 运行，Python 3.11+，仅标准库，无需网络或 API key：

```bash
python3 -m audit.history private/conversations.json --output private/history-analysis.json
python3 -m audit.history private/export.zip --output private/history-analysis.json
python3 -m audit.history private/personal.zip private/work.zip --output private/history-analysis.json --private-review private/question-review.json
python3 -m audit.history private/messages.jsonl --as-of 2026-10-31T22:00:00+13:00
python3 -m unittest discover -s tests -p 'test_history.py' -v
```

原始导出与详细结果均留在 Git 忽略的 `private/`。默认统计输出不包含正文、标题、原始对话或消息 ID，但日期、主题、哈希索引仍可能敏感；应先审阅再发布汇总图表。不要将导出改名到仓库其他路径后提交。

可选 `--private-review private/question-review.json` 单独生成原始提问片段供真正发现主题，绝不混入统计输出。它强制输出位于当前工作目录的 `private/` 内，拒绝符号链接逃逸、覆盖源文件或与统计输出共用路径，文件权限为 `0600`。从仓库根目录运行即可得到 Git 忽略保护。

私密审阅默认每个“主类别 × 最近90天/更早/无有效日期”层最多取100条，按消息哈希确定性抽样，避免旧数据或数量最多的类别淹没其他类别；每条最多2000字符。样本不足时包含所有问题。文件明确给出总体数、样本数、各层覆盖与截断标志。不能把样本当作全部主题或漏掉的任务不存在的证明；可以直接针对原始导出继续审阅。它只保留提问正文，不保留助手回复或原始ID。

## 输入覆盖

- ChatGPT JSON：顶层会话数组，或 `{"conversations": [...]}`；每个会话需要 `id` / `conversation_id` 及 `mapping`。
- ZIP：只读取唯一 `conversations.json`，可在子目录，无文件解压；拒绝路径穿越、绝对路径、重复候选、加密文件与超过 256 MiB 的会话文件。ZIP 本体最多 512 MiB、最多 10,000 个条目；JSON/JSONL 文件最多 256 MiB。输出不能覆盖输入文件。
- JSONL：每行一个消息对象，字段为 `conversation_id`、`message_id`、`role`、`text`，可选 `timestamp`。ID 必须非空字符串。时间使用带时区 ISO 8601 或 Unix 秒；无时区时间不猜测时区。
- 导出需分别从拥有相应历史的账户/工作区取得；有的工作区不开放导出或不含全部模式。可以直接把多个 JSON/ZIP/JSONL 文件一起传给 CLI，无需手动整理或合并。最多100个源文件，一次解析一个源的正文。文件名与本地路径不进入统计输出，各源用序号标记。
- 跨来源重复消息按 conversation_id + message_id 去重，证据索引保留所有 `source_indexes`。同 ID 但正文或时间戳不同的版本保留第一个，并计入 `quality.conflicting_duplicate_messages` 供后续排查；工具不自动推断某个工作区覆盖完整，也不把文件数量当成已覆盖的 chat/work 模式数量。若自制 JSONL 重用简单 ID，应在生成时给不同来源加前缀。
- 不支持未知 HTML/PDF/截图格式，不会把无法解析的输入视作零历史。

## 统计定义

1. 只计 `role=user`。系统、助手、工具消息不进入提问数量。图像/音频无文字消息计入消息数量但不推断内容。
2. 有 `current_node` 时沿 parent 链计当前可见分支，排除改写前的分支；链损坏报错。缺少该指针时计所有节点，质量报告明确提示分支可能造成膨胀。
3. 以会话 ID + 消息 ID 去重；相同时间和相同文字、但 ID 不同的实际消息不擅自删掉。不同导出 IDs 的碰撞需在合并时处理。
4. 时区统一为 `Pacific/Auckland`，正确考虑夏令时；日按当地日期，周按 ISO 周。会话间隔按 UTC 实际经过时间计算。
5. 全历史窗口：第一条有效提问的本地日期到 `as_of` 当日，含中间零提问日。`last_date` 单列，不能把最后提问日当窗口终点而夸大频率。
6. 最近 90 天：含 `as_of` 当日的 90 个日历日，与第一条有效记录取交集；更早窗口为第一条记录到第 91 天前。分别给出每日历周率，不能直接比较不同长度窗口的总数。
7. 时间戳缺失、非法或晚于 `as_of` 的消息保留在总提问数与分类数，排除于日期、窗口、时序与会话指标。
8. 20 分钟内同一对话消息算一段“推断交互会话”。这不等于真实任务数量，更不能乘以 20 分钟当作耗时。分类内会话单独计数，相加可能超过全局会话数。
9. 关键词分类只是中立的初筛框架，不表示用户有这些兴趣。目前覆盖 SEC 高管交易、Powershop、投资事件、其他财务、软件、职业、教育、购物、旅行、健康、家庭能源、行政法律、效率规划、饮食、文娱、人际、时事，共17个主题加 `uncategorized`。普通电费归家庭能源，不能自动推断是 Powershop；`sec` 表示秒的普通语句不归入 SEC 高管主题，`form4` 可识别。类别粗糙、有重叠、可能误分，尤其不能将“旅行问题”直接当成“机票提醒”这一可自动化工作流。未分类内容与私密原文仍需审阅，再依证据发现此前未知的机会。
10. 多标签允许交叉。`primary_*` 按规则次序只归一类、总数守恒；`multilabel_*` 会重复，不能相加制造总工作量。SEC/Powershop 已有自动化的查询也不能当作新收益。

## API 输出契约（schema_version=1）

`analyze_history(Path(...), as_of=datetime(...)) -> dict` 保持单源兼容；多源 API 为 `analyze_histories([Path(...), ...], as_of=None, *, private_review_path=None) -> dict`。`as_of` 需带时区；省略为当前 UTC 时间。私密审阅路径限制与 CLI 相同。没有输入文件属于错误，不等于空历史。

| 字段 | 含义 |
|---|---|
| `status` | `analyzed` / `no_user_messages` / `no_dated_user_messages`；后两者不能声称真实历史分析完成 |
| `source` | 输入格式（多源为 `multiple_exports`）、`files_read`、格式数量 `formats`、读取会话数、历史覆盖未知、不含原文；`exports` 为各源序号/格式/读入会话数/读入用户记录数/新增唯一数/跳过重复数 |
| `quality` | 去重、分支排除、缺失/非法/未来日期、无文字与分支指针缺失数量 |
| `counts` | 去重用户消息、有效日期用户消息、含用户消息会话数 |
| `window` | `first_date`、`last_date`、`as_of_date`、`calendar_days`、`zero_message_days` |
| `metrics` | `daily_counts` / `weekly_counts`、`max_day` / `max_week`、活跃日/周、每日历周率、推断会话数 |
| `categories` | 每类 `primary_user_messages`、`primary_dated_messages`、`multilabel_user_messages`、`inferred_sessions`、每日历周率 |
| `recent_90_days` / `older` | 日期起止、日历天数、消息数、每日历周率 |
| `evidence_index` | SHA-256 前 24 位的会话/消息引用、本地时间、分类、包含该消息的 `source_indexes`；不含原文或原始 ID |
| `private_review`（可选） | 仅报告私密文件已写、样本数量和抽样方法；不会把原文嵌入统计结果 |
| `limitations` | 不能视作完整账户覆盖、时间耗用、任务身份或节省时间证明的明确说明 |

频率是导出中可观察事实；节省时间还需要单次人工用时、已有自动化覆盖、通知审核和维护开销。后续收益模型必须把这些参数标为测量值或假设，不能把请求次数直接当作真实任务次数。
