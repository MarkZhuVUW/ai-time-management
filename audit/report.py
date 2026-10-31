"""Generate an honest, shareable audit. Raw conversations never enter the report."""

from __future__ import annotations

import argparse
import csv
import html
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from audit.economics import load_scenarios

ROOT = Path(__file__).resolve().parents[1]


def render_chart(candidates: list[dict], results: list[dict], out: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10,
                         "svg.fonttype": "none"})
    fig, ax = plt.subplots(figsize=(10.5, 5.2))
    fig.patch.set_facecolor("#f8fafc")
    ax.set_facecolor("#f8fafc")
    for index, (candidate, result) in enumerate(zip(candidates, results)):
        values = result["net_minutes_per_week"]
        ax.barh(index, values["central"], height=0.42, color="#2563eb")
        ax.plot([values["low"], values["high"]], [index, index], color="#0f172a", lw=1.4)
        ax.plot([values["low"], values["high"]], [index, index], "|", color="#0f172a", ms=9)
        ax.scatter([values["central"]], [index], color="#fff", edgecolors="#0f172a", s=36, zorder=3)
        ax.annotate(f" {values['central']:g} min/wk", (values["central"], index),
                    xytext=(0, 17), textcoords="offset points", color="#1e3a8a", fontsize=9)
    ax.set_yticks(range(len(candidates)), [c["name_chart"] for c in candidates])
    ax.invert_yaxis()
    ax.axvline(0, color="#64748b", linewidth=1)
    ax.set_xlabel("Incremental net minutes saved per week (assumed inputs)")
    ax.set_title("WHAT-IF SCENARIOS — NO HISTORICAL USER DATA LOADED", loc="left", pad=25, weight="bold", fontsize=12)
    ax.grid(axis="x", alpha=0.18)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    fig.text(0.015, 0.05, "Bars = central assumption; whiskers = low/high scenarios, NOT confidence intervals.", fontsize=9)
    fig.text(0.015, 0.015, "Existing SEC/Powershop savings excluded. Projects overlap: do not sum bars. Negative values mean extra work.", fontsize=8)
    fig.tight_layout(rect=(0, 0.1, 1, 1))
    fig.savefig(out / "scenario-savings.svg")
    fig.savefig(out / "scenario-savings.png", dpi=180)
    plt.close(fig)


def write_dashboard(candidates: list[dict], out: Path) -> None:
    # Candidate fields are from versioned local assumptions, serialized as data only.
    encoded = json.dumps(candidates, ensure_ascii=False).replace("<", "\\u003c")
    page = """<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>自动化收益：假设计算器</title>
<style>body{font:16px/1.7 system-ui,sans-serif;background:#f8fafc;color:#0f172a;max-width:1000px;margin:32px auto;padding:0 22px}h1{font-size:28px}.notice{border-left:5px solid #f59e0b;background:#fffbeb;padding:16px}section{background:white;border:1px solid #cbd5e1;border-radius:12px;padding:20px;margin:20px 0}label{display:block}input{width:90px;font:inherit;margin:5px}svg{width:100%;height:100px}small{color:#475569}output{font-weight:bold}button{font:inherit}</style>
<h1>哪些重复工作值得自动化？</h1>
<p class="notice">这是收益情景计算器，不是你的历史行为统计。默认数字全部是假设，不能当成已经节省的时间；接入真实历史和短期计时后再替换。现有 SEC / Powershop 收益不计入，项目之间可能重叠，不能相加。</p>
<p>净省时 = 每周增量人工核查次数 × 能替代的比例 ×（原耗时 − 审核耗时）− 维护 − 异常处理。提问消息数不能直接代替人工核查次数。</p>
<main id="cards"></main><p>请只输入仍在手工完成的动作；不用填写账户、代码或聊天内容。输入保存在当前页面内存中，刷新即恢复默认，不联网。</p>
<script>const candidates=__DATA__;
const fields=[['freq','仍需人工核查次数 / 周'],['manual','原来每次分钟'],['review','自动化后每次审核分钟'],['coverage','能替代的比例（0—1）'],['maintenance','每周维护分钟'],['exceptions','每周异常分钟'],['build','一次性建设分钟']];
for(const c of candidates){const s=document.createElement('section');const title=document.createElement('h2');title.textContent=c.name_zh;s.append(title);const defaults={freq:c.checks_per_week.central,manual:c.manual_minutes_per_check,review:c.review_minutes_per_check,coverage:c.automation_fraction.central,maintenance:c.maintenance_minutes_per_week,exceptions:c.exception_minutes_per_week,build:c.build_minutes.central};
const inputs={};for(const [key,label]of fields){const el=document.createElement('label');el.textContent=label+' ';const input=document.createElement('input');input.type='number';input.min=0;input.step='any';if(key==='coverage')input.max=1;input.value=defaults[key];el.append(input);s.append(el);inputs[key]=input;}
const output=document.createElement('output');output.setAttribute('aria-live','polite');s.append(output);const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 800 100');svg.setAttribute('role','img');svg.setAttribute('aria-label','净省时情景柱状图');s.append(svg);const gate=document.createElement('p');gate.textContent='验收前提：'+c.validation_gate;s.append(gate);
function update(){const v={};for(const [k,i]of Object.entries(inputs)){v[k]=i.value===''?NaN:Number(i.value);if(!Number.isFinite(v[k])||v[k]<0||(k==='coverage'&&v[k]>1)){output.textContent='请输入有效的非负数字，覆盖率在0到1之间。';svg.replaceChildren();return;}}
const net=v.freq*v.coverage*(v.manual-v.review)-v.maintenance-v.exceptions;const annual=(net*52-v.build)/60;output.textContent=`假设净省时 ${net.toFixed(1)} 分钟/周；首年扣除建设 ${annual.toFixed(1)} 小时；回本 ${net>0?(v.build/net).toFixed(1)+'周':'无法回本'}`;
svg.replaceChildren();const ns='http://www.w3.org/2000/svg';const baseline=document.createElementNS(ns,'line');baseline.setAttribute('x1',100);baseline.setAttribute('x2',100);baseline.setAttribute('y1',10);baseline.setAttribute('y2',90);baseline.setAttribute('stroke','#64748b');svg.append(baseline);const rect=document.createElementNS(ns,'rect');const width=Math.min(650,Math.abs(net)*5);rect.setAttribute('x',net>=0?100:Math.max(0,100-Math.min(100,width)));rect.setAttribute('y',25);rect.setAttribute('width',net>=0?width:Math.min(100,width));rect.setAttribute('height',35);rect.setAttribute('fill',net>=0?'#2563eb':'#dc2626');svg.append(rect);const text=document.createElementNS(ns,'text');text.setAttribute('x',110);text.setAttribute('y',84);text.textContent='正值省时；负值增加工作；横轴5px/分钟，超界截断。';svg.append(text);}
for(const i of Object.values(inputs))i.addEventListener('input',update);document.getElementById('cards').append(s);update();}
</script></html>""".replace("__DATA__", encoded)
    (out / "calculator.html").write_text(page, encoding="utf-8")


