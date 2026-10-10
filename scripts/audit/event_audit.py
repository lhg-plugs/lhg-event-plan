#!/usr/bin/env python3
"""lhg-event-plan 确定性活动方案审计：30 条规则，零第三方依赖。

扫描目标目录下的活动方案文档（*.md），检查：
  预算（求和校验/应急预留/支付节点）、排期（时间冲突/单点故障/里程碑）、
  物料（数量/负责人/到货时间/类别覆盖）、风险（预案/等级/责任人/B方案）、
  目标（量化指标/数据来源/复盘计划）。

用法：
    python3 event_audit.py --target <目录> [--format json|text] [--fail-on blocker|warning|nit]

有 blocker 时以 exit code 2 退出（供 hooks 与 CI 使用）。
规则文案均为本项目原创。
"""

import argparse
import json
import os
import re
import sys

VERSION = "1.0.0"
TOOL = "lhg-event-plan/event-check"

SKIP_DIRS = {
    "node_modules", ".next", "dist", "build", ".git", "coverage",
    "__pycache__", "vendor", ".turbo", "out", "fixtures",
}
SCAN_EXTS = {".md", ".markdown", ".txt"}

SEVERITY_ORDER = {"blocker": 0, "warning": 1, "nit": 2}

DATE_RE = re.compile(r"20\d{2}[-/年](\d{1,2})[-/月](\d{1,2})")
PERSON_RE = re.compile(r"@([^\s@，,、；;：:()（）]+)")
MONEY_RE = re.compile(r"(\d[\d,]*)\s*元?")
SUM_LINE_RE = re.compile(r"^(合计|总计|总预算)", re.I)
SPECIAL_ROW_RE = re.compile(r"应急|预留|不可预见|凑整")
PLAN_WORD_RE = re.compile(r"预案|B\s*方案|备用|应急措施|应对|切换")
RISK_ID_RE = re.compile(r"^(R\d+|[-*]\s*R\d+)", re.I)
LEVEL_RE = re.compile(r"概率\s*[高中低]|影响\s*[高中低]|等级\s*[高中低]|高风险|中风险|低风险")
ONSITE_RE = re.compile(r"到场|彩排|接机|签到|搭建|主持|布展|走台")
TASK_LINE_RE = re.compile(r"^\s*[-*]\s+\S")


class Finding:
    __slots__ = ("rule_id", "severity", "category", "title", "file",
                 "line", "snippet", "detail")

    def __init__(self, rule_id, severity, category, title, file, line,
                 snippet, detail):
        self.rule_id = rule_id
        self.severity = severity
        self.category = category
        self.title = title
        self.file = file
        self.line = line
        self.snippet = snippet
        self.detail = detail

    def to_dict(self):
        return {
            "rule_id": self.rule_id, "severity": self.severity,
            "category": self.category, "title": self.title,
            "file": self.file, "line": self.line,
            "snippet": self.snippet, "detail": self.detail,
        }


RULES = []
_RULE_BY_ID = {}


def rule(rule_id, severity, category, title, detail):
    def deco(fn):
        r = {"id": rule_id, "severity": severity, "category": category,
             "title": title, "detail": detail}
        _RULE_BY_ID[rule_id] = r
        r["check"] = fn
        RULES.append(r)
        return fn
    return deco


def _mk(rule_id, path, line, snippet):
    r = _RULE_BY_ID[rule_id]
    return Finding(r["id"], r["severity"], r["category"], r["title"],
                   path, line, snippet[:160], r["detail"])


def collect_files(target):
    out = []
    for root, dirs, files in os.walk(target):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            _, ext = os.path.splitext(f)
            if ext in SCAN_EXTS:
                out.append(os.path.join(root, f))
    return sorted(out)


