#!/usr/bin/env python3
"""lhg-event-plan 硬门②：写活动方案文档后，检查预算表分项求和与风险项预案。

触发：PostToolUse / Write|Edit。只看文件名像活动方案的文档
（event/活动/plan/方案/budget/预算/schedule/排期/risk/风险/_ep.md），
其他文件 0 开销放行。发现"预算求和不一致"或"风险项无预案"时，
通过 additionalContext 给出提醒（不拦截）。

逃生方式：LHG_EP_HOOKS_OFF=1（本次 hook 直接放行）。

设计原则：内部出错 fail-open；这是一个提醒，不是门禁。
真正的门禁在 /lhg-event-plan:event-check。零第三方依赖。
"""

import json
import os
import re
import sys

EVENT_RE = re.compile(r"event|活动|plan|方案|budget|预算|schedule|排期|risk|风险|_ep\.md$", re.I)
MONEY_RE = re.compile(r"(\d[\d,]*)\s*元?")
SUM_LINE_RE = re.compile(r"(合计|总计|总预算|sum|total)", re.I)
RISK_LINE_RE = re.compile(r"^(R\d+|[-*]\s*(风险|R\d))", re.I | re.M)
PLAN_WORD_RE = re.compile(r"预案|B\s*方案|备用|应急措施|应对")


def _parse_money(s):
    m = MONEY_RE.search(s.replace(",", ""))
    return int(m.group(1)) if m else None


def check_budget(text):
    """预算表分项求和 vs 合计行。返回问题描述或 None。"""
    lines = text.split("\n")
    items, total = [], None
    in_table = False
    for ln in lines:
        if "|" in ln and ("金额" in ln or "预算" in ln or "费用" in ln):
            in_table = True
            continue
        if in_table and "|" in ln and "---" not in ln:
            cells = [c.strip() for c in ln.strip().strip("|").split("|")]
            amt = _parse_money(" ".join(cells[1:]))
            if amt is None:
                continue
            if SUM_LINE_RE.search(cells[0]):
                total = amt
            elif "应急" not in cells[0] and "预留" not in cells[0]:
                items.append(amt)
        elif in_table and "|" not in ln and ln.strip():
            in_table = False
    if total is not None and items and sum(items) != total:
        return "预算表分项求和 %d 元 ≠ 合计 %d 元（差 %d 元）" % (
            sum(items), total, total - sum(items))
    return None


def check_risk(text):
    """风险项无预案提醒。返回无预案的风险编号列表。"""
    no_plan = []
    for m in RISK_LINE_RE.finditer(text):
        line = m.group(0)
        # 取该行之后 3 行看有没有预案关键词
        start = text.find(line)
        window = text[start:start + 300]
        if not PLAN_WORD_RE.search(window):
            no_plan.append(line.strip()[:30])
    return no_plan


def main() -> int:
    if os.environ.get("LHG_EP_HOOKS_OFF") == "1":
        return 0
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        return 0
    tool_input = payload.get("tool_input", {}) or {}
    fp = tool_input.get("file_path", "") or ""
    if not EVENT_RE.search(os.path.basename(fp)):
        return 0  # 非活动方案文档：放行
    try:
        with open(fp, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return 0

    notes = []
    b = check_budget(text)
    if b:
        notes.append("预算求和异常：" + b + "。跑 /lhg-event-plan:event-check 做全量自查。")
    r = check_risk(text)
    if r:
        notes.append("以下风险项没写预案：%s。风险没预案=没写，补上触发信号+预案+责任人。"
                     % "、".join(r[:5]))
    if notes:
        print(json.dumps({"additionalContext": "\n".join(notes)},
                         ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
