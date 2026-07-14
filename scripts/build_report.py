#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
质量预警升级报告生成器（纯文字版 .txt + Markdown .md）
读取异常事件 JSON（或内置小样本）→ 按 L1–L4 升级矩阵分级 → 核算响应时限(达标/超时) → 生成升级流程 + 看板 → 输出 .txt + .md。

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
  python build_report.py --input events.json --out-dir ./out
  python build_report.py --out-dir ./out          # 使用内置小样本
"""

import argparse
import json
import os
from datetime import date

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


def generate_txt(meta, events, rows, summary):
    lines = []
    lines.append("=" * 72)
    lines.append(f"质量预警升级看板 · {meta.get('plant','未命名工厂')}")
    lines.append("=" * 72)
    lines.append("")
    lines.append(f"工厂：{meta.get('plant','待企业补充')} ｜ 班次：{meta.get('shift','—')} ｜ 日期：{meta.get('date','—')}")
    lines.append(f"负责人：{meta.get('owner','待企业补充')}")
    lines.append("")

    # 一、升级流程
    lines.append("-" * 72)
    lines.append("一、升级流程")
    lines.append("-" * 72)
    lines.append("异常触发（Andon/巡检/客户端） → 分级判定(L1–L4) → 按升级矩阵通知 → 限时响应 → 遏制/隔离/停线 → 闭环(8D/记录) → 看板跟踪。")
    lines.append("")

    # 二、升级矩阵
    lines.append("-" * 72)
    lines.append("二、升级矩阵（通用参考，企业可覆盖）")
    lines.append("-" * 72)
    for lv in ["L1", "L2", "L3", "L4"]:
        m = MATRIX[lv]
        lines.append(f"  {lv} {m['name']}：触发={m['trigger']}；通知={m['notify']}；时限=≤{m['limit_min']}min；动作={m['action']}")
    lines.append("")

    # 三、实时看板
    lines.append("-" * 72)
    lines.append("三、实时看板")
    lines.append("-" * 72)
    lines.append("  事件 | 产线/工位 | 缺陷 | 等级 | 涉及量 | 发生→响应 | 响应时长 | 时限 | 状态 | 响应人")
    lines.append("  " + "-" * 68)
    for ev, r in zip(events, rows):
        if not r["time_valid"]:
            status = "时间缺失"
        elif r["on_time"]:
            status = "达标"
        else:
            status = "⚠️超时"
        lines.append(
            f"  {ev.get('event_id','—')} | {ev.get('line','—')}/{ev.get('station','—')} | {ev.get('defect','—')} "
            f"| {r['level']} {r['lv_name']} | {ev.get('affected_qty','—')} | "
            f"{ev.get('occur_time','—')}→{ev.get('response_time','—')} | {fmt_dur(r['duration_min'])} | ≤{r['limit_min']}min | {status} | {ev.get('responder','待企业补充')}"
        )
    lines.append("")

    # 四、汇总
    lines.append("-" * 72)
    lines.append("四、汇总统计")
    lines.append("-" * 72)
    lines.append(f"  异常事件总数：{summary['total']}")
    bl = "，".join(f"{k}:{v}" for k, v in sorted(summary["by_level"].items()))
    lines.append(f"  分级分布：{bl if bl else '—'}")
    lines.append(f"  超时事件：{summary['overtime']} 条（需重点跟进）")
    lines.append("")
    lines.append("-" * 72)
    lines.append("本看板由质量预警升级助手生成。组织层级、人员职责缺失处已标注「待企业补充」；分级默认参考通用矩阵，最终以企业制度为准。")
    lines.append("")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="质量预警升级报告生成器（txt + md）")
    ap.add_argument("--input", help="异常事件 JSON 路径（缺省使用内置小样本）")
    ap.add_argument("--out-dir", default=os.getcwd(), help="输出目录（默认当前工作目录）")
    ap.add_argument("--format", choices=["txt", "md", "all"], default="all",
                    help="输出格式：txt / md / all（默认 all = txt + md）")
    args = ap.parse_args()

    if args.input:
        with open(args.input, "r", encoding="utf-8") as f:
            payload = json.load(f)
    else:
        payload = SAMPLE_DATA
        print("ℹ️ 未提供 --input，使用内置小样本数据。")

    meta = payload.get("meta", {}) or {}
    events = payload.get("events", []) or []

    rows = [eval_event(e) for e in events]
    summary = compute_summary(rows)

    date_str = date.today().strftime("%Y%m%d")
    base = f"质量预警升级看板_{meta.get('plant','未命名工厂')}_{date_str}".replace("/", "-")
    os.makedirs(args.out_dir, exist_ok=True)

    if args.format in ("md", "all"):
        md = generate_md(meta, events, rows, summary)
        md_path = os.path.join(args.out_dir, base + ".md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md)
        print(f"✅ MD : {md_path}")

    if args.format in ("txt", "all"):
        txt = generate_txt(meta, events, rows, summary)
        txt_path = os.path.join(args.out_dir, base + ".txt")
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(txt)
        print(f"✅ TXT: {txt_path}")

    print(f"   异常总数={summary['total']}  超时={summary['overtime']}")


if __name__ == "__main__":
    main()