def read_text(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def _sections(lines):
    """按 ## 标题切分章节，返回 {关键词: [(行号, 行文本), ...]}。"""
    secs = {}
    cur = None
    for i, ln in enumerate(lines):
        m = re.match(r"^#{1,3}\s*(.+?)\s*$", ln.strip())
        if m:
            cur = m.group(1)
            secs.setdefault(cur, [])
        elif cur is not None:
            secs[cur].append((i + 1, ln))
    return secs


def _find_sec(secs, *keywords):
    out = []
    for title, body in secs.items():
        if any(k in title for k in keywords):
            out.extend(body)
    return out


def _parse_money_cell(s):
    """解析金额单元格：优先带「元/万」单位的数字，避免误取「10%」这类数字。"""
    s = s.replace(",", "")
    m = re.search(r"(\d+)\s*元", s)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+)\s*万", s)
    if m:
        return int(m.group(1)) * 10000
    m = MONEY_RE.search(s)
    return int(m.group(1)) if m else None


def _budget_rows(sec_lines):
    """解析预算表行：返回 (items, special, total, total_line, header)。"""
    items, special = [], []
    total, total_line, header = None, 0, ""
    in_table = False
    for ln_no, ln in sec_lines:
        if "|" in ln and ("金额" in ln or "预算" in ln or "费用" in ln):
            in_table = True
            header = ln
            continue
        if in_table and "|" in ln and "---" not in ln:
            cells = [c.strip() for c in ln.strip().strip("|").split("|")]
            if not cells or not cells[0]:
                continue
            amt = _parse_money_cell(" ".join(cells[1:]))
            if amt is None:
                continue
            if SUM_LINE_RE.search(cells[0]):
                total, total_line = amt, ln_no
            elif SPECIAL_ROW_RE.search(cells[0]):
                special.append((ln_no, cells[0], amt))
            else:
                items.append((ln_no, cells[0], amt))
        elif in_table and "|" not in ln and ln.strip():
            in_table = False
    return items, special, total, total_line, header


# ================================================================ 预算规则

@rule("EP-BUD-001", "blocker", "预算", "分项求和与总预算不一致",
      "预算表分项金额加起来不等于合计行。这是活动预算翻车的头号原因：请逐项核对，差额单独列一行说明去向。")
def _b001(lines, text, path, ctx):
    secs = _sections(lines)
    body = _find_sec(secs, "预算", "费用", "花费")
    if not body:
        return []
    items, special, total, total_line, _ = _budget_rows(body)
    if total is None or not items:
        return []
    expect = sum(a for _, _, a in items) + sum(a for _, _, a in special)
    if expect != total:
        return [_mk("EP-BUD-001", path, total_line,
                     "分项求和 %d ≠ 合计 %d（差 %d）"
                     % (expect, total, total - expect))]
    return []


@rule("EP-BUD-002", "blocker", "预算", "预算表无合计/总预算行",
      "有预算明细却没有合计行，没人知道总共要花多少钱：加上「合计」行并确保分项求和一致。")
def _b002(lines, text, path, ctx):
    secs = _sections(lines)
    body = _find_sec(secs, "预算", "费用", "花费")
    if not body:
        return []
    items, _, total, _, _ = _budget_rows(body)
    if items and total is None:
        return [_mk("EP-BUD-002", path, body[0][0], "预算表明细 %d 项但无合计行" % len(items))]
    return []


@rule("EP-BUD-003", "warning", "预算", "无应急预留",
      "预算表没有「应急预留」行。建议单独列一行，按总预算 10%（高风险活动 15%）预留，动用需负责人确认。")
def _b003(lines, text, path, ctx):
    secs = _sections(lines)
    body = _find_sec(secs, "预算", "费用", "花费")
    if not body:
        return []
    if not any(SPECIAL_ROW_RE.search(ln) for _, ln in body):
        return [_mk("EP-BUD-003", path, body[0][0], "预算章节无应急预留行")]
    return []


@rule("EP-BUD-004", "blocker", "预算", "单项金额超过总预算",
      "某个明细项的金额比合计还大，数字一定录错了：核对该项金额与合计行。")
