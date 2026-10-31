# 个人自动化机会审计

目标：从真实历史提问中找出反复投入时间的工作，设计 App 或 ChatGPT 定期任务，把“需要再问一次”变成“只有值得处理时才通知”。

## 当前边界

- 默认分析全部历史，最近 90 天作为趋势对照；时区 `Pacific/Auckland`。
- 当前可见证据只有本线程；不能读取账户其他 Codex / ChatGPT chat / work 历史。
- 已确认现有自动化：Discord SEC 高管买卖机器人；Powershop 每周二自动买特价包。它们是已有基准，不能把已有收益算成新项目收益。
- 没有历史数据之前，不把示例频率、假设耗时或候选项目写成实际行为。
- 原始导出、对话正文、账户资料放在被 Git 忽略的 `private/`；只提交经检查的统计汇总。
- 本任务不创建 Git worktree；使用现有 `/workspace/ai-`。

## 恢复任务

先读 [进度](PROGRESS.md) 和 [证据](data/evidence.json)，再继续未完成事项。每个实质阶段更新进度并保存 Git 提交；远程同步结果必须如实记录。

## 交付范围

1. 历史导入与本地统计：真实频率、按周分布、重复工作会话、最近 90 天趋势。
2. 时间模型：区分观测次数与假设分钟数，显示维护、审核和异常处理成本。
3. 可执行方案：App 与 ChatGPT 定期任务二选一，优先复用现有机器人。
4. 可分享的图表与报告；没有数据时明确空缺，不制造柱状图“实绩”。

状态：历史源尚未提供；可独立完成分析工具与候选方案，真实全历史结论等待数据。

## 使用与已生成成果

Python 3.11+。导入器、统计和收益计算只依赖标准库；图表使用 `requirements-report.txt` 中的 Matplotlib（当前环境已安装并验证）。

```bash
cd /workspace/ai-
python3 -m unittest discover -s tests -v
python3 -m audit.report
# 取得真实导出后再执行；原文和逐条索引保持私有。
python3 -m audit.history private/conversations.json --output private/history-analysis.json
# 多个账户/工作区无需手工合并；用于语义审阅的原文片段仍只写到 private/。
python3 -m audit.history private/personal.zip private/work.zip --output private/history-analysis.json --private-review private/question-review.json
python3 -m audit.report --history private/history-analysis.json
```

- [阶段报告](reports/REPORT.zh-CN.md)：真实证据、未完成部分与候选方案。
- [情景柱状图](reports/scenario-savings.png)：明确标记为假设，并非个人实测时间。
- [离线收益计算器](reports/calculator.html)：修改次数、审核、维护和建设成本，观察是否值得做。
- [完整候选设计](docs/automation-designs.md)：App / 定期任务选择、触发条件、依赖和验收。
- [导入说明](docs/history-import.md)：格式、覆盖、去重和频率定义。
- [全历史审计方法](docs/methodology.md)：发现此前未知的工作流，区分研究兴趣与重复劳动。

不要将未知历史写成零历史，不要将关键词主题当作低效工作，不要将多轮追问算成多个可替代任务。公开汇总默认不包含每日时间线和逐条证据索引。新增真实汇总也应在推送前检查。
