#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
质量预警升级报告生成器（混合式双版：MD + HTML）
读取异常事件 JSON（或内置小样本）→ 按 L1–L4 升级矩阵分级 → 核算响应时限(达标/超时) → 生成升级流程 + 看板 → 输出 MD + HTML。

JSON 字段：
{
  "meta": {"plant":"工厂","shift":"班次","date":"日期","owner":"负责人"},
  "events": [
    {
      "event_id":"E-001",
      "line":"总装线A","station":"工位12","defect":"扭矩不合格",
      "level":"L3","detected_qty":5,"affected_qty":120,
      "occur_time":"10:15","response_time":"10:38","responder":"现场主管"
    }
  ]
}

用法：
  python build_report.py -i events.json -o escalation_report
  python build_report.py -o escalation_report          # 使用内置小样本
"""

import argparse
import json
import os
import html

# 主色
MAIN = "#C8102E"

# 升级矩阵（通用参考，企业可覆盖）
MATRIX = {
    "L1": {"name": "轻微", "trigger": "单件/局部异常", "notify": "班组长/操作员", "limit_min": 240, "action": "现场纠正 + 记录"},
    "L2": {"name": "一般", "trigger": "小批量异常", "notify": "线长 → 主管", "limit_min": 120, "action": "遏制 + 初步分析"},
    "L3": {"name": "严重", "trigger": "影响整线/客户风险", "notify": "主管 → 质量经理", "limit_min": 60, "action": "隔离 + 启动 8D"},
    "L4": {"name": "重大", "trigger": "安全/法规/批量流出风险", "notify": "经理 → 总监/客户", "limit_min": 30, "action": "全线停 + 上报"},
}


# 内置小样本
SAMPLE_DATA = {
    "meta": {"plant": "XX 工厂", "shift": "白班", "date": "2026-07-13", "owner": "待企业补充"},
    "events": [
        {"event_id": "E-001", "line": "总装线A", "station": "工位12", "defect": "扭矩不合格",
         "level": "L3", "detected_qty": 5, "affected_qty": 120,
         "occur_time": "10:15", "response_time": "10:38", "responder": "现场主管"},
        {"event_id": "E-002", "line": "焊装线B", "station": "工位07", "defect": "虚焊",
         "level": "L2", "detected_qty": 2, "affected_qty": 40,
         "occur_time": "11:02", "response_time": "11:20", "responder": "线长"},
        {"event_id": "E-003", "line": "涂装线C", "station": "工位03", "defect": "颗粒",
         "level": "L1", "detected_qty": 1, "affected_qty": 8,
         "occur_time": "13:40", "response_time": "13:55", "responder": "操作员"},
    ],
}


def parse_min(s):
    """将 'HH:MM' 解析为分钟数；失败返回 None"""
    try:
        h, m = str(s).split(":")
        return int(h) * 60 + int(m)
    except Exception:
        return None


def eval_event(ev):
    """分级映射 + 时限核算"""
    level = ev.get("level", "L1")
    info = MATRIX.get(level, MATRIX["L1"])
    occur = parse_min(ev.get("occur_time"))
    resp = parse_min(ev.get("response_time"))
    if occur is not None and resp is not None:
        dur = resp - occur
        if dur < 0:
            dur += 24 * 60  # 跨日
        limit = info["limit_min"]
        ok = dur <= limit
        return {
            "level": level, "lv_name": info["name"], "notify": info["notify"],
            "limit_min": limit, "action": info["action"], "trigger": info["trigger"],
            "duration_min": dur, "on_time": ok, "time_valid": True,
        }
    return {
        "level": level, "lv_name": info["name"], "notify": info["notify"],
        "limit_min": info["limit_min"], "action": info["action"], "trigger": info["trigger"],
        "duration_min": None, "on_time": None, "time_valid": False,
    }


def compute_summary(rows):
    total = len(rows)
    by_level = {}
    overtime = 0
    for r in rows:
        by_level[r["level"]] = by_level.get(r["level"], 0) + 1
        if r["time_valid"] and not r["on_time"]:
            overtime += 1
    return {"total": total, "by_level": by_level, "overtime": overtime}


def fmt_dur(d):
    if d is None:
        return "—"
    return f"{d} 分钟"


def level_color(level):
    return {"L1": "#2E9E5B", "L2": "#E8A33D", "L3": MAIN, "L4": "#7A0C1E"}.get(level, "#666")


def generate_md(meta, events, rows, summary):
    lines = []
    lines.append(f"# 质量预警升级看板 · {meta.get('plant','未命名工厂')}")
    lines.append("")
    lines.append(f"- **工厂**：{meta.get('plant','待企业补充')} ｜ **班次**：{meta.get('shift','—')} ｜ **日期**：{meta.get('date','—')}")
    lines.append(f"- **负责人**：{meta.get('owner','待企业补充')}")
    lines.append("")

    # 一、升级流程
    lines.append("## 一、升级流程")
    lines.append("")
    lines.append("异常触发（Andon/巡检/客户端） → 分级判定(L1–L4) → 按升级矩阵通知 → 限时响应 → 遏制/隔离/停线 → 闭环(8D/记录) → 看板跟踪。")
    lines.append("")

    # 二、升级矩阵
    lines.append("## 二、升级矩阵（通用参考，企业可覆盖）")
    lines.append("")
    lines.append("| 等级 | 名称 | 触发条件 | 通知对象 | 响应时限 | 标准动作 |")
    lines.append("|------|------|----------|----------|----------|----------|")
    for lv in ["L1", "L2", "L3", "L4"]:
        m = MATRIX[lv]
        lines.append(f"| {lv} | {m['name']} | {m['trigger']} | {m['notify']} | ≤{m['limit_min']}min | {m['action']} |")
    lines.append("")

    # 三、实时看板
    lines.append("## 三、实时看板")
    lines.append("")
    lines.append("| 事件 | 产线/工位 | 缺陷 | 等级 | 涉及量 | 发生→响应 | 响应时长 | 时限 | 状态 | 响应人 |")
    lines.append("|------|-----------|------|------|--------|-----------|----------|------|------|--------|")
    for ev, r in zip(events, rows):
        if not r["time_valid"]:
            status = "时间缺失"
        elif r["on_time"]:
            status = "达标"
        else:
            status = "⚠️超时"
        lines.append(
            f"| {ev.get('event_id','—')} | {ev.get('line','—')}/{ev.get('station','—')} | {ev.get('defect','—')} "
            f"| {r['level']} {r['lv_name']} | {ev.get('affected_qty','—')} | "
            f"{ev.get('occur_time','—')}→{ev.get('response_time','—')} | {fmt_dur(r['duration_min'])} | ≤{r['limit_min']}min | {status} | {ev.get('responder','待企业补充')} |"
        )
    lines.append("")

    # 四、汇总
    lines.append("## 四、汇总统计")
    lines.append("")
    lines.append(f"- 异常事件总数：**{summary['total']}**")
    bl = "，".join(f"{k}:{v}" for k, v in sorted(summary["by_level"].items()))
    lines.append(f"- 分级分布：{bl if bl else '—'}")
    lines.append(f"- 超时事件：**{summary['overtime']}** 条（需重点跟进）")
    lines.append("")
    lines.append("---")
    lines.append("> 本看板由质量预警升级助手生成。组织层级、人员职责缺失处已标注「待企业补充」；分级默认参考通用矩阵，最终以企业制度为准。")
    lines.append("")
    return "\n".join(lines)


def generate_html(meta, events, rows, summary):
    plant = html.escape(str(meta.get("plant", "未命名工厂")))
    shift = html.escape(str(meta.get("shift", "—")))
    date = html.escape(str(meta.get("date", "—")))
    owner = html.escape(str(meta.get("owner", "待企业补充")))

    # 矩阵表
    matrix_rows = ""
    for lv in ["L1", "L2", "L3", "L4"]:
        m = MATRIX[lv]
        c = level_color(lv)
        matrix_rows += f"<tr><td style='color:{c};font-weight:bold'>{lv} {html.escape(m['name'])}</td><td>{html.escape(m['trigger'])}</td><td>{html.escape(m['notify'])}</td><td>≤{m['limit_min']}min</td><td>{html.escape(m['action'])}</td></tr>"

    # 看板
    kb = ""
    for ev, r in zip(events, rows):
        c = level_color(r["level"])
        if not r["time_valid"]:
            status = "<span class='badge miss'>时间缺失</span>"
        elif r["on_time"]:
            status = "<span class='badge ok'>达标</span>"
        else:
            status = "<span class='badge late'>⚠️超时</span>"
        kb += f"""<tr>
