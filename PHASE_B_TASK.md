# Phase B: 复核、风控与操作清单生成

次日开盘一段时间后执行（建议开盘30-60分钟后，避开开盘瞬间的价格失真）。**不下单**——本系统没有接入任何券商程序化下单接口，最后一步永远由人工在券商App完成。

---

## Step 1: 复核

1. 读取 `pending_proposals.jsonl` 中 `stage: "thesis"` 的记录。
2. 对每条记录调用 `market_data.get_quote()` 拿最新开盘后价格，判断今日是否疑似不可交易（成交量异常/报价报错——见 PHASE_A_TASK.md"已知环境适配"关于美股无涨跌停锁死、无可靠免费停牌标志的说明）。
3. 判断论点是否还成立：
   - 价格已大幅偏离 `pct_below_52wk_high` 计算时的基准 → 标记 `thesis_stale`
   - `invalidation_thesis` 描述的情形已经发生 → 标记 `invalidated`，直接归为 `avoid`
   - 疑似不可交易 → 标记 `not_tradeable`，无论论点是否成立都不能操作
   - **周线价格止损**（2026-08-31新增，仅对 `is_held: true` 的持仓生效）：`pending_proposals.jsonl` 该持仓当天记录的 `trend_gate.price_stop_loss_triggered` 为 true 时，标记 `price_stop_loss_triggered`，**无论当天thesis/direction是什么（哪怕是long），强制归为 `sell`**——这是独立于论点判断的价格纪律止损，不需要基本面/技术面thesis本身认输，只要求最近`risk_rules.json`的`trend_gate.price_stop_loss_confirm_days`个交易日收盘价连续低于周线关键支撑位一定比例（见`PHASE_A_TASK.md` 3.1a）。目的是防止"论点还没被正式证伪，但价格已经跌穿关键技术支撑"这种情况被无限期用"再观察观察"拖着不处理。触发时仍然要过Step2的GFV结算守卫——止损判断和"能不能现在卖"是两件事，若因未结算资金被挡下，`not_tradeable_reason`里如实写明"价格止损已触发但资金未结算"。
   - **持仓论文台账检查**（2026-09-11新增，见`PHASE_A_TASK.md` 3.8）：读取该持仓在`thesis_ledger.json`里的条目（没有条目的持仓——比如3.8上线前就已建仓的老持仓——如实标注"尚未建立论文台账"，不强行补建，等下次Phase A遇到触发条件时自然建立）。检查`health_score_history`最新一条：
     - 分数≤2 **且** 触发的红线里有`severity: "fatal"`且`action_if_triggered`是"立即清仓" → 标记`thesis_ledger_fatal_triggered`，**无论当天thesis/direction是什么，强制归为`sell`**，跟价格止损同等地位（两者都是独立于当天论点判断的硬性纪律，不是"再观察观察"）。
     - 分数在3-6之间，或触发了`severity: "severe"`/`"warning"`的红线（未达fatal标准）→ **不强制action**，但必须在操作清单的`reason`里写明健康度分数和具体哪条假设/红线出的问题，交给人工判断是否减仓——这是给人看的信号，不是机械否决，跟现有"只有price_stop_loss强制、其余技术面信号只是disclosure"的设计保持一致。
     - 分数≥7 → 正常记录，不需要额外提示。

## Step 2: 机械风控过滤（不可由AI绕过，规则来自 risk_rules.json）

依次检查，任一不通过则该候选不能进入操作清单的 buy 分支：