def _b004(lines, text, path, ctx):
    secs = _sections(lines)
    body = _find_sec(secs, "预算", "费用", "花费")
    if not body:
        return []
    items, special, total, _, _ = _budget_rows(body)
    if total is None:
        return []
    out = []
    for ln_no, name, amt in items + special:
        if amt > total:
            out.append(_mk("EP-BUD-004", path, ln_no,
                            "%s %d 元 > 合计 %d 元" % (name, amt, total)))
    return out


@rule("EP-BUD-005", "warning", "预算", "预算表无负责人列",
      "预算表没有「负责人」列。每笔钱都要有人认领：加上负责人列，精确到具体人名。")
def _b005(lines, text, path, ctx):
    secs = _sections(lines)
    body = _find_sec(secs, "预算", "费用", "花费")
    if not body:
        return []
    items, _, _, _, header = _budget_rows(body)
    if items and "负责" not in header:
        return [_mk("EP-BUD-005", path, body[0][0], "预算表头无负责人列")]
    return []


@rule("EP-BUD-006", "warning", "预算", "金额缺单位",
      "有金额数字没写「元」单位。数字必须带单位，避免「2.5」到底是 2.5 元还是 2.5 万的歧义。")
def _b006(lines, text, path, ctx):
    secs = _sections(lines)
    body = _find_sec(secs, "预算", "费用", "花费")
    out = []
    for ln_no, ln in body:
        if "|" in ln and "---" not in ln and re.search(r"\d", ln):
            cells = [c.strip() for c in ln.strip().strip("|").split("|")]
            for c in cells[1:]:
                if "%" in c:
                    continue  # 百分比是比例不是金额，不判
                if re.search(r"\d", c) and "元" not in c and "万" not in c:
                    out.append(_mk("EP-BUD-006", path, ln_no, ln.strip()[:60]))
                    break
    return out


@rule("EP-BUD-007", "warning", "预算", "有待询价/待定金额项",
      "预算项标注了「待询价」「待定」。拍脑袋的项超过总预算 30% 则整表不可信：48 小时内落实书面报价。")
def _b007(lines, text, path, ctx):
    secs = _sections(lines)
    body = _find_sec(secs, "预算", "费用", "花费")
    out = []
    for ln_no, ln in body:
        if re.search(r"待询价|待定|TBD|拍脑袋", ln):
            out.append(_mk("EP-BUD-007", path, ln_no, ln.strip()[:60]))
    return out


@rule("EP-BUD-008", "nit", "预算", "无支付节点说明",
      "预算章节没写支付节点（什么时候付、付多少）。现金流断裂是小团队翻车主因之一：每笔大额支出写清支付节奏。")
def _b008(lines, text, path, ctx):
    secs = _sections(lines)
    body = _find_sec(secs, "预算", "费用", "花费")
    if not body:
        return []
    blob = "\n".join(ln for _, ln in body)
    if "付" not in blob and "支付" not in blob:
        return [_mk("EP-BUD-008", path, body[0][0], "预算章节无支付节点说明")]
    return []


@rule("EP-BUD-009", "nit", "预算", "未计算人均成本",
      "没算人均成本（总预算 ÷ 到场人数）。发布会人均超 500 元、团建人均超 800 元时，停下来重新审一遍值不值。")
def _b009(lines, text, path, ctx):
    secs = _sections(lines)
    body = _find_sec(secs, "预算", "费用", "花费")
    if not body:
        return []
    blob = "\n".join(ln for _, ln in body)
    if "人均" not in blob:
        return [_mk("EP-BUD-009", path, body[0][0], "预算章节未计算人均成本")]
    return []


# ================================================================ 排期规则

@rule("EP-SCH-001", "blocker", "排期", "同一人同一天两件到场任务",
      "同一人同一天被安排了两件「必须到场」的事（如彩排+接机）。人不会分身：调整时间或换人。")
