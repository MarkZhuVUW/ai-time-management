# 历史导入与可验证统计

本工具只处理用户提供的本地文件，不读取 ChatGPT / Codex 账户数据库，也不能证明导出包含 chat/work 的全部记录。当前没有真实导出；测试数据完全合成，不是用户行为证据。

## 使用

从 `/workspace/ai-` 运行，Python 3.11+，仅标准库，无需网络或 API key：

```bash
python3 -m audit.history private/conversations.json --output private/history-analysis.json
python3 -m audit.history private/export.zip --output private/history-analysis.json
python3 -m audit.history private/messages.jsonl --as-of 2026-10-31T22:00:00+13:00
python3 -m unittest discover -s tests -p 'test_history.py' -v
```

原始导出与详细结果均留在 Git 忽略的 `private/`。默认输出不包含正文、标题、原始对话或消息 ID，但日期、主题、哈希索引仍可能敏感；应先审阅再发布汇总图表。不要将导出改名到仓库其他路径后提交。

## 输入覆盖

- ChatGPT JSON：顶层会话数组，或 `{"conversations": [...]}`；每个会话需要 `id` / `conversation_id` 及 `mapping`。
- ZIP：只读取唯一 `conversations.json`，可在子目录，无文件解压；拒绝路径穿越、绝对路径、重复候选、加密文件与超过 256 MiB 的会话文件。ZIP 本体最多 512 MiB、最多 10,000 个条目；JSON/JSONL 文件最多 256 MiB。输出不能覆盖输入文件。
- JSONL：每行一个消息对象，字段为 `conversation_id`、`message_id`、`role`、`text`，可选 `timestamp`。ID 必须非空字符串。时间使用带时区 ISO 8601 或 Unix 秒；无时区时间不猜测时区。
- 导出需分别从拥有相应历史的账户/工作区取得；有的工作区不开放导出或不含全部模式。多个来源可先规范成 JSONL，并给不同工作区的 conversation_id 加来源前缀，避免碰撞。当前 CLI 一次读一个文件，不自动合并多账户 ZIP。
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
9. 关键词分类只是初筛。目前覆盖 SEC/Powershop/投资事件/其他财务；其他主题保留 `uncategorized`，等待实际历史后扩展规则或人工审阅。不能凭这份初筛宣布已归纳用户全部兴趣。
10. 多标签允许交叉。`primary_*` 按规则次序只归一类、总数守恒；`multilabel_*` 会重复，不能相加制造总工作量。SEC/Powershop 已有自动化的查询也不能当作新收益。

## API 输出契约（schema_version=1）

`analyze_history(Path(...), as_of=datetime(...)) -> dict`，`as_of` 需带时区；省略为当前 UTC 时间。

| 字段 | 含义 |
|---|---|
| `status` | `analyzed` / `no_user_messages` / `no_dated_user_messages`；后两者不能声称真实历史分析完成 |
| `source` | 输入格式、读取会话数、历史覆盖未知、不含原文 |
| `quality` | 去重、分支排除、缺失/非法/未来日期、无文字与分支指针缺失数量 |
| `counts` | 去重用户消息、有效日期用户消息、含用户消息会话数 |
| `window` | `first_date`、`last_date`、`as_of_date`、`calendar_days`、`zero_message_days` |
| `metrics` | `daily_counts` / `weekly_counts`、`max_day` / `max_week`、活跃日/周、每日历周率、推断会话数 |
| `categories` | 每类 `primary_user_messages`、`primary_dated_messages`、`multilabel_user_messages`、`inferred_sessions`、每日历周率 |
| `recent_90_days` / `older` | 日期起止、日历天数、消息数、每日历周率 |
| `evidence_index` | SHA-256 前 24 位的会话/消息引用、本地时间、分类；不含原文或原始 ID |
| `limitations` | 不能视作完整账户覆盖、时间耗用、任务身份或节省时间证明的明确说明 |

频率是导出中可观察事实；节省时间还需要单次人工用时、已有自动化覆盖、通知审核和维护开销。后续收益模型必须把这些参数标为测量值或假设，不能把请求次数直接当作真实任务次数。