1. **单票仓位上限**：`account.max_position_pct_per_symbol`
2. **单日/单周亏损上限**：`account.max_daily_loss_pct` / `account.max_weekly_loss_pct` —— 若已触发，当日/当周剩余时间只允许 `sell`/`hold`，不允许新开仓
3. **最大并发持仓数**：`account.max_concurrent_positions`——**用`watchlist.json`的`held_positions`当前笔数直接比，不能预支同一轮里其他候选"待执行的sell"腾出的名额**（2026-09-10首次真正撞到这条：`held_positions`当时正好等于上限，同一轮里IQ被Step1强制判定sell，理论上执行后会空出1个名额，但那笔sell在这一刻还没有被用户执行——按本仓库最基础的原则"从不假设execution，只认`held_positions`里的真实持仓"，这个名额不能提前算给新开仓候选用。如果同一天用户确认已经手动执行了某笔sell、`held_positions`也已经相应更新，那时候名额才算真的空出来，可以在下一轮Phase B里正常使用；不要在同一轮里因为"清单上有条sell"就把它当成已经腾出的空间。
4. **现金账户 Good Faith Violation 结算守卫**（取代A股版的T+1检查）：`cash_settlement.gfv_guard` 为 true 时，若某代码今天要卖出，先检查买入这笔仓位所用的资金是否已完成结算（美股现行T+1结算周期）——`trade_log.jsonl` 中记录为"用尚未结算的资金买入"的仓位，在资金结算完成前不能卖出，否则触发 Good Faith Violation。判断依据：该仓位的买入交易距今是否已经过了至少1个结算日，且买入资金来源不是"尚未结算的卖出所得"。无法从 `trade_log.jsonl` 确认结算状态时，保守起见按`not_tradeable`处理并标注 `unsettled_funds_gfv_risk`，交给人工核实实际券商账户的结算状态。
   - **若账户后续换成保证金账户**：这一项应替换为 PDT (Pattern Day Trader) 规则检查——净值<$25,000时，5个交易日内不能做超过3次"当日买卖同一票"的日内交易，否则账户被锁交易权限。两条规则**不要同时套用**，取决于 `risk_rules.json` 的 `account.account_type`。
5. **不可交易检查**：Step 1 标记的 `not_tradeable` 直接排除

通过以上所有检查后，`size_pct_of_capital` 按 `risk_rules.json` 的 `position_sizing_by_conviction` 从 Phase A 论点的 `conviction` 直接查表取值（high/medium/low），不是临场估的数字；conviction 到这一步只影响仓位大小，不影响是否通过前面几条机械检查——一份高conviction的论点一样会被仓位/并发数/GFV结算这些红线拦下。

### Step 2b: 仓位偏离提示（只提示不下单）

对每个 `is_held: true` 且 direction 为 `long` 的持仓，计算 `实际仓位% = 持仓股数 × 现价(get_quote) / risk_rules.json的starting_capital_usd`（若资产有变动，用最新总资产），跟 `position_sizing_by_conviction[该票conviction]` 对比：

- **实际仓位明显低于目标**（差距 > 2个百分点）且**今日无新的负面8-K/risk_flags**：在操作清单该条目里加一句"仓位低于目标XXpp，可考虑补仓"的提示，**action 仍然是 `hold`，不要自动改成 `buy`**——因为(1)Step2的机械检查没有专门为"补仓"这个场景重新跑一遍，(2)带 `risk_flags` 的持仓即使仓位低于目标也不提示补仓。
- **实际仓位明显高于目标**（差距 > 2个百分点）：同样只加提示"仓位超出目标XXpp"，不自动生成卖出/减仓建议。

这条规则的目的是**把偏离暴露出来让人判断**，不是自动执行仓位再平衡。

### Step 2c: 部署节奏限制（新增，2026-08-23）

只影响**新开仓**(即`is_held: false`且通过Step2前5项检查的buy候选)，不影响sell/hold，也不改变Phase A论点本身——被这一步拦下的候选下次Phase B自动重新排队，不需要重新调研。

**"今天已经用了几个新开仓名额"这个计数，必须以`watchlist.json`的`held_positions`当天新增的真实代码数为准，不能用`trade_log.jsonl`里同一天更早批次的buy推荐数来算**（2026-09-04教训：同一天跑了第2次Phase B，把当天早些时候的CRCL/HCM两条buy推荐当成"已用掉2个名额"，导致NU被错误挤到defer——但用户从未真的执行那两笔CRCL/HCM买入，`held_positions`里从来没有过这两个代码，名额根本没被真正消耗。trade_log的buy是"推荐"不是"成交"，这条区分本节上面已经写过一次(见下方"周度资金部署上限"那条历史记录)，但在具体执行时被忽略了一次，说明光在文档里提醒一次不够，必须在计数规则本身写清楚计算口径）。每次算"今天还剩几个新开仓名额"时，去查`held_positions`里`date_acquired`是今天的条目有几个，用这个数字，不要数trade_log里今天写过几条buy。