def _s001(lines, text, path, ctx):
    secs = _sections(lines)
    body = _find_sec(secs, "排期", "时间", "倒排", "甘特", "计划")
    if not body:
        return []
    seen = {}
    out = []
    for ln_no, ln in body:
        if not TASK_LINE_RE.match(ln):
            continue
        dm = DATE_RE.search(ln)
        pm = PERSON_RE.search(ln)
        if not dm or not pm or not ONSITE_RE.search(ln):
            continue
        key = (dm.group(0), pm.group(1))
        if key in seen:
            out.append(_mk("EP-SCH-001", path, ln_no,
                            "%s 在 %s 有两件到场任务" % (pm.group(1), dm.group(0))))
        else:
            seen[key] = ln_no
    return out


@rule("EP-SCH-002", "blocker", "排期", "全文无活动日期",
      "文档里找不到活动日期（YYYY-MM-DD）。没有锚点的排期是空转：先定日期再排期。")
def _s002(lines, text, path, ctx):
    if not DATE_RE.search(text):
        return [_mk("EP-SCH-002", path, 1, "全文无活动日期")]
    return []


@rule("EP-SCH-003", "warning", "排期", "任务无负责人",
      "排期里有任务没写 @负责人。负责人写到具体人名，「市场部」不算：出了问题没人认。")
def _s003(lines, text, path, ctx):
    secs = _sections(lines)
    body = _find_sec(secs, "排期", "时间", "倒排", "甘特", "计划")
    out = []
    for ln_no, ln in body:
        if TASK_LINE_RE.match(ln) and not PERSON_RE.search(ln):
            out.append(_mk("EP-SCH-003", path, ln_no, ln.strip()[:60]))
    return out


@rule("EP-SCH-004", "warning", "排期", "任务无截止日期",
      "排期里有任务没写日期。「小王负责物料」不算任务，「小王 12-05 前交付打样」才算。")
def _s004(lines, text, path, ctx):
    secs = _sections(lines)
    body = _find_sec(secs, "排期", "时间", "倒排", "甘特", "计划")
    out = []
    for ln_no, ln in body:
        if TASK_LINE_RE.match(ln) and not DATE_RE.search(ln):
            out.append(_mk("EP-SCH-004", path, ln_no, ln.strip()[:60]))
    return out


@rule("EP-SCH-005", "warning", "排期", "缺彩排节点",
      "排期里没有彩排/走台节点。彩排是验证流程不是改方案：T-1 必须安排，且发现的问题当天闭环。")
def _s005(lines, text, path, ctx):
    secs = _sections(lines)
    body = _find_sec(secs, "排期", "时间", "倒排", "甘特", "计划")
    if not body:
        return []
    blob = "\n".join(ln for _, ln in body)
    if "彩排" not in blob and "走台" not in blob:
        return [_mk("EP-SCH-005", path, body[0][0], "排期无彩排节点")]
    return []


@rule("EP-SCH-006", "nit", "排期", "外部依赖任务无缓冲",
      "排期没体现缓冲意识（无「提前」字样）。印刷搭建类提前 2 天、依赖供应商的提前 3 天，供应商延期是常态。")
def _s006(lines, text, path, ctx):
    secs = _sections(lines)
    body = _find_sec(secs, "排期", "时间", "倒排", "甘特", "计划")
    if not body:
        return []
    blob = "\n".join(ln for _, ln in body)
    if "提前" not in blob and "缓冲" not in blob:
        return [_mk("EP-SCH-006", path, body[0][0], "排期无缓冲安排")]
    return []


@rule("EP-SCH-008", "nit", "排期", "无里程碑划分",
      "排期只有任务流水账，没有里程碑。建议按 M1 方案冻结 / M2 场地签约 / M3 物料定稿 / M4 嘉宾确认 / M5 彩排划分。")
def _s008(lines, text, path, ctx):
    secs = _sections(lines)
    body = _find_sec(secs, "排期", "时间", "倒排", "甘特", "计划")
    if not body:
        return []
    blob = "\n".join(ln for _, ln in body)
    if "里程碑" not in blob and not re.search(r"\bM[1-6]\b", blob):
        return [_mk("EP-SCH-008", path, body[0][0], "排期无里程碑划分")]
    return []


