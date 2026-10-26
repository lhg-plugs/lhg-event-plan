<!-- About 简介（推送后填写到仓库 About 栏）：
小白一句话→完整活动方案：目标拆解、预算编制、排期甘特、风险清单、物料核查、复盘报告一条龙 ｜ 一键安装：/plugin marketplace add lhg-plugs/lhg-event-plan
-->
<!-- topics（推送后打）：claude-code, claude-plugin, agent-skills, event-planning, conference, marketing -->

# lhg-event-plan：活动策划专家

> **一句话介绍**：给 AI 助手装上"活动策划全流程"——目标拆解、预算编制、排期甘特、风险清单、物料核查、执行手册、复盘报告，筹备期一条龙，小白也能办出专业水准的活动。
>
> **一键安装**：在 Claude Code 里运行
> ```
> /plugin marketplace add lhg-plugs/lhg-event-plan
> /plugin install lhg-event-plan@lhg-plugs
> ```

## 它是什么

`lhg-event-plan` 是一个 Claude Code 插件（plugin 为主、skill 为辅）：

- **3 个命令**（小白入口）：`event-plan` 策划一条龙（目标→预算→排期→风险→执行手册），`event-check` 筹备期自查，`event-postmortem` 复盘报告生成器
- **5 个平台中立 skill**（方法论，可移植到豆包/Workbuddy 等 agent 平台）：目标拆解、预算编制、排期甘特、风险清单、复盘方法论
- **2 个专家 agent**：活动策划总监、独立审查员
- **2 个硬门 hooks**：commit 前活动方案审计拦截、写方案时预算求和/风险预案提醒（均可一键豁免）
- **30 条确定性审计规则**：纯 Python、零第三方依赖，查预算求和、时间冲突、物料遗漏、风险预案缺失

差异化：**预算求和机器校验**（分项加起来≠合计直接 blocker，差一分钱都要说明去向）、**时间冲突检测**（同一人同一天两件到场事自动报警）、**风险项无预案=没写**（"待定""到时候再说"一律打回）、**复盘指标策划期先定**（事后补指标等于没复盘）。

## 3 个命令用法

| 命令 | 作用 | 示例 |
|---|---|---|
| `/lhg-event-plan:event-plan` | 目标→预算→排期→风险→物料→执行手册→复盘框架，一条龙输出 | `/lhg-event-plan:event-plan "200人线下发布会" 2026-12-20 10万` |
| `/lhg-event-plan:event-check` | 筹备期自查：预算超支/时间冲突/物料遗漏/风险预案四类硬伤 | `/lhg-event-plan:event-check 方案目录` |
| `/lhg-event-plan:event-postmortem` | 复盘报告：指标对照→预算复盘→得失清单→下次改进清单 | `/lhg-event-plan:event-postmortem 方案目录 "到场173人"` |

## 自查说明

自查 = 确定性脚本（机器）+ 人工评审（人）：

1. 先跑 `scripts/audit/event_audit.py`（30 条规则），零 LLM 成本；
2. 发现分三级：**blocker**（预算求和不一致、时间冲突、风险无预案、活动日期未定，commit 前 hook 会拦截）、**warning**（无应急预留、物料无负责人、目标无量化指标等）、**nit**（无支付节点、超 5 个指标等洁癖项）；
3. 再按人工清单过一遍脚本抓不到的（比过价吗？预案演练过吗？责任人知不知道自己是责任人？）。

blocker 清零才算做完；为清零改规则算作弊，不许。筹备期自查至少跑三遍：方案初稿、执行前一周、执行前一天。

## 目录结构

```
lhg-event-plan/
├── .claude-plugin/          # plugin.json（插件清单）+ marketplace.json（本仓即市场）
├── commands/                # 3 个命令：event-plan / event-check / event-postmortem
├── skills/                  # 5 个平台中立 skill（goal/budget/schedule/risk/postmortem）
├── agents/                  # 2 个专家 agent（event-architect / event-reviewer）
├── hooks/                   # 2 个硬门（commit 前审计拦截 / 预算求和与风险预案提醒）+ 豁免说明
├── scripts/audit/           # 30 条确定性审计规则（零第三方依赖）
├── fixtures/                # 冒烟测试用例（坏/好对照）
├── smoke_test.py            # 一键冒烟测试
├── ATTRIBUTION.md           # 灵感来源与许可声明
└── LICENSE                  # MIT
```

## 什么时候用 / 什么时候不用（FAQ）

**什么时候用：**

- 你要办一场发布会/路演/团建/招商会，不知道从哪下手时 → 用 `/lhg-event-plan:event-plan` 一条龙
- 活动方案写完了，怕预算算错、时间撞车、风险没预案这类硬伤时 → 跑 `/lhg-event-plan:event-check`
- 活动办完了，想把经验沉淀成下次可用的复盘报告时 → 用 `/lhg-event-plan:event-postmortem`
- 你是行政/市场负责人，想给团队立一套"方案提交前必须自查"的纪律时 → 它的 hooks 硬门开箱即用

**什么时候不用：**

- 你只是组织一次 10 人以内的内部小会、无外部支出 → 杀鸡不用牛刀，口头对一下即可
- 活动日期还没定、也不打算定 → 没有锚点的排期是空转，先定日期再用它
- 你希望 AI 无条件按你的数字出预算、不接受"分项求和≠合计"的打回 → 它的预算零容忍会让你烦（这是设计，不是 bug）

## lhg-plugs 插件矩阵

| 插件 | 一句话 | 安装 |
|---|---|---|
| [lhg-event-plan](https://github.com/lhg-plugs/lhg-event-plan) | 活动策划专家：目标→预算/排期/风险清单→执行手册→复盘报告，筹备期一条龙 | `/plugin install lhg-event-plan@lhg-plugs` |
| *（后续插件在此追加，保持表格格式一致）* | | |

## 自检与反馈

本插件自带质检：每次使用后可跑 `python3 smoke_test.py` 一键验证（JSON 合法、skill/command/agent 完整性、30 条规则、fixtures 命中、hooks 行为）。

发现 bug 或有改进建议，以 `[QC] <一句话问题>` 为标题提交到 https://github.com/lhg-plugs/lhg-event-plan/issues，正文写清"复现步骤 → 实际表现 → 期望表现"。

## 出品：刘洪光

本插件由真人出镜 IP「刘洪光」（安徽合肥）出品，归属 [lhg-skills](https://github.com/lhg-skills)。

- GitHub 主页：https://github.com/lhg-skills —— 全部 skill 开源在此，欢迎 star
- 抖音：搜「刘老师讲AI实战」
- 微信：lhgsmsw