**背景**：单票仓位上限(20%)和并发持仓数上限(10)各自都通过，不代表"今天一次性把所有新论点都按标准仓位买满"是合理的——账户从零持仓起步时，一次性建8个新仓可能占用90%起始资金，且这些仓位会全部锚定在同一天的价格水平上，丧失分批建仓摊薄成本的缓冲。这条规则就是补这个"单条规则都过、但整体节奏过快"的空当。

**机械排序规则**（不是自由裁量，超出限额时按这个顺序砍，砍到符合限额为止）：
1. `conviction`高的排前面（high > medium > low）
2. 同`conviction`内，`technical_signals_triggered`不含`top_reversal`/`bearish_divergence`的排前面（没有活跃技术警告的优先）
3. 同上一档内，`calibration_context.drop5pct_5d`（若存在）数值更低的排前面（统计意义上近期回撤概率更低的优先）
4. 以上都打平时按symbol字母序排列（保证结果可复现，不依赖执行顺序）

**具体检查**：
1. 按上面的排序规则给所有通过Step2前5项的新开仓候选排队。
2. 从队首开始累加，一旦"今日新开仓个数"超过 `execution_pacing.max_new_positions_per_day`，队列里剩下的全部标记`action: "defer"`。

_2026-08-27移除了原第3点(周度资金部署上限`max_capital_deployed_pct_per_week`)：用户反馈只需要"每天不要集中买"这一条约束，不需要额外的周度资金上限——建仓早期真实持仓本来就不多，周度上限容易在持仓集中的几周内把明显合理的候选也一起拦掉。`risk_rules.json`的`execution_pacing`已同步移除这个字段。历史教训仍然保留在这里供参考：这条规则存在期间，`trade_log.jsonl`的buy记录不能当"已部署仓位"的依据——那只是Phase B生成的推荐，系统不连券商不知道用户是否真的执行了；唯一可信的真实持仓来源永远是`watchlist.json`的`held_positions`。以后如果重新引入类似的资金层面节奏限制，务必按这条教训以`held_positions`为准，不要用trade_log的推荐记录。_

`defer`的候选仍然要写进操作清单，`reason`里说明"论点和风控都通过，本轮因部署节奏限制延后"，不是"论点有问题"。

## Step 3: 生成操作清单

对通过复核和风控检查的候选，生成清单条目：

```jsonc
{
  "date": "2026-08-24",
  "symbol": "AAPL",
  "action": "buy",                 // buy | sell | hold | avoid | defer
  "suggested_price_range": [225, 230],
  "size_pct_of_capital": 0.08,
  "reason": "复核后论点仍成立的一句话摘要",
  "risk_flags": [],
  "conviction": "medium",
  "not_tradeable_reason": null,    // 若因疑似停牌/GFV结算风险/风控被排除，写明原因
  "position_deviation_note": null, // Step2b结果：低于/高于目标仓位多少pp，只提示不代表action会自动变成buy/sell
  "pacing_deferred_reason": null,  // 2026-08-23新增，action为defer时说明原因；2026-08-27起只会是max_new_positions_per_day(周度资金上限已移除)
  "price_stop_loss_triggered": false, // 2026-08-31新增，true时action强制为sell，与当天thesis/direction无关，见Step1
  "thesis_ledger_check": null       // 2026-09-11新增，见Step1；{"health_score": 8, "fatal_triggered": false, "note": "..."} 或 null(尚未建立台账)
}
```

`avoid`/`not_tradeable`/`defer` 的条目也要写进清单并说明理由——这是审计轨迹的一部分，不能只记录"通过"的。`defer`和`avoid`的区别：`avoid`是论点本身或风控判断不该买，`defer`是论点和风控都通过、纯粹因为部署节奏被延后，语义不同不要混用。

## Step 4: 输出

1. 追加写入 `trade_log.jsonl`（append-only，不覆盖），每条记录都要包含 Step 2 各项检查的通过/拒绝结果，不只是最终结论。
2. git add + commit（message 格式：`Phase B YYYY-MM-DD`）。**不 push 到任何下单渠道，不调用任何券商API。**
3. 提醒：`execution.mode` 目前是什么值都不影响本步骤行为——本系统设计上完全不连接任何券商API，操作清单生成后需要人工复核并手动在券商App执行。