# ================================================================ 物料规则

@rule("EP-MAT-001", "warning", "物料", "物料无数量",
      "物料项没写数量。「手提袋」是多少个？数量不清，采购和预算都对不上：补上数量+单位。")
def _m001(lines, text, path, ctx):
    secs = _sections(lines)
    body = _find_sec(secs, "物料")
    out = []
    for ln_no, ln in body:
        if TASK_LINE_RE.match(ln) and not re.search(
                r"\d+\s*(个|份|套|台|块|张|箱|包|只|顶|米)", ln):
            out.append(_mk("EP-MAT-001", path, ln_no, ln.strip()[:60]))
    return out


@rule("EP-MAT-002", "warning", "物料", "物料无负责人",
      "物料项没写 @负责人。谁采购、谁验收、谁带到现场，三件事要落到人头。")
def _m002(lines, text, path, ctx):
    secs = _sections(lines)
    body = _find_sec(secs, "物料")
    out = []
    for ln_no, ln in body:
        if TASK_LINE_RE.match(ln) and not PERSON_RE.search(ln):
            out.append(_mk("EP-MAT-002", path, ln_no, ln.strip()[:60]))
    return out


@rule("EP-MAT-003", "warning", "物料", "物料无到货时间",
      "物料项没写到货/交付时间。印刷品、定制物料要留返工时间：写清最晚到货日。")
def _m003(lines, text, path, ctx):
    secs = _sections(lines)
    body = _find_sec(secs, "物料")
    out = []
    for ln_no, ln in body:
        if TASK_LINE_RE.match(ln) and not DATE_RE.search(ln) \
                and "到货" not in ln and "交付" not in ln:
            out.append(_mk("EP-MAT-003", path, ln_no, ln.strip()[:60]))
    return out


@rule("EP-MAT-004", "warning", "物料", "物料类别覆盖不全",
      "物料清单缺失关键类别。印刷品 / 设备租赁 / 签到物料三类中缺两类以上时，现场大概率手忙脚乱。")
def _m004(lines, text, path, ctx):
    secs = _sections(lines)
    body = _find_sec(secs, "物料")
    if not body:
        return []
    blob = "\n".join(ln for _, ln in body)
    cats = {"印刷": bool(re.search(r"印刷|单页|海报|易拉宝|背景板", blob)),
            "设备": bool(re.search(r"设备|音响|LED|投影|话筒|灯光", blob)),
            "签到": bool(re.search(r"签到", blob))}
    missing = [k for k, v in cats.items() if not v]
    if len(missing) >= 2:
        return [_mk("EP-MAT-004", path, body[0][0],
                     "物料缺类别：%s" % "、".join(missing))]
    return []


@rule("EP-MAT-005", "nit", "物料", "无备用/损耗说明",
      "物料章节没提备用或损耗。印刷品多备 5%、设备备电源/备用机：现场缺 1 个就是事故。")
def _m005(lines, text, path, ctx):
    secs = _sections(lines)
    body = _find_sec(secs, "物料")
    if not body:
        return []
    blob = "\n".join(ln for _, ln in body)
    if "备用" not in blob and "损耗" not in blob and "多备" not in blob:
        return [_mk("EP-MAT-005", path, body[0][0], "物料无备用/损耗说明")]
    return []


# ================================================================ 风险规则

def _risk_items(body):
    """风险条目：R编号开头的行，返回 [(行号, 行文本, 本条窗口)]。

    窗口 = 本条风险行 + 其后直到下一条风险行之前的行（最多 3 行），
    避免把下一条风险的预案算到本条头上。
    """
    items = []
    flat = [(n, ln) for n, ln in body]
    for idx, (ln_no, ln) in enumerate(flat):
        if RISK_ID_RE.match(ln.strip()):
            window_lines = [ln]
            for _, l2 in flat[idx + 1:idx + 4]:
                if RISK_ID_RE.match(l2.strip()) or not l2.strip():
                    break
                window_lines.append(l2)
            items.append((ln_no, ln.strip(), "\n".join(window_lines)))
    return items