def generate(scenario_path: Path, out: Path, history_path: Path | None = None) -> None:
    data, results = load_scenarios(scenario_path)
    out.mkdir(parents=True, exist_ok=True)
    # Explicit allowlist: do not copy per-message evidence_index/source paths to GitHub.
    history = json.loads(history_path.read_text(encoding="utf-8")) if history_path else None
    summary = {
        "generated_date": datetime.now(ZoneInfo("Pacific/Auckland")).date().isoformat(),
        "history": {"loaded": history is not None, "status": history.get("status") if history else "not_provided",
                    "counts": history.get("counts") if history else None,
                    "window": history.get("window") if history else None},
        "scenario_status": data["status"], "scenarios": results,
        "no_combined_savings_total": "candidate scopes may overlap",
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with (out / "scenarios.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["候选", "数据性质", "假设人工核查/周", "原分钟/次", "审核分钟/次", "覆盖率", "维护及异常/周", "净分钟低", "净分钟中", "净分钟高", "建设分钟中", "回本周中", "首年净小时中"])
        for c, r in zip(data["candidates"], results):
            writer.writerow([c["name_zh"], "纯情景假设_非历史实测", c["checks_per_week"]["central"],
                             c["manual_minutes_per_check"], c["review_minutes_per_check"],
                             c["automation_fraction"]["central"], c["maintenance_minutes_per_week"] + c["exception_minutes_per_week"],
                             *[r["net_minutes_per_week"][s] for s in ("low", "central", "high")],
                             r["build_minutes_central"], r["central_payback_weeks"], r["central_annual_net_hours_after_build"]])
    rows = []
    for c, r in zip(data["candidates"], results):
        net = r["net_minutes_per_week"]
        rows.append(f"| {c['name_zh']} | {c['checks_per_week']['central']:g} | {net['low']:g} / {net['central']:g} / {net['high']:g} | {r['build_minutes_central']} | {r['central_payback_weeks']} | {r['central_annual_net_hours_after_build']} |")
    history_note = "未提供；没有真实历史问题总数、重复次数、平均每周次数，也没有实测时间。"
    if history:
        history_note = (f"已导入，状态 `{history.get('status')}`；匿名汇总见 summary.json。"
                        "分类仍需人工核对；有数据不等于任务识别和耗时测量已经完成。")
    report = f"""# 自动化机会审计：阶段报告

## 可回答与待回答

历史数据：{history_note}

范围：全部历史为主，最近 90 天用于趋势对照，按新西兰时区统计。当前事实只包括已有 SEC Discord 机器人、Powershop 周二买包，以及聊天主要发生在 chat/work。无法据此声称你在其他主题上投入了多少时间。

目前推荐的是验证顺序，不是用户实际省时排名：先检查现有 SEC 机器人还留下哪些手工筛选；再从历史验证是否经常反复追踪同一股票；Powershop 仅补实际缺口。不要重建已有效的自动化。

## 情景柱状图（不是历史实测）

![增量省时情景图](scenario-savings.svg)

柱形是中间假设，横线是输入情景范围，不是统计置信区间。负值表示反而多花时间。原始输入见 `../data/scenarios.json`，计算器见 [calculator.html](calculator.html)。

| 候选 | 假设剩余人工核查/周 | 假设净分钟/周：低 / 中 / 高 | 建设分钟（中） | 回本周（中） | 首年扣建设净小时（中） |
| --- | ---: | ---: | ---: | ---: | ---: |
{chr(10).join(rows)}

所有数字均未用你的历史验证。核查次数不等于提问次数；不能把 10 条追问简单计为 10 次独立工作。这些项目可能处理同一信息，不能相加。维护、异常和阅读通知均有成本。

模型：`每周净省时 = 剩余人工核查次数 × 可替代比例 ×（手工分钟 − 审核分钟）− 维护 − 异常处理`。

回本只在净省时大于零时成立；首年净收益再扣一次建设投入。开发时长只是预算假设，未验证的接口、反爬、账户限制可能大幅增加成本。

在这些输入下，股票变化摘要约11.4周回本；SEC 增量整理约14.4周；Powershop 小缺口约90周。由此只能推导：低频的小事可能不值得做新 App，适合补现有脚本或一个提醒。不能据此推导你本人会获得这些收益。

## 候选落地方式

1. **SEC：在现有机器人里加“值得研究的交易”卡片。** 先检查现有功能。只对尚未处理的 P/S 类交易、持股变化、集中买入、修订、计划交易披露做整理。每张卡带原始申报和筛选理由；不把所有 P/S 认定为主观主动买卖，不从申报推导收益保证。目标是少开多个网页核对同一笔交易。
2. **关注股票：按重要变化推送，而非定时泛新闻。** 名单和关注理由一次录入，记录上次状态，抓官方申报与投资者关系消息；财报、指引、稀释等触发差异卡。复用 Discord，一个日汇总，紧急阈值另设；同一事件只通知一次。需要真实历史确认这一工作反复存在。
3. **Powershop：覆盖天数和购买结果的异常提醒。** 核查月初购买、预算、余额/电量、到期和重复购买是否已被现有脚本覆盖。已有周二自动买包不再计入收益。优先补购买后核验、幂等和失败提醒；只有省时达到门槛才做更多界面。没有账户接口前，只能提供方案，不能声称已自动验证或购买。
4. **两个机器人的健康检查。** 仅在漏跑、数据过期、购买结果不确定时提醒，且先检查现有告警。它的价值是避免自动化失效后的返工，当前没有证据把它计成每周省时。

完整设计、数据依赖与 ChatGPT 任务模板：[automation-designs.md](../docs/automation-designs.md)。模板没有被创建为真实定期任务；当前工具没有 ChatGPT 任务创建能力。

## 下一阶段如何产生真实结论

1. 将真实导出放入 `private/`，执行 `python3 -m audit.history private/conversations.json --output private/history-analysis.json`。ZIP 也可直接读；不同账户/工作区的导出覆盖不应混为完整账户历史。
2. 检查导入覆盖、时间缺失、会话分支、分类盲区和每周分布。全历史统计与最近90天分开报告。不会从聊天时间戳推算真实工作分钟。
3. 对重复最多的工作抽取 10 个私有证据会话人工复核，再进行两周短期计时：动作开始/结束、是否仍需核查、通知审核和异常分钟。旧历史只能提供频率线索；无法补测的旧分钟数保持估算。
4. 给每个动作一个唯一 ID，处理候选覆盖重叠，扣除现有自动化，才能计算组合收益。先试运行一个项目；达到实际净省时门槛后才上线。

## 尚未完成

全历史行为归纳、真实低效事项排名、实测柱状图、定期任务创建、线上运行均未完成。当前完成的是可复现的本地分析工具、候选设计、透明收益情景、版本化进度。原始对话及逐条私有证据不提交 GitHub。
"""
    (out / "REPORT.zh-CN.md").write_text(report, encoding="utf-8")
    render_chart(data["candidates"], results, out)
    write_dashboard(data["candidates"], out)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenarios", type=Path, default=ROOT / "data/scenarios.json")
    parser.add_argument("--out", type=Path, default=ROOT / "reports")
    parser.add_argument("--history", type=Path)
    args = parser.parse_args()
    generate(args.scenarios, args.out, args.history)


if __name__ == "__main__":
    main()