<td>{html.escape(str(ev.get('event_id','—')))}</td>
<td>{html.escape(str(ev.get('line','—')))}/{html.escape(str(ev.get('station','—')))}</td>
<td>{html.escape(str(ev.get('defect','—')))}</td>
<td style='color:{c};font-weight:bold'>{r['level']}</td>
<td>{html.escape(str(ev.get('affected_qty','—')))}</td>
<td>{html.escape(str(ev.get('occur_time','—')))}→{html.escape(str(ev.get('response_time','—')))}</td>
<td>{fmt_dur(r['duration_min'])}</td>
<td>≤{r['limit_min']}min</td>
<td>{status}</td>
<td>{html.escape(str(ev.get('responder','待企业补充')))}</td></tr>"""

    bl = "，".join(f"{k}:{v}" for k, v in sorted(summary["by_level"].items())) or "—"

    doc = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>质量预警升级看板 · {plant}</title>
<style>
*{{box-sizing:border-box;font-family:-apple-system,'Segoe UI','Microsoft YaHei',sans-serif;margin:0;padding:0;color:#1f2329}}
body{{background:#f5f6f8;padding:24px}}
.wrap{{max-width:1080px;margin:0 auto;background:#fff;border-radius:12px;padding:28px 32px;box-shadow:0 2px 12px rgba(0,0,0,.06)}}
h1{{font-size:22px;color:{MAIN};border-bottom:3px solid {MAIN};padding-bottom:10px}}
.meta{{color:#666;font-size:13px;margin:12px 0 18px;line-height:1.7}}
.section{{margin:22px 0}}
.section h2{{font-size:16px;color:{MAIN};border-left:4px solid {MAIN};padding-left:8px;margin-bottom:10px}}
.flow{{background:#fafbfc;border:1px solid #e8eaed;border-radius:8px;padding:12px 16px;font-size:14px;line-height:1.8}}
table{{width:100%;border-collapse:collapse;font-size:13px;margin:6px 0}}
th,td{{border:1px solid #e8eaed;padding:8px 10px;text-align:left}}
th{{background:#fafbfc;color:#444}}
.badge{{padding:2px 8px;border-radius:10px;font-size:12px;color:#fff}}
.badge.ok{{background:#2E9E5B}}.badge.late{{background:{MAIN}}}.badge.miss{{background:#999}}
.cards{{display:flex;gap:14px;margin:10px 0}}
.card{{flex:1;background:#fafbfc;border:1px solid #e8eaed;border-radius:8px;padding:14px;text-align:center}}
.card b{{font-size:26px;color:{MAIN};display:block}}
.foot{{color:#999;font-size:12px;margin-top:22px;border-top:1px dashed #ddd;padding-top:10px}}
</style></head>
<body><div class="wrap">
<h1>质量预警升级看板 · {plant}</h1>
<div class="meta">班次：{shift} ｜ 日期：{date} ｜ 负责人：{owner}</div>

<div class="section"><h2>升级流程</h2>
<div class="flow">异常触发（Andon / 巡检 / 客户端） → 分级判定(L1–L4) → 按升级矩阵通知 → 限时响应 → 遏制 / 隔离 / 停线 → 闭环(8D / 记录) → 看板跟踪</div></div>

<div class="section"><h2>升级矩阵（通用参考，企业可覆盖）</h2>
<table><tr><th>等级</th><th>触发条件</th><th>通知对象</th><th>响应时限</th><th>标准动作</th></tr>{matrix_rows}</table></div>

<div class="section"><h2>实时看板</h2>
<table><tr><th>事件</th><th>产线/工位</th><th>缺陷</th><th>等级</th><th>涉及量</th><th>发生→响应</th><th>响应时长</th><th>时限</th><th>状态</th><th>响应人</th></tr>{kb}</table></div>

<div class="section"><h2>汇总统计</h2>
<div class="cards">
<div class="card"><b>{summary['total']}</b>异常总数</div>
<div class="card"><b>{summary['overtime']}</b>超时事件</div>
<div class="card"><b>{len(summary['by_level'])}</b>涉及等级</div>
</div>
<p style="font-size:13px;color:#666">分级分布：{html.escape(bl)}</p></div>

<div class="foot">本看板由质量预警升级助手生成。组织层级、人员职责缺失处已标注「待企业补充」；分级默认参考通用矩阵，最终以企业制度为准。</div>
</div></body></html>"""
    return doc


def main():
    ap = argparse.ArgumentParser(description="质量预警升级报告生成器（MD + HTML 双版）")
    ap.add_argument("-i", "--input", help="异常事件 JSON 路径（缺省使用内置小样本）")
    ap.add_argument("-o", "--output", default="escalation_report", help="输出前缀（生成 .md 与 .html）")
    args = ap.parse_args()

    if args.input:
        with open(args.input, "r", encoding="utf-8") as f:
            payload = json.load(f)
    else:
        payload = SAMPLE_DATA
        print("ℹ️ 未提供 -i，使用内置小样本数据。")

    meta = payload.get("meta", {}) or {}
    events = payload.get("events", []) or []

    rows = [eval_event(e) for e in events]
    summary = compute_summary(rows)

    md = generate_md(meta, events, rows, summary)
    htm = generate_html(meta, events, rows, summary)

    out_dir = os.path.dirname(args.output)
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir, exist_ok=True)

    md_path = args.output + ".md"
    html_path = args.output + ".html"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(htm)

    print(f"✅ 报告已生成：\n  MD : {md_path}\n  HTML: {html_path}")
    print(f"   异常总数={summary['total']}  超时={summary['overtime']}")


if __name__ == "__main__":
    main()