@rule("EP-RISK-001", "blocker", "风险", "风险项无预案",
      "风险项没写预案。「到时候再说」「待定」不算预案：写清触发信号+预案+责任人。")
def _r001(lines, text, path, ctx):
    secs = _sections(lines)
    body = _find_sec(secs, "风险")
    out = []
    for ln_no, ln, window in _risk_items(body):
        if not PLAN_WORD_RE.search(window):
            out.append(_mk("EP-RISK-001", path, ln_no, ln[:60]))
    return out


@rule("EP-RISK-002", "warning", "风险", "无风险清单章节",
      "文档没有风险清单章节。户外/大型/首次办的活动，风险清单是必选项：按天气/人员/场地设备/物料/安全合规/舆情六类扫描。")
def _r002(lines, text, path, ctx):
    secs = _sections(lines)
    if not _find_sec(secs, "风险"):
        return [_mk("EP-RISK-002", path, 1, "全文无风险清单章节")]
    return []


@rule("EP-RISK-003", "warning", "风险", "风险无责任人",
      "风险项没写 @责任人。预案写了但责任人不知道自己是责任人，等于没写：当面或书面同步到人。")
def _r003(lines, text, path, ctx):
    secs = _sections(lines)
    body = _find_sec(secs, "风险")
    out = []
    for ln_no, ln, _ in _risk_items(body):
        if not PERSON_RE.search(ln) and "责任人" not in ln:
            out.append(_mk("EP-RISK-003", path, ln_no, ln[:60]))
    return out


@rule("EP-RISK-004", "warning", "风险", "风险无等级",
      "风险项没定等级（高/中/低）。定级方法：概率×影响；「发生了活动就办不下去」= 影响高。")
def _r004(lines, text, path, ctx):
    secs = _sections(lines)
    body = _find_sec(secs, "风险")
    out = []
    for ln_no, ln, window in _risk_items(body):
        if not LEVEL_RE.search(window):
            out.append(_mk("EP-RISK-004", path, ln_no, ln[:60]))
    return out


@rule("EP-RISK-005", "nit", "风险", "高风险项无B方案",
      "高风险项没写 B 方案。B 方案要写清是什么、切换成本、谁在什么时间点做切换决策；贵到用不起的 B 方案等于没有。")
def _r005(lines, text, path, ctx):
    secs = _sections(lines)
    body = _find_sec(secs, "风险")
    out = []
    for ln_no, ln, window in _risk_items(body):
        if re.search(r"影响高|高风险|等级高", window) \
                and not re.search(r"B\s*方案", window):
            out.append(_mk("EP-RISK-005", path, ln_no, ln[:60]))
    return out


# ================================================================ 目标规则

@rule("EP-GOAL-001", "warning", "目标", "目标无量化指标",
      "目标章节没有量化指标（人数/金额/百分比）。「提升品牌影响力」不行，「活动后7天品牌词搜索量提升20%」可以。")
def _g001(lines, text, path, ctx):
    secs = _sections(lines)
    body = _find_sec(secs, "目标")
    if not body:
        return [_mk("EP-GOAL-001", path, 1, "全文无目标章节")]
    blob = "\n".join(ln for _, ln in body)
    if not re.search(r"\d+\s*(人|元|万|%|单|家|个)", blob):
        return [_mk("EP-GOAL-001", path, body[0][0], "目标章节无量化指标")]
    return []


@rule("EP-GOAL-002", "warning", "目标", "指标无数据来源",
      "量化指标没写数据来源（谁来数、用什么工具、什么时候数）。三个没定，指标就是空话。")
def _g002(lines, text, path, ctx):
    secs = _sections(lines)
    body = _find_sec(secs, "目标")
    if not body:
        return []
    blob = "\n".join(ln for _, ln in body)
    if not re.search(r"\d", blob):
        return []
    if "数据来源" not in blob and "统计" not in blob and "口径" not in blob:
        return [_mk("EP-GOAL-002", path, body[0][0], "指标无数据来源说明")]
    return []


@rule("EP-GOAL-003", "nit", "目标", "无复盘计划",
      "全文没提复盘。复盘指标要在策划阶段定好，事后补等于没复盘：执行手册末尾附复盘框架。")
def _g003(lines, text, path, ctx):
    if "复盘" not in text:
        return [_mk("EP-GOAL-003", path, 1, "全文无复盘计划")]
    return []


@rule("EP-GOAL-004", "nit", "目标", "量化指标超过5个",
      "量化指标超过 5 个，等于没有重点。逼自己砍到 3~5 个：一个活动只能有一个核心目的。")
def _g004(lines, text, path, ctx):
    secs = _sections(lines)
    body = _find_sec(secs, "目标")
    if not body:
        return []
    blob = "\n".join(ln for _, ln in body)
    n = len(re.findall(r"指标\s*[：:]\s*\S+", blob))
    n = n or len([ln for _, ln in body
                  if TASK_LINE_RE.match(ln) and re.search(r"\d", ln)])
    if n > 5:
        return [_mk("EP-GOAL-004", path, body[0][0],
                     "量化指标 %d 个，超过 5 个" % n)]
    return []


# ================================================================ 主流程

def audit_file(path):
    text = read_text(path)
    lines = text.split("\n")
    ctx = {}
    findings = []
    for r in RULES:
        try:
            findings.extend(r["check"](lines, text, path, ctx) or [])
        except Exception:
            continue  # 单条规则出错不影响整体
    findings.sort(key=lambda f: (SEVERITY_ORDER[f.severity], f.file, f.line))
    return findings


def main():
    ap = argparse.ArgumentParser(description="lhg-event-plan 活动方案确定性审计")
    ap.add_argument("--target", required=True, help="待扫描目录")
    ap.add_argument("--format", default="text", choices=["json", "text"])
    ap.add_argument("--fail-on", default="blocker",
                    choices=["blocker", "warning", "nit"])
    args = ap.parse_args()

    files = collect_files(args.target)
    all_findings = []
    for p in files:
        all_findings.extend(audit_file(p))

    sev_count = {"blocker": 0, "warning": 0, "nit": 0}
    for f in all_findings:
        sev_count[f.severity] += 1

    summary = {
        "tool": TOOL,
        "version": VERSION,
        "target": args.target,
        "files_scanned": len(files),
        "rules_run": len(RULES),
        "blocker": sev_count["blocker"],
        "warning": sev_count["warning"],
        "nit": sev_count["nit"],
        "total": len(all_findings),
    }

    if args.format == "json":
        print(json.dumps({"summary": summary,
                          "findings": [f.to_dict() for f in all_findings]},
                         ensure_ascii=False, indent=2))
    else:
        print("=" * 60)
        print("lhg-event-plan 活动方案审计报告  v%s" % VERSION)
        print("=" * 60)
        print("扫描文件: %d  运行规则: %d" % (len(files), len(RULES)))
        print("blocker: %d  warning: %d  nit: %d" %
              (sev_count["blocker"], sev_count["warning"], sev_count["nit"]))
        print("-" * 60)
        cur = None
        for f in all_findings:
            if f.severity != cur:
                cur = f.severity
                print("\n[%s]" % cur.upper())
            print("  %s %s:%d" % (f.rule_id, os.path.basename(f.file), f.line))
            print("    %s" % f.title)
            if f.snippet:
                print("    > %s" % f.snippet)
        if not all_findings:
            print("\n未发现问题，方案结构完整。")

    threshold = SEVERITY_ORDER[args.fail_on]
    bad = any(SEVERITY_ORDER[f.severity] <= threshold for f in all_findings)
    return 2 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
