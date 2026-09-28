# Phase A: 筛选与投研论点生成

交易日收盘后执行。只做筛选、抓信号、写投研论点。**不下单，不生成操作清单，不做风控执行**——那些是 Phase B 的事。

本文件是 source of truth。`risk_rules.json` 里的数值可以改，但本文件描述的步骤顺序和判断逻辑不应被跳过或简化。

所有数据调用使用 `~/.claude/skills/us-stock-data/SKILL.md` 里定义的函数，调用方式：

```bash
PYTHONPATH=~/.claude/skills/us-stock-data ~/.claude/skills/us-stock-data/.venv/bin/python3 -c "..."
```

需要先 `export SEC_EDGAR_CONTACT="Your Name your.email@example.com"`（SEC要求，否则8-K相关调用会403）。

## 已知环境适配（2026-08-23首次验证记录，详见 us-stock-data 的 SKILL.md）

- 三层数据（yfinance行情/基本面/内部人交易/机构持仓/分析师评级/新闻，SEC EDGAR 8-K/全文检索，Yahoo补充扫描）已用 AAPL(大盘)/DUOL(中盘)/RDDT(近期IPO小盘) 三只真实标的验证通过，全部拿到真实数据。
- `screener.run_supplementary_scan()` 已知会偶发失败（yfinance screener端点历史上不稳定），失败时返回空列表——本轮 Step 1 补充扫描没有候选时，视为正常情况，不中断整轮筛选，只用 watchlist 候选继续跑。
- 美股**没有**A股那种"涨跌停锁死"机制，也没有可靠的免费"是否停牌"实时标志——`market_data.get_quote()` 返回的成交量/价格如果明显异常（比如量是0），当作"疑似不可交易"的粗略信号去人工复核，不是确定性判断。
- `sec_edgar.get_recent_8k_filings()` 查不到结果时，先确认该ticker不是纯OTC/粉单股（`sec_edgar.get_cik()` 会抛 `KeyError`）——这类票本来就该被 `universe.exclude_otc_pink_sheet` 过滤掉。

---

## Step 1: 构建候选池

1. 读取 `watchlist.json` 的 `symbols` 和 `held_positions`。
2. 读取 `risk_rules.json` 的 `universe.supplementary_scan_type`（**2026-08-24起是列表，不是单个字符串**）——对列表里每个scan_type分别调用 `screener.run_supplementary_scan(scan_type, limit=risk_rules.universe.supplementary_scan_max_candidates)`，每个类型各拿一批，按symbol合并去重后作为补充候选池（不对合并后的池子再截一次）。任一类型失败/返回空列表按上面"已知环境适配"处理，不中断，其余类型正常跑。**每个候选要记录它是从哪个scan_type来的**(一个symbol被多个scan_type同时命中时，记录全部来源)，Step 2a要用到这个信息。
3. 对每个候选（watchlist + 补充扫描）调用：
   - `market_data.get_fundamentals(ticker)` 拿市值/行业/板块
   - `market_data.get_quote(ticker)` 拿现价/前收/成交量
   - `sec_edgar.get_cik(ticker)` 判断是否为SEC注册公司（`KeyError` → 疑似OTC/粉单）
4. 机械过滤（顺序执行，不可跳过）：
   - 市值超过 `universe.max_market_cap_usd` → 剔除
   - 价格低于 `universe.penny_stock_price_threshold_usd` → 剔除
   - `universe.exclude_otc_pink_sheet` 为 true 时，`sec_edgar.get_cik()` 抛出 `KeyError` → 剔除
   - 上市天数不足 `universe.exclude_new_stock_days`（用 `get_YFin_data_online` 最早一条记录的日期估算首次可得数据的天数，做不到精确IPO日期时用这个近似）→ 剔除
   - 交易所命中 `universe.exclude_exchanges` → 剔除
   - `get_quote()` 返回明显异常（如 `error` 字段存在，或 volume 长期为0）→ 剔除
5. 候选数量分别按 `universe.watchlist_max_candidates` / `universe.supplementary_scan_max_candidates` 截断。
6. `held_positions` 里的代码**无论是否命中过滤规则或超过数量上限，都必须保留在最终候选列表里**，并标记 `is_held: true`。

**这一步的候选来源目前只有两条：`watchlist.json`手动维护 + `screener.run_supplementary_scan()`的动量类预置筛选，两条都是被动来源**——本流程不解决"想系统性调研某个行业/主题下有哪些值得关注的美股"这种主动发现需求，这类需求走文末"可选：行业主题候选发现"，不是每轮Phase A自动跑的部分。

## Step 2: 抓信号

对 Step 1 得到的每个候选：

### 2a. 常规信号（价格/量能）

1. 调用 `market_data.get_YFin_data_online(symbol, start, end)` 拿至少60个自然日的日K。
2. 计算三项：
   - 60日涨跌幅 = `abs(latest_close - close_60d_ago) / close_60d_ago`
   - 量比 = `latest_volume / avg_volume_30d`
   - 52周极值距离 = `min((high_52w - price)/high_52w, (price - low_52w)/low_52w)`（52周高低可从 `get_fundamentals()` 的 `52 Week High`/`52 Week Low` 字段直接读，不用重新算）
3. 任一指标超过 `signal_thresholds` 对应阈值 → 触发信号，进入 Step 3。
4. 同时检查 `get_quote()` 是否有异常（见上）——若疑似不可交易，标记但**不触发深入研究**，仅在 summary 里记录原因。
5. **基本面/估值来源的候选绕开这道价格/量能门槛**（2026-09-04新增）：候选如果来自`universe.fundamentals_sourced_scan_types`(见`risk_rules.json`，目前是`["undervalued_growth"]`)列出的scan_type，**即使1-3项价格/量能信号全部未触发，也直接进入Step 3**，不需要等它先动了才研究——这类候选本来就是Yahoo按PEG/成长性等基本面指标筛出来的，要求它"同时还要有价格动量"等于是把所有靠基本面筛出来、还没被市场发现的票全部过滤掉，反而只剩下清一色"已经涨过一截"的候选，这是2026-09-04用户发现的真实筛选偏差("筛选出来的新股票都有一个特征就是在前几天出现过大幅跳涨")的根因——Step 2d的调研预算排序本身就是按"偏离阈值程度"打分，隐含地优先研究涨得最多的候选，如果Step 2a又要求所有候选必须先触发价格信号，系统结构上就没有渠道能发现"基本面变好但股价还没涨"的机会。跟`held_positions`/2e自选股首次调研一样，这条豁免的候选**不占用`signal_triggered_research_budget`**，每轮都必须出新论点（避免它们又在2d的动量打分里被挤掉，变相回到老问题）。

### 2b. 趋势信号

1. 读取 `sentiment_history.jsonl`，取该候选过去 `trend_tracking.lookback_days` 天的快照（机构持仓占比、分析师评级分布）。若历史记录不足 lookback_days 天，跳过此项（不算失败）。
2. 计算：
   - 机构持仓变化 = `(今日机构持仓占比总和 - N天前机构持仓占比总和) / N天前机构持仓占比总和`（用 `market_data.get_institutional_holders()` 的 `pctHeld` 列求和）
   - 评级档位变化 = 今日 vs N天前 `market_data.get_analyst_recommendations()` 的评级分布（strongBuy/buy 占比上升记正档位，下降记负档位）
3. 任一指标超过 `trend_tracking` 对应阈值 → 即使 2a 未触发，也计入待研究候选（捕捉价格还没体现但机构/分析师态度正在转向的票）。

### 2c. 收尾

无论候选是否触发信号，都把它今日的快照（`institutional_holding_pct_total`、`analyst_rating_distribution`、`short_pct_of_float`）追加写入 `sentiment_history.jsonl`（这一步在 Step 4 最后统一做，避免同轮内重复写入）。

### 2d. 调研预算分配

若触发信号的候选数超过 `universe.signal_triggered_research_budget`（2026-08-28新增的显式字段，默认10；此前是Step2d没写进配置的隐性"前6"惯例，候选多的时候太少，改成显式可调的10），按"偏离阈值程度"打分排优先级，优先研究分数最高的前N个（N=该字段值），排不进去的标记`screened`并注明"deprioritized_by_budget"，不是论点判断：
- 价格信号：实际值 ÷ 阈值
- 趋势信号：实际值 ÷ 阈值
- 52周极值信号：阈值 ÷ 实际距离（越接近极值分数越高）

`held_positions`、2e 规则下"首次强制调研"的自选股、以及2a第5点"基本面来源豁免"的候选都不占用这个预算，每轮都必须出新论点；`held_positions` 额外检查近90天内 `sec_edgar.get_recent_filings(ticker, forms=["8-K"])` 有无新增重大事件公告。除此之外**普通自选股(已建立过baseline、非持仓)触发信号后和补充扫描候选适用同一套预算排序，不因为是自选股就豁免**——2026-08-27的教训：不能悄悄给"更关心的候选"开后门，机械规则要对所有非豁免候选一视同仁。

### 2e. 自选股首次强制深度调研

读取 `researched_baseline.json`（`{symbol: date_first_researched}`）。`watchlist.json` 里的 `symbols` 中，任何一个**不在**这个文件里的代码，本轮**无条件进入 Step 3**，不受 `signal_thresholds` 限制——这是给新加入自选股的票建立基线论点，跟"有没有触发信号"无关。跟 `held_positions` 一样不占用调研预算。

Step 4 完成该代码的论点后，把它连同今天的日期写入 `researched_baseline.json`（一次性写入，之后同一代码不会再被这条规则强制触发，除非从 `researched_baseline.json` 里手动删除）。

## Step 3: 生成投研论点

对每个进入本步骤的候选，依次做四轮**独立**推理（不要合并成一步输出，每轮只依据该轮指定的数据）：

**信息丰富度分级（新增，2026-09-10，吸收开源项目[ai-berkshire](https://github.com/xbtlin/ai-berkshire)"四大师方法论"skill的思路，只借检查清单本身，不借它的多Agent辩论架构）**：正式进入3.1前，先给候选打一个信息丰富度标签，决定后续审查的重点，写进thesis开头一句话，不占用额外字段：
- **A级**（大盘股/覆盖多年/分析师覆盖充分，比如45位以上分析师覆盖的MU/NVDA/GOOGL这类）：这类候选信息不缺，缺的是反面视角。3.5综合裁决时必须额外回答"如果这个判断这么可信，为什么市场/分析师自己的分歧还这么大（比如目标价区间、一致预期区间跨度）"，不能只罗列支持证据。
- **B级**（信息适中，多数watchlist候选属于这档）：正常走3.1-3.6流程，但thesis里推算/估计出来的数字(比如3.3b的分位桶判断、3.2的营收增速推断)要标出"直接读取自数据源"还是"自己推算/填补"，不能让两者读起来一样确定。
- **C级**（次新股/覆盖薄，比如上市不到一年、`trend_regime`为`insufficient_history`、或`signal_calibration.json`/`market_calibration.json`所属行业未覆盖的候选）：套用A/B级的常规分析框架容易产生虚假的确定感——历史数据本来就不够，硬套框架只是让判断看起来更有把握，不是真的更可靠。3.2改用第一性原理提问："客户是谁、为什么付钱、有没有替代选择？复购靠什么驱动？竞争对手拿一大笔钱能不能复制这门生意？管理层做过什么关键决策、反映了什么判断力？"——这几个问题答不出来，如实写"信息不足，无法判断"，不要凭同类公司的经验硬填。

### 3.1 技术面视角

数据：`market_data.get_YFin_data_online()`、`market_data.get_stock_stats_indicators_window()`（rsi/macd/boll等）、`market_data.get_quote()`、**`technical_signals.check_all_signals(symbol, curr_date)`**、**`pattern_signals.check_double_bottom(symbol, curr_date)`和`pattern_signals.check_head_and_shoulders_bottom(symbol, curr_date)`**（2026-09-08新增，见下方说明）。
只依据价格/量能/技术指标给一句判断，不引入基本面信息。

**必须先跑八个共振信号再下判断，不是自由描述图形**：`check_all_signals()`返回上车/下车/启动/爆发/顶部反转/顶背离六个信号，加上`double_bottom`(双重底)/`head_and_shoulders_bottom`(头肩底)这两个K线形态信号，各自的triggered状态+具体证据(交叉日期、波峰波谷数值)。这套信号的精确定义(RSI白/黄/紫=RSI6/12/24、"零轴附近"怎么量化、"最近才形成"的时间窗口等)全部写在`technical_signals.py`/`pattern_signals.py`的模块docstring里，执行前先读一遍，不要凭记忆假设定义。

- 八个信号里只要有任意一个triggered，必须写进3.1的判断里，并把`evidence`里的关键数值(比如顶部反转信号的RSI双头两个峰值、颈线是否跌破；双重底/头肩底的具体低点日期数值、颈线位置)带出来，不能只写"触发了顶部反转信号"这种空话。
- 八个信号全部False是正常情况(这套信号本来就该是相对少见的强确认信号)，此时3.1就退回到读RSI/MACD/均线的当前数值和方向做定性判断，和之前一样。
- 顶部反转/顶背离(图5/图6)属于反方证据来源，即使3.2/3.3的基本面证据很强，只要这两个信号触发了，3.5综合裁决时必须把它们计入"最强反方证据"的候选，不能因为基本面好看就略过技术面的警告。
- **双重底/头肩底触发时不能单独把conviction拉到high**，逻辑跟`trend_regime`的`strong_aligned`一样——3.5的high标准硬性要求"公司自己在8-K中公开确认的具体催化事件"，技术面形态再扎实也不能替代这一条，只能作为支持证据写进thesis正文。

**entry信号触发时，必须额外报告价格是否已经跌破周线支撑位，不能只当作单纯的看多确认**（2026-09-08新增，2026-09-04至09-08七轮回测发现的核心问题）：186只股票1年+5年两个窗口的历史回测反复证实，`entry`信号(纯日线级别MA/MACD/RSI短期对齐)完全不检查长周期(26-30周)支撑位置，**过去1年的entry触发案例里51.04%买入时价格实际已经跌破了周线支撑位**——这类"下跌中继假摔"买入后续触发止损的概率和亏损幅度都明显更差(止损段胜率仅32.77%，PF仅0.30，系统性亏钱)。所以`entry`信号触发时，3.1判断里必须同时引用`check_trend_gate()`的`pct_below_weekly_support`：如果该值≥0(即价格已经在支撑位下方)，必须在thesis里明确标注"entry信号触发但价格已跌破周线支撑位，技术面警告"，不能只把entry当成正面确认写进去；`double_bottom`/头肩底这两个新信号因为本身要求确认过的结构性反转，回测显示"买入时已破位"的比例明显更低(35.4%，比entry低约16个百分点)，相对更可信，但同样要报告这个数值，不要因为形态好看就跳过检查。

**触发的信号必须带出历史概率，不能只报告"识别到了什么"**（2026-09-04新增，用户原话："如果识别了这种信号，要告诉我根据历史规律，后面的涨跌情况啊，而不是只是告诉我识别了什么"）：读取`signal_calibration.json`（2026-09-03构建，方法论见文件本身的`methodology_note`——分行业统计，5年历史，`entry`/`exit`/`launch`/`explosion`/`top_reversal`/`bearish_divergence`六个信号在该候选所属行业(`industry.get_industry_snapshot()`返回的`industry`字段，需要精确匹配到`signal_calibration.json`的`industries`键，比如"Software - Application"≠"Software - Infrastructure"，不能因为名字像就当作同一个)触发后，未来1/3/5/10个交易日的涨跌幅分布(6个区间的经验概率+均值/中位数)）。任一信号triggered时，在thesis里带出该信号在该候选所属行业的历史统计(至少报5日和10日两个窗口)；`n_triggers`带了`low_sample_warning`的要如实说明样本量小、统计意义有限；该候选所属行业根本不在`signal_calibration.json`的6个已覆盖行业里时，如实写"该行业未覆盖，无历史统计"，不要套用别的行业的数字硬凑——2026-09-04已验证：顶背离在Computer Hardware/Communication Equipment这两个行业触发后，未来5日实际继续上涨10%+的概率(17.1%/12.9%)反而比下跌10%+的概率(7.6%/3.5%)更高，跟"顶背离=看空"的直觉相反，这正是为什么必须把真实历史数据带出来、不能只凭信号名字和直觉下判断的原因。

`signal_calibration.json`覆盖`technical_signals.py`的六个信号+`pattern_signals.py`的全部11个K线形态(含本节新接入的`double_bottom`/`head_and_shoulders_bottom`)，历史统计引用规则同样适用于这两个新信号。`pattern_signals.py`其余9个形态(平台突破/杯柄形态/红三兵/上升三角形/下降楔形/口袋支点/看涨旗形/底部直升机/月线反转)**依然不接入本流程**——2026-09-04至09-08的回测只针对`double_bottom`/`head_and_shoulders_bottom`做过多轮验证(1年/5年窗口、跟entry信号横向对比、"买入时已跌破支撑位"诊断指标)，其余9个形态还没有类似的验证基础，3.1只调用这两个，不要调用`pattern_signals.py`里的其他函数，等后续单独验证再考虑接入。

### 3.1a 月线+周线趋势闸门（新增，2026-08-31）

数据：**`technical_signals.check_trend_gate(symbol, curr_date, price_stop_loss_pct_below_support=risk_rules.json的trend_gate.price_stop_loss_pct_below_support, price_stop_loss_confirm_days=risk_rules.json的trend_gate.price_stop_loss_confirm_days)`**（用现有`get_YFin_data_online`已缓存的5年日K resample成月K/周K，不需要额外数据源）。**必须显式传这两个参数，不能只写`check_trend_gate(symbol, curr_date)`**——函数自己的默认值(`price_stop_loss_confirm_days=3`)是旧的，2026-09-08已经在`risk_rules.json`里改成5天确认，但函数签名的默认值没有同步改，两边会静默不一致（2026-09-09复盘时发现这个缺口：本节这行指令过去一直没写传参，等于每次都在用函数的旧默认值3天，除非执行者自己留意到两边对不上、临时手动传参更正——不能依赖这种运气，这次已经把显式传参写进指令本身）。

用户提供了一套小红书上的"MACD六周期共振战法"截图，核心理念是"大周期定方向、小周期找点位，顺序不能倒"。这里只吸收其中两块可机械化、且和本系统现有节奏(隔日执行的swing/position trading，不是日内交易)吻合的部分——**月线+周线的MACD零轴趋势闸门**，60分钟/15分钟/5分钟那三层日内精细化点位不在本系统范围内，不用管。

`check_trend_gate()`返回`trend_regime`，四档（外加次新股的`insufficient_history`）：
- `strong_aligned`：月线MACD零轴上方(多头) + 周线金叉——两个大周期共振向上
- `pullback`：月多头 + 周死叉——短期回调，趋势本身没坏
- `weak_rebound`：月线MACD零轴下方(空头) + 周金叉——弱势反弹，不代表长期趋势已经反转
- `bearish_confirmed`：月空头 + 周死叉——月周共振向下

**这个闸门是conviction的封顶机制，不直接决定direction**（保持3.6"conviction不决定direction"的既有分工，避免变成纯技术流）：
- `trend_regime`为`bearish_confirmed`或`weak_rebound`时，conviction本轮最高只能到`medium`，即使3.2-3.5的其余证据满足high的其他条件也不能给high。
- `trend_regime`本身连同`evidence`里的月/周MACD数值，计入3.5"最强反方证据"的候选池，跟顶部反转/顶背离同等地位——不能因为基本面证据好看就略过月周共振向下的警告。
- `strong_aligned`不能单独把conviction拉到high（3.5的high标准里"公司自己在8-K中公开确认的具体催化事件"这一条硬性要求不因技术面强势而豁免），但可以作为支持证据写进thesis正文。

同时`check_trend_gate()`会算出**周线关键支撑位**(`weekly_support_level`)和当前价格相对它的偏离(`pct_below_weekly_support`)——这两个数字所有候选都要算（不只是持仓），写进Step4输出的`trend_gate`字段，供Phase B做价格止损判断用（见PHASE_B_TASK.md）。次新股`trend_regime`为`insufficient_history`时如实写"月/周历史不足，跳过趋势闸门判断"，不要硬凑。

### 3.2 基本面/公告视角

数据：`sec_edgar.get_recent_8k_filings()`、`market_data.get_fundamentals()`、`market_data.get_balance_sheet()`/`get_cashflow()`/`get_income_statement()`、`market_data.get_analyst_recommendations()`。
只依据公司公告/财报/机构评级给一句判断。**这是判断 conviction 是否够 high 的主要依据**——催化事件必须能在 `sec_edgar.get_recent_8k_filings()` 里找到对应的8-K公告（并核对 `items` 字段确实对应该事件类型），不能只是市场传闻。

**8-K抓取深度**：`limit` 至少取10-15条，不能只抓最近3-5条——重大但稍早的负面事件（比如几个月前的高管离职5.02或诉讼相关披露）容易被最近的财报8-K(2.02)淹没在前几条里。

**`[READ REQUIRED]`标记必须真的去读，不能只看items编号就当作确认了催化事件**（2026-08-23教训）：`get_recent_8k_filings()`输出里凡是标了这个标记的行，说明items包含`sec_edgar.ITEMS_REQUIRING_FULL_READ`里的编号（`1.01`签重大协议/`2.03`产生新财务义务/`8.01`其他重大事项等）——这些编号本身**不透露具体是什么事**，之前吃过亏：NVDA一条`1.01,2.03,7.01`的8-K被当成"催化事件已确认"直接跳过没读，实际内容是NVIDIA为OpenAI在俄亥俄数据中心项目的租约做了**最高1050亿美元的剩余价值担保**（OpenAI违约NVIDIA要代付）——这种规模的或有负债信息，光看items编号完全看不出来。

看到`[READ REQUIRED]`标记，调用 `sec_edgar.get_filing_text(url)` 把原文读一遍再写thesis。不带这个标记的行(比如`2.02`财报、`5.02`高管变动、`5.07`股东投票结果)不用逐条打开，编号本身已经说明是什么事，把研究预算留给真正需要读的那几条。

**机构评级**：用 `get_analyst_recommendations()` 的评级分布 + 最近评级变化方向作为主要定量参考，而不是价格目标——同样存在"目标价覆盖率不稳定"的问题（不同分析师、不同时点覆盖率差异很大），评级方向（升级/维持/降级）比具体目标价数字更可靠。若近3个月无评级变化，如实写"近期无新增评级变化"。

**生意本质与管理层评估（新增，2026-09-10，吸收[ai-berkshire](https://github.com/xbtlin/ai-berkshire)"段永平"方法论skill）**：3.2目前的写法是读到什么写什么，容易漏掉关键维度。**conviction达到medium及以上的候选**，3.2里必须额外覆盖两块结构化内容（哪个维度数据缺失就如实写"未覆盖"，不能悄悄跳过不提）：
- **生意本质**：一句话说清楚这门生意是什么；收入结构(按产品/地区拆分，能拆多细拆多细)；毛利率水平和趋势，能拿到同业数据(`industry.compare_peer_returns()`)时跟同业比、解释差异原因；是一次性销售还是订阅/复购驱动的生意；如果CEO明天退休，这家公司的竞争力还剩多少(判断护城河在不在"人"身上，不在产品/网络效应/规模这类结构性因素上)。
- **管理层评估**：`get_insider_transactions()`能看到的关键决策(并购/回购/重大资本开支)——时间、决策内容、目前看结果如何；股东一致性——管理层持股比例、近期是买入还是卖出、卖出是不是10b5-1计划内的常规操作还是计划外抛售(3.3已经在看这两个数字，这里要求把它们跟资本配置记录放在一起综合评价，不是分开孤立看)。

### 3.2a 质量去劣闸门（机械前置，新增2026-09-11）

数据：`fundamentals_analysis.get_quality_screen(symbol)`，方法论改写自[ai-berkshire](https://github.com/xbtlin/ai-berkshire)的`skills/quality-screen.md`（不是照搬代码——原skill假设WebSearch拿10年数据，这里改用yfinance年度三大报表结构化取数，函数实现细节见`us-stock-data`的`SKILL.md`）。

**这一步对所有走到Step3的候选都要跑一遍，不只是long方向**——和3.1a技术面闸门是对称结构：3.1a是技术面的机械前置检查，3.2a是基本面的机械前置检查，两者都是"封顶/披露机制"，不是自动一票否决机制。

7条硬指标：10年平均ROE≥8%、5年累计FCF≥0、利息覆盖倍数(EBIT/利息)≥2倍、毛利率≥15%、经营现金流/净利润(5年均值)≥0.7、净利率≥5%、5年股本膨胀≤20%。

- `get_quality_screen()`返回的`years_used`**必须原样写进thesis**，不能只展示pass/fail结论——yfinance免费年度报表最多约5年，不是原方法论要求的10年，`years_used<3`时这一整套结论只能标"样本不足，仅供参考"。
- `overall`为`fail`(未通过且不满足任何豁免条件)时，**conviction本轮最高只能到medium**，跟3.1a的`bearish_confirmed`/`weak_rebound`封顶规则同等地位，即使3.2-3.5其余证据满足high的条件也不能给high；`overall`为`exempt_candidate`时，thesis里必须写清楚满足的是A(战略投入期)还是B(主动低利润率)哪条豁免、具体数值支撑，不能只写"豁免通过"四个字。exemption_c(会员制/高周转薄利模式)函数本身判不了，需要3.2"生意本质"那段的文字判断来确认是否适用，适用时同样按`exempt_candidate`处理。
- **已知两类数据失真，遇到时要在thesis里加一句说明，不是被数字唬住直接下结论**：①`avg_roe`异常高(>50%)时大概率是股东权益被大额回购压得很低导致分母失真(AAPL实测167%)，不代表真实资本效率有这么夸张，参考价值有限；②`years_used`覆盖到公司IPO前后时，历史科目(尤其净利率/利息覆盖)可能被IPO相关一次性股权激励费用等因素拉得很难看(CRCL实测净利率-18.6%、利息覆盖-82.9x)，需要交叉参照信息丰富度分级(C级候选本来就该对这类数字更谨慎)。
- 这一步的目的是"排除确定不好的公司"，不是"证明这家公司好"——通过闸门不代表conviction应该高，只是没有被这7条硬指标直接排除。

### 3.2b 资产负债表异常扫描（机械前置，新增2026-09-11）

数据：`fundamentals_analysis.get_balance_sheet_anomalies(symbol)`，方法论改写自ai-berkshire的`skills/earnings-review.md`第4.2节"异常信号检测"。

检查四类红旗：应收账款增速>营收增速(可能在塞渠道)、存货增速>营收增速(可能在压库存)、经营现金流/净利润比值同比转差(利润质量下降)、资本化开支同比突增>50%(可能在粉饰利润)。**只报flag+具体数值，不自动扣分**——比如次新股/快速扩张期公司的应收增速跑赢营收很常见，未必是坏事，这一步的作用是把异常"亮出来"交给3.2/3.5的文字判断去解释，不是机械否决。

`any_flagged`为true时，在thesis里至少对每个被flag的项给一句解释或承认"未查到合理解释"；查不到合理解释、又叠加3.2a的quality screen fail时，两者一起计入3.5"最强反方证据"的候选池。

### 3.3 资金面/情绪视角

数据：`market_data.get_institutional_holders()`、`market_data.get_insider_transactions()`、`market_data.get_fundamentals()` 里的 `Short % of Float`/`Shares Short`、`market_data.get_analyst_recommendations()`。
判断资金是真实介入还是纯情绪炒作：机构持仓集中度上升+内部人无异常抛售，倾向真实介入；做空比例异常升高+内部人密集卖出，倾向风险信号。**没有"龙虎榜"/"北向资金"这类美股不存在的披露制度对应物，不要硬凑，缺就是缺。**

**可选增强**（该候选带期权链信息量大、或催化事件明显是宏观/政策性事件时才用，不是每票必查）：
- 期权链隐含波动率异常（call/put skew）可作为市场对该票事件预期强弱的补充信号：`vibe_trading_optional.get_options_chain(ticker)`
- 宏观/政策性催化因素（比如"美联储降息"这类）可用 Polymarket 的市场隐含概率做印证，而不是凭感觉判断"市场觉得这事儿多可能发生"：`vibe_trading_optional.prediction_market_search(query)`

### 3.3a 行业同业对比（新增）

数据：`industry.get_industry_snapshot(ticker)`、需要横向对比时再调 `industry.compare_peer_returns(symbols, curr_date)`。

1. 先看`ticker_ytd_rank_in_industry`——这只票在**全行业**(不是watchlist内部)里YTD涨幅排第几、`industry_overview.companies_count`共多少家。排名靠前(比如前5)且是罕见的高排名，本身就是需要在thesis里点出来的信息，不能只字不提"这票年内涨幅在同行业里是什么水平"。
2. 看`top_peers_by_weight`（按市值权重排的同业+评级）——如果一个候选的催化因素(3.2)是"行业性"的（比如DRAM涨价），检查同业权重最高的几家是否也在涨/评级也在被上调，用来判断这是"真行业性机会"还是"只有这一家在动"。需要具体数字时，把这几家的代码传给`compare_peer_returns()`拿1d/5d/20d/60d涨跌幅横向表。
3. `get_industry_snapshot()`返回`ok:false`（部分ADR无industryKey）或行业类目里目标票自己占了绝大部分市值权重（比如AAPL在"Consumer Electronics"占99.9%）时，如实写"该行业分类下无有效同业对比"，不要硬凑一个不成立的同业结论。

### 3.3b 定量概率校准

读取 `market_calibration.json`（2026-08-23已用真实数据构建，见下方"如何构建"一节——距上次构建超过30天或watchlist出现新行业时需要重新跑）。

方法论和 a-stock-screener 一致：不用单票自己的历史算（样本量太小），而是把该票所属行业的同业股票凑池，统计"某个特征达到什么水平时，未来5日跌/涨5%+的经验概率"。**同业股票池现在直接用 `industry.get_industry_snapshot()` 的 `top_peers_by_weight` 自动拿（免费、无需手动列名单），不用再像最初设想的那样手动凑。**

**分桶方式**：不是按固定涨跌幅区间分桶（比如"5%~10%"这种），是按该行业该窗口下所有历史样本的**分位数**分桶(`bottom10`/`p10_30`/`p10_30_normal`/`p70_90`/`top10`)——因为1日涨跌幅和20日涨跌幅的自然波动幅度差一个数量级，固定区间会导致某些窗口的桶严重不均匀，分位数分桶能自动适配尺度。每个桶同时报告该桶对应的实际涨跌幅数值范围，不是只给一个分位数标签。

**用法**：算出该候选当前的1/2/3/5/10/20日涨跌幅，去对应行业(`symbol_industry_map`)的`ret_Nd_curve`里找它落在哪个分位桶，读出该桶的`drop5pct_5d`/`rally5pct_5d`。**首次真实构建已经复现出和A股版一致的规律**：多数行业呈U型——暴涨(`top10`)和暴跌(`bottom10`)两端的未来5日下跌5%+概率都比"正常区间"(`p30_70_normal`)高，且"暴跌后反弹5%+"的概率往往比"暴涨后继续涨5%+"的概率更高(均值回归)。这不是每次都要重新验证的新发现，是已确认的通用规律，重点是把候选自己的数值代进去查对应概率。

**`drop5pct_5d`/`rally5pct_5d`这类数字必须是真的从`market_calibration.json`文件里读出来的，禁止凭印象/凭上一轮的记忆改写**（2026-09-04教训：VEEV一次thesis声称"该桶最新drop5pct_5d(18.85%)已低于rally5pct_5d(20.55%)、跟昨天(25.16%/18.93%)相比数字变了"，据此把`extended_after_large_move`标记清空、conviction从low跳回high——但`market_calibration.json`当天完全没有被改动过(`last_extended_date`字段+git log都能验证)，文件里那个桶的真实数字跟"昨天"完全一样，是25.16%/18.93%，凭空编出了一组不存在的数字。这类静态校准文件(`market_calibration.json`/`signal_calibration.json`)里的分桶概率**不会随着候选自己的价格变化而变化**——变的只应该是"候选的trailing涨跌幅落进哪个桶"，桶本身的`drop5pct_5d`/`rally5pct_5d`是这份文件上次构建/扩展时就定死的常数，除非文件真的被重新构建过(检查`built_date`/`last_extended_date`和git log能不能对上)。每次引用这两个数字前，要么是这一轮的Python调用里直接从文件读出来的原始输出，要么就不要在thesis里写出具体数字，写"与上一轮一致"这种定性表述，绝不能凭对话记忆去描述"数字变了多少"。

**`extended_after_large_move` risk_flag(2026-09-04新增)**：算出的1/2/3/5/10/20日任一trailing涨跌幅落在`top10`分位桶、且该桶`drop5pct_5d > rally5pct_5d`(历史上继续下跌的概率高于反弹) → 打上这个flag。这条规则直接回应用户的观察("筛选出来的新股票都有一个特征就是在前几天出现过大幅跳涨")——根因是Step2a/2d的候选来源和排序机制本身偏向"已经涨过一截"的票(见Step2a第5点、Step2d说明)，3.3b原本只是把"追高后统计上更容易回撤"这个信息写进thesis文字里，不会自动影响conviction，容易被强叙事盖过去(比如VEEV/ESTC那几次，3.3b数据已经指出回撤概率更高，但因为基本面证据扎实还是给了long/medium甚至high)——**打上这个flag之后，按3.5"risk_flags非空→low"的既有机械规则自动生效**，不需要额外写新的裁决逻辑，这条本来就有，只是`extended_after_large_move`之前不存在，没有被触发的入口。**该候选所属行业未被`market_calibration.json`覆盖时无法判断，如实标注不能评估，不要因为缺数据就假设没有这个风险**。

**关键差异（相对A股版）**：A股版的板块级兜底是"主板10%/创业板20%"两档涨跌停板类型，**美股没有这个每日价格限制机制**，改用**市值分层**兜底（大盘股/中盘股/小盘股三档，阈值可参考常见分类：大盘>100亿美元、中盘10-100亿、小盘<10亿）——行业池样本不足5只时，退化到市值分层兜底，方法论上和A股版"L3→L2行业粒度退化"是同一套逻辑，只是退化的落点从"板块类型"换成"市值分层"。

行业级校准给 `watchlist.json` 里的**`symbols` + `held_positions` 并集**建。补充扫描候选（既不在`symbols`也不在`held_positions`里的新代码）默认同样不单独建行业池——**除非**这个候选走到3.5综合裁决时方向落在`long`且conviction达到medium或以上：这种情况下必须在**写入最终thesis之前**临时给它所属行业建一次同业池同业曲线(方法论同下方"如何构建"一节)，追加进`market_calibration.json`和`signal_calibration.json`(不覆盖已有行业)，然后再回来完成3.3b的定量校准引用、定稿这条thesis。

**这条规则2026-09-04新增，直接吸取SOLS/ESTC/VEEV的教训**：之前"成本控制"的假设是"建一个行业池很贵"，但2026-09-04实测建1个新行业(同业池抓取+曲线构建+17信号回测)总共约10秒，负担得起——真正该省成本的地方是"screen掉大部分候选、不给avoid/low conviction的候选浪费时间"，不是省这几秒钟。3.5是Step3流程里最后一步，3.3b排在它前面，操作上是：先按3.1-3.4正常走，3.3b这一步如果发现行业未覆盖，先按"未覆盖"如实记录、继续往下走完3.4和3.5的初步判断；如果3.5判定方向确实是long且conviction≥medium，回头临时建库、把3.3b的定量校准补齐、再定稿——不是在3.3b这一步就臆测最终方向后决定建不建。**避免的正是"买入推荐已经给出、但支撑它的3.3b这层其实是空的"这种情况**——不管候选是不是持仓，只要要给出一个medium+的买入建议，这层校准就必须先补上再落笔，不能等它变成持仓之后才被动发现。

**`held_positions` 的行业覆盖优先级不能低于 `symbols`，2026-09-04教训**：SOLS(Specialty Chemicals)和ESTC(Software - Application)买入推荐给出的时候，这两个行业根本不在已建的6个行业池里——不是数字不准，是3.3b这一整层**直接被跳过**，而市值分层兜底(见下方"关键差异"一节)documented了但从未真正写出代码，所以实际上买入判断完全没有3.3b这层定量校准支撑，只是没人注意到。根本原因是行业池覆盖范围锚定在`watchlist.json`最初的`symbols`列表上，`held_positions`加了新持仓不会自动触发重新构建，用户后来才发现漏了。**修正**：`held_positions`里任何一个代码的行业不在已建的行业池里，视为触发重新构建的条件（不是"发现了才补"），必须在下一次给该持仓写thesis之前补建完成，不能让持仓票带着"3.3b缺失"的状态继续被当作完整分析处理——持仓是真金白银在里面，覆盖优先级理应高于还没买的自选股。

### 3.4 新闻/去重聚类

数据：`news.get_news_yfinance()`、`sec_edgar.get_recent_8k_filings()`（作为最权威来源交叉验证）。

1. 抓取两个来源关于该候选的近期内容。
2. **去重聚类**：判断新闻报道和8-K是否描述同一事件。若是，合并为一条论点，`sources` 列出全部来源链接。
3. **强度加权**：8-K是公司法定披露，权重高于媒体报道/市场传闻；`market_data.get_institutional_holders()`/`get_insider_transactions()` 显示的真实资金动作，权重高于纯舆论提及无资金跟进的事件。

### 3.5 综合裁决

分别写出该候选**最强的一条支持证据**和**最强的一条反对证据**，然后按固定规则定级（不是自由裁量）：

**反方证据推导必须先列失败路径再选最强的一条，不能想到哪条写哪条（新增，2026-09-10，吸收[ai-berkshire](https://github.com/xbtlin/ai-berkshire)"芒格反过来想"方法论skill）**：写"最强反方证据"之前，先列出2-3条不同的失败路径（每条包括：路径具体是什么、大致概率高低、如果发生影响有多大），路径来源可以是——同类公司历史上类似位置的结局(历史类比)、3.1-3.4已经出现的负面证据(内部人卖出/风险信号/技术面警告)、"如果我在做空这只票，理由会是什么"(空方视角反推)。列完之后再从里面选影响×概率最大的一条写成正式的"最强反方证据"，不是凭第一直觉想到哪条就用哪条——这么做是为了防止分析时只看见了自己已经注意到的那一种风险，漏掉了同等重要但没想到的路径。

**定稿前偏见自查（新增，2026-09-10，吸收[ai-berkshire](https://github.com/xbtlin/ai-berkshire)方法论skill）**：conviction/direction定稿前，thesis里必须补一句自查，诚实回答（不是走过场凑字数）：这个判断的确定性主要来自生意本质本身，还是来自读到的资料数量堆出来的？如果今天读到的资料量减半，这个conviction会不会变？这个结论是不是跟市场已有共识(分析师评级方向、目标价区间)高度一致——如果高度一致，我们比市场多知道什么？答不出最后一个问题时，如实承认"没有明显的信息优势，本条判断更多是复述市场已有观点"，不要为了显得有见地而编一个说不清楚的理由。

- **high** 需同时满足：
  - 有公司自己在 8-K 中公开确认的具体催化事件（非传闻、非待定）
  - 论点明确压制住了最强的反方证据
  - `risk_flags` 为空
  - 近期没有未解决的二元事件（如财报/审批结果未出）
- **low**（满足任一即可）：
  - 证据混杂/未定论
  - 涨跌主要由技术面/情绪驱动
  - `risk_flags` 非空
  - 催化因素是宏观/行业性的而非公司专属
- **medium**：其余情况——有可信的公司专属催化因素，但未达 high 标准。

`risk_flags` 只能从 `risk_rules.json` 的 `risk_flags_enum` 中选取，且必须有研究依据支撑才能打上，不能凭印象。

### 3.6 方向判定（direction）

`conviction` 回答"这个判断有多可信"，`direction` 回答"这个判断指向买还是不买"——两者是独立的轴，判定方式（沿用 FriesTrader/a-stock-screener 原版逻辑）：

- **`is_held: true` 的持仓**：`direction` 只能是 `long`（论点依然支持继续持有）或 `exit_existing`（不再支持），**永远不会是 `avoid`**。
- **未持仓的候选**：`direction` 是 `long` 或 `avoid`，由 3.1-3.5 综合出来的论点**整体净情绪**判断，不是套某条机械规则算出来的。

conviction 不决定 direction，它只验证"不管最终得出哪个方向，支撑这个方向的论证扎不扎实"。

**持仓票的 `invalidation_thesis` 必须同时覆盖基本面和技术面两类失效条件，不能只写基本面**（2026-08-28教训：MU/GOOGL/ALB连续几轮的invalidation_thesis只写了基本面情形，用户问"怎么没check技术面"才发现——3.1本来就强制每轮跑六信号检查，`exit`信号(图2)本来就是为"该不该卖"设计的，只是没被写进失效条件的文字里，容易让人误以为技术面没被纳入判断）。固定加一句"或六信号体系里exit/top_reversal/bearish_divergence任一触发"，不是说这三个信号自动等于卖出，而是确保这类证据不会被单纯当作背景数据、必须明确进入失效判断的考虑范围。

2026-08-31再加一句技术面失效条件：**"或月线MACD由零轴上方转为零轴下方（trend_regime转为bearish_confirmed）"**——3.1a的月线趋势闸门是比日线六信号更大级别的信号，持仓票的失效条件里应该同时覆盖日线级别(六信号)和月线级别(趋势闸门)两层技术面证据，不是只写一层。

### 3.7 估值台账（新增，2026-09-10）

**触发条件：3.6判定`direction`为`long`的候选，不分conviction高低，全部要生成**——用户明确要求过不按conviction筛选，即使low conviction的long也要跑完整这一套，接受Phase A单轮耗时会明显变长。`avoid`/`exit_existing`不需要。

这一步是把"公司自己说了什么(官方指引)"和"分析师怎么想(一致预期)"两条线分开呈现、再对照"同业中位数倍数"和"假设是成熟公司套用大盘倍数"两个基准，外加一个更保守的终值法估值——四个视角摆在一起看，不是给出一个单一"合理价"结论。逐项说明怎么拿数据：

**A. 公司官方指引**：`sec_edgar.get_recent_8k_filings(symbol, limit=15)`找最近一条items含`2.02`的记录，**主文件本身只是封面页+"详见Exhibit 99.1"这一句话，指引数字在单独的exhibit文件里**——用`sec_get()`直接访问该8-K所在目录(把主文件URL最后一段去掉，取目录路径)，从返回的HTML里找href含`ex99`/`ex-99`/`pressrelease`/`earningsrelease`字样的文件名(排除`R1.htm`这类XBRL查看器文件)，再对这个真正的新闻稿文件调`get_filing_text(url, max_chars=25000起，不够再加大)`，在正文里找"Outlook"/"Guidance"/"expects"这类关键词定位指引段落，通常在财务数据表格之后、Forward-Looking Statements法律声明之前。摘出：下一季度指引(如有)和全年指引(如有)的营收区间、EPS区间(留意标不标"非GAAP")、和公司自己给的YoY百分比原话(有时候公司会直接说"14.9% at the midpoint"这种话，直接摘录，不用自己重算)。**三种"没有"要分清楚，不能都写成"缺失"**：(1)公司根本不发正式指引(比如GOOGL，全文搜不到guidance/outlook字样)——如实标注"公司不提供正式指引"；(2)外国私人发行人走6-K/20-F不走8-K——如实标注这个原因；(3)公司说了要上调但没给具体数字(比如CHWY某次财报)——如实标注"定性表态，无具体区间"。这三种都不等于"数据缺口"，不要含糊处理成一句"未披露"。

**管理层语气信号（新增2026-09-11，方法论改写自ai-berkshire的`skills/earnings-review.md`第3.1节）**：读取上面这段新闻稿正文(以及8-K里有的MD&A段落)时，顺手标注管理层表述里的语气信号，不需要额外调用，是同一份文本的第二遍用途：🟢坦诚(主动承认问题+给出具体原因，比如"本季度利润率下降主要因为X业务投入超预期")、🟢清晰(有量化目标的具体表述)；🔴模糊("我们对未来充满信心"这类没有实质内容的话)、🔴转移(问利润率答收入增速这类顾左右而言他)、🔴归因外部化(把问题全归咎于宏观/行业，不谈内部可控因素)。这不是硬性打分项，是给3.2"管理层评估"和3.5"最强反方证据"提供一手依据——找不到明显信号时如实写"未见明显语气异常"，不要为了填满这一格生造一个信号。

**B. 分析师一致预期**：`yf.Ticker(symbol).earnings_estimate`和`.revenue_estimate`(通过`market_data`模块或直接yfinance调用皆可)，取`0y`(当前财年，通常跟公司刚给的当期指引是同一个周期，可以互相交叉验证)和`+1y`(下一财年)两行，各自的`avg`/`low`/`high`/`growth`/`numberOfAnalysts`。**`growth`字段就是yfinance自己算好的YoY同比，不需要手动去找上一财年实际值再算一遍**。**不要用`info['forwardEps']`/`info['forwardPE']`**——这两个字段是`currentPrice/forwardPE`反推出来的，可能跟`earnings_estimate`不同步、滞后(2026-09-09在CRCL身上验证过，`info.forwardPE`给59.4x而`earnings_estimate`算出来是66.78x，前者是旧的)，统一以`earnings_estimate`/`revenue_estimate`为准。

**分析师历史准确度参考（新增2026-09-11）**：`fundamentals_analysis.get_earnings_surprise_history(symbol)`拿近4季度EPS实际vs一致预期的surprise%和`beat_rate`，作为"这只票的分析师一致预期历史上有多准"的旁证——`beat_rate`长期接近1(每次都小幅超预期)通常说明公司/分析师双方都倾向于把指引报得保守，B这里的`avg`值可能系统性偏低；`beat_rate`低或surprise方向不稳定，说明一致预期本身分歧大/不确定性高，对应C/D/E算出来的隐含价也要多一分保留。这条跟3.7A的"公司官方指引vs实际"(见3.7a)是两条不同的线，一个看分析师、一个看公司自己，不要混着当一回事。

**C. 同业中位数倍数 + 同业中位数YoY**：复用`market_calibration.json`已经建好的同业池符号列表(不用重新调`get_industry_snapshot()`)，对池子里每个peer(排除自身)取`info['forwardPE']`和`earnings_estimate`/`revenue_estimate`的`+1y`的`growth`，各自算中位数(排除None和负值，负值多半是亏损公司，中位数会被拉得没有意义)。同业中位数隐含价 = 分析师`+1y` EPS均值 × 同业中位数forwardPE。**遇到同业池跟目标公司业务模式明显不对等时要点出来，不是算完就算了**——已知的几个例子：GOOGL被归进Internet Content池，同业是RDDT/SNAP/PINS这类中小内容平台，质量不对等；CRCL被归进Capital Markets池，同业是MS/GS/SCHW这类传统券商，跟稳定币业务模式不是一回事；MU/NVDA/CBRS同属Semiconductors池，把强周期的存储器和相对稳定的逻辑芯片混在一起比。

**D. 指数/"假设成熟公司"隐含价**：`yf.Ticker('SPY').info['trailingPE']`**每次都要重新拉取，不能沿用之前用过的数字硬编码**(这个数字会随时间浮动，2026-09-09用的是24.6x，不代表以后还是这个数)。指数隐含价 = 分析师`+1y` EPS均值 × SPY当前trailing P/E。这一列代表"如果市场把这家公司当成大盘平均水平的成熟公司定价，会给多少"，跟C的同业倍数是两个不同的参照系，都要给。**这条"不能硬编码"约束针对的是"凭记忆/复制会话里之前提到过的旧数字"这种做法**——3.7a引入的显式缓存机制(有date戳、有明确到期规则、到期会真的重新拉)不算硬编码；按3.7a规则判定需要重建时，D依然要重新拉取最新值，不能沿用缓存里已过期的旧值。

**E. 终值法估值（更保守的第四个视角，方法论来自开源项目[ai-berkshire](https://github.com/xbtlin/ai-berkshire)的`terminal_value.py`工具思路）**：
- 公式：`终值P/E = (1 - g/ROIC) / (r - g)`，终值年(第10年)股价 = 终值年EPS × 终值P/E，今天的隐含价 = 终值年股价 / (1+r)^10
- ROIC用`info['returnOnEquity']`(ROE)代替，是近似不是真ROIC，要注明
- g固定用**7%**(标普500自1950年以来长期历史5年年化盈利增速均值，2026-09-10通过网络搜索FactSet/multpl.com等信源确认，**这个7%本身是个经验统计值，不是永恒不变的常数，如果之后重新查到更新的长期均值数字，用新查到的替换，不要一直沿用7%这个数字而不复核**)
- r测两档：9%和11.5%(美元资产折现率合理区间的下限和上限，同样来自`terminal_value.py`的`CURRENCY_BANDS["USD"]`设定)
- 终值年EPS：从`0y`分析师EPS均值出发，用`+1y`同比增速作为起点，10年内线性衰减到g=7%，逐年复利滚到第10年
- **C2门禁**：r-g的spread必须≥5个百分点，本次r=9%/11.5%配g=7%，spread分别是2.0%/4.5%，**两档都不过这道门禁**，算出来的数字只能标注"未过C2门禁，仅供参考"，不能当结论用，但仍要算出来展示（后续如果找到更贴近r-g≥5pp的参数组合，可以替换，但不要因为过不了门禁就跳过不算）
- ROE明显处于周期高点或异常值(比如年化ROE超过50%这种，2026-09-09在MU/NVDA身上见过66.6%/117.2%导致算出的隐含价离谱到没有参考意义)时，**这一档结果要明确标注"不可信"，不要隐藏问题直接摆一个离谱数字**
- 本财年(`0y`)EPS为负(仍亏损)的候选，这一整套terminal value算不出来，标注"本财年仍亏损，模型不适用"，不要跳过不提

**E2. 三情景目标价（新增2026-09-11，跟E互补而非替代）**：`financial_rigor.three_scenario_valuation()`算牛/中/熊三档N年(默认3年)目标价——EPS按各档年化增速复利滚N年，乘以各档退出PE，不折现回现值。跟E的区别：E是永续折现模型，在r≤g时数学上无定义(2026-09-10已实测撞过这堵墙，比如市场当前隐含增速15%时r=9%/11.5%都undefined)；这个方法没有r-g的数学限制，代价是没有把终值折现回今天，是纯N年后名义目标价，不能跟E的"今天的隐含价"直接比大小。牛/中/熊三档增速和退出PE取值：熊档参考C的同业中位数倍数和历史更低分位，中性档参考B的分析师`+1y` growth，牛档参考公司官方指引(A)如果明显更高的话；三档都要写出取值依据，不能凭感觉填数字。

**计算校验（新增2026-09-11）**：B-E2所有隐含价/倍数换算，凡是涉及乘除的地方（分析师EPS均值×同业中位数PE、×SPY trailing PE、终值折现、三情景复利），都用`financial_rigor.py`的`verify_valuation`/`exact_calc`/`three_scenario_valuation`算，不要心算或用近似值——这是ai-berkshire全部estimate类skill的共同硬性要求("`禁止LLM心算`")，用Decimal精确计算能避免复利多轮滚算时的浮点误差累积。同一个数字如果有多个来源(比如公司指引的营收区间中值 vs 分析师一致预期的营收均值)，用`financial_rigor.cross_validate()`做交叉验证，偏差超容差的要点出来不能悄悄各写各的。

**F. 分析师高低估值分歧原因**：`WebSearch`搜"{公司名} {ticker} analyst price target [具体高低数值] rationale/reason"这类关键词，**尽力找，但不保证找得到，找不到就如实写"未查到具体机构归因"，不要为了填满这一格而编一个说不清楚来源的理由**。找到时优先摘录：具体机构名字+分析师姓名(如果搜得到)、目标价数字、给出这个数字的核心逻辑(用了什么倍数/关键假设是什么)。CBRS这只票在2026-09-09/09-10两轮搜索里都没查全(只查到一次未具名的目标价上调)，这是正常情况的下限，不是搜索方法有问题。

### 3.7a 估值台账缓存机制（新增，2026-09-11，为降低token消耗）

背景：2026-09-10单轮Phase A里3.7这一步占了总耗时的35-40%(9只long候选全部走一遍A-F)，2026-09-11单轮agent用了约50万token。用户要求先做"不影响质量"的优化：不改变3.7 A-F本身要呈现的内容和判断逻辑，只优化"数据要不要每天从头重新拿"。跑几天看实际节省多少token后，再决定要不要动会牺牲信息量的选项(缩小候选池/按conviction分级/降模型档位)。

**缓存文件**：新建 `valuation_cache.json`(仓库根目录，跟`market_calibration.json`同级)，按symbol分组：

```jsonc
{
  "AAPL": {
    "built_date": "2026-09-10",           // 这条缓存最后一次完整重建(A-E)的日期
    "last_8k_seen_date": "2026-08-01",    // 重建当时看到的、items含2.02的最新一条8-K的filing日期，用来判断之后有没有新指引
    "guidance": { /* A部分摘出的内容，结构同下方valuation_ledger.rows里company_guidance_quarter/annual两种row */ },
    "guidance_track_record": [  // 2026-09-11新增，只增不删，见下方规则2
      {"period": "FQ2 FY2027", "guided_range": {"sales_low": 104e9, "sales_high": 108e9}, "actual_sales": 106.5e9, "beat_or_miss": "beat", "deviation_pct": 0.021}
    ],
    "analyst_and_benchmarks": {           // B/C/D/E打包缓存
      "industry_median_fwd_pe": 19.36,
      "spy_trailing_pe": 24.6,
      "roe_used_as_roic_proxy": 0.14425,
      "rows": [ /* analyst_current_fy / analyst_next_fy 两行，含industry_median_implied_price/index_implied_price/terminal_value，结构同下方valuation_ledger.rows */ ]
    },
    "dispersion": {"reason": "...", "search_date": "2026-09-08", "not_found": false}
  }
}
```

**A(公司官方指引)+B/C/D/E(分析师/同业/指数/终值) 的重建规则**：
1. 该symbol在`valuation_cache.json`里没有条目 → 必须重建(A-E全走一遍)。
2. 有条目，且`sec_edgar.get_recent_8k_filings(symbol)`里能找到一条items含2.02、filing日期晚于`last_8k_seen_date`的新记录 → 必须重建，公司发了新财报/新指引，A的指引内容和B的分析师预期大概率都跟着变了，两者一起刷新，不能只刷新一半。**触发这一条时，覆盖旧`guidance`之前，先做一次指引兑现追踪（新增2026-09-11，方法论改写自ai-berkshire`skills/earnings-review.md`第5.2节）**：旧`guidance`里记录的上一期指引(比如上季度给的"下季度营收区间X-Y、EPS区间A-B")，跟这次新8-K/财报里实际报告的对应期实际值对比，算出偏差方向和幅度，追加写入`guidance_track_record`数组(`{period, guided_range, actual, beat_or_miss, deviation_pct}`)，这个数组只增不删，用于积累"这家公司指引可信度"的历史——`beat_or_miss`统计到3次以上时，在3.7A的输出里加一句"该公司近N次指引兑现情况：X次超预期/Y次符合/Z次不及"，给B/C/D/E的隐含价结论提供一个"这家公司的指引/分析师预期历史上有多可信"的背景，不是每次都从零开始假设指引100%可信。没有旧`guidance`可比时（该symbol第一次建缓存），如实跳过这一步，不倒推历史。
3. 没有新8-K，但距`built_date`超过7个自然日 → 重建B/C/D/E(分析师一致预期/同业中位数/指数倍数/终值法)，A可以沿用缓存(公司指引文本在下一次财报前不会变，不需要跟B/C/D/E绑同一个7天窗口)。选7天而不是`market_calibration.json`的30天，是因为分析师一致预期会因non-8K事件(评级调整/行业数据/宏观)被机构修正，缓存周期不宜拖太长导致台账脱离当前实际。
4. 都不满足(有条目、没有新8-K、7天以内) → 直接读缓存，不重新调用yfinance/SEC相关接口。

**F(分析师高低分歧原因)的重建规则**：
1. `dispersion`没有条目 → 重新WebSearch。
2. `not_found:true`且距`search_date`超过14天(值得重新搜一次看有没有新报道) → 重新WebSearch。
3. `not_found:false`且距`search_date`未超过14天 → 沿用缓存，输出时在`analyst_dispersion_reason`后加一句"(沿用{search_date}的搜索结果)"，不重新搜。
4. 触发了上面A/B/C/D/E重建规则第2条(有新8-K) → 即使在14天内也要重新搜一次——新财报/指引后analyst通常会跟着发新评论，旧的分歧原因可能已经过时。

**输出**：这一整套内容(不管来自缓存还是重建)作为`valuation_ledger`字段写进该候选的Step 4记录（见下方schema），不是另起一份文件。新增`cache_status`子字段标注A-E和F各自这一轮是`"rebuilt"`还是`"cached_from_YYYY-MM-DD"`，用于事后核对实际省了多少token，不是为了掩盖重建过程。数据本身仍是这一轮的快照，字段里要带上取数/查缓存当天的日期。

**维护**：`valuation_cache.json`由Phase A运行本身按上面规则直接读取/更新写回，跟`pending_proposals.jsonl`一样是Phase A的常规读写对象，不需要像`market_calibration.json`那样单独跑一次性脚本。git commit当天Phase A变更时一并提交这个文件的变化。

### 3.8 持仓论文追踪台账（新增，2026-09-11，方法论改写自[ai-berkshire](https://github.com/xbtlin/ai-berkshire)`skills/thesis-tracker.md`+`skills/thesis-drift.md`）

**动机**：现在持仓票只有一句话的`invalidation_thesis`（见3.6），Phase B每天只机械检查价格止损/六信号/月周趋势闸门。但"这个持仓论点到底还成不成立"这个问题，一句话概括不了太多信息——买入时支撑论点的可能有好几条独立假设，其中一条被证伪不代表整个论点崩溃，但也不该被笼统的"论点还成立"一句话盖过去。这一节给`is_held: true`且`direction: long`的持仓建一份结构化的、可以跨天累积状态的论文台账，不是每天重写，是每天判断"要不要碰它"。

**触发条件**：只对`is_held: true`的持仓生效，不对未持仓的long候选生效——未持仓候选的`invalidation_thesis`一句话已经够用，没有"买入后纪律"这个场景。

**新文件**：`thesis_ledger.json`（仓库根目录），按symbol分组：

```jsonc
{
  "MU": {
    "established_date": "2026-08-15",       // 首次建台账的日期，不是买入日期(买入日期从watchlist.json的held_positions.date_acquired读)
    "core_thesis_5_sentences": {
      "business_essence": "存储芯片(DRAM/NAND)制造商，受益于AI服务器HBM需求",
      "moat": "规模效应+技术壁垒，处于加宽阶段(HBM份额提升)",
      "management": "值得信赖，资本配置纪律良好，理由：...",
      "valuation_discount": "相对3.7E终值法隐含价打X折，安全边际来自...",
      "downside_if_wrong": "可控，因为...；不可控的情形是..."
    },
    "hypotheses": [  // 通常3-7条，太少说明想得不深，太多说明论点不聚焦
      {"id": 1, "text": "DRAM涨价周期至少持续到FY2027", "verify_method": "季度财报毛利率趋势+行业价格数据", "status": "green", "last_checked": "2026-09-10", "evidence": "..."}
    ],
    "red_lines": [  // 严重度分级，不是非黑即白的sell/hold二选一
      {"id": 1, "condition": "管理层诚信问题(财务造假/关联交易)", "severity": "fatal", "action_if_triggered": "立即清仓", "triggered": false},
      {"id": 2, "condition": "核心业务连续2季度收入下滑", "severity": "severe", "action_if_triggered": "减仓50%，重新评估", "triggered": false},
      {"id": 3, "condition": "管理层大规模非计划性减持", "severity": "warning", "action_if_triggered": "深入调查原因", "triggered": false}
    ],
    "valuation_anchor": {
      "buy": {"price": 123.45, "pe": 18.2, "date": "2026-08-15"},
      "last_check": {"price": 130.0, "pe": 19.1, "date": "2026-09-03"},
      "current": {"price": 145.0, "pe": 20.5, "date": "2026-09-11"}
    },
    "health_score_history": [
      {"date": "2026-09-10", "score": 8, "note": "假设1边际弱化(涨价斜率不及预期但方向未变)，无红线触发"}
    ]
  }
}
```

**建台账（新symbol首次is_held:true且long时）**：3.5/3.6定稿thesis之后，补写`core_thesis_5_sentences`(五句话，逐句必须能写出来，写不出来说明3.5的论点本身不够扎实，回去重新过一遍3.5而不是硬凑这五句话)、3-7条`hypotheses`(每条要能被验证，不能是"公司很好"这种空话，参考格式"收入增速维持15%+"这种可验证的具体断言)、`red_lines`(3-5条，分`fatal`/`severe`/`warning`三档，每档配不同的`action_if_triggered`，不是所有红线都等价于立即清仓)、`valuation_anchor.buy`(取买入当天的价格+3.7的隐含PE)。初始`health_score_history`只有一条，分数按下面公式算。

**检查台账（后续每次Phase A跑到这个symbol时）**：先看触发条件是否满足——(a) 3.7a判定该symbol今天有新8-K(items含2.02) 或 (b) 今天的3.2b/3.4发现了新的risk_flag。都不满足时**跳过，不用重新过一遍全部假设**——直接沿用上次的`hypotheses`/`red_lines`/`health_score_history`最后一条，这是跟3.7a同样的成本考量：多数假设只在有新财报/新风险事件时才会真的移动，天天重跑是浪费。满足任一触发条件时：
1. 逐条核对`hypotheses`，状态改成🟢成立/🟡边际弱化/🔴受损/⚫破裂四档之一，`evidence`写这次判断依据的具体证据(不能只改状态不写理由)。
2. 逐条核对`red_lines`是否触发，`triggered`改成true/false。
3. 更新`valuation_anchor.current`。
4. 算健康度分数：`10 - (⚫破裂假设数×3) - (🔴受损假设数×2) - (🟡弱化假设数×1) - (触发红线数×5)`，最低1分最高10分，追加一条到`health_score_history`(不覆盖旧记录)。
5. **区分"证据变了"和"只是措辞变了"**（吸收`thesis-drift.md`的核心原则）：某条假设的状态之所以改变，必须能指向具体的新证据(财报行项目/监管披露/新闻事件)，不能因为这次描述得更悲观/更乐观就改状态而没有新证据支撑——找不到能解释变化的新证据时，状态维持不变，不能凭感觉调整。

**Phase B消费方式**：见`PHASE_B_TASK.md`——健康度骤降或红线触发时进操作清单的`reason`，不强制转成sell(除非红线本身的`action_if_triggered`就是"立即清仓"且严重度是`fatal`，这种情况跟现有`price_stop_loss_triggered`一样强制sell；`severe`/`warning`级别只是显式提示，交给人工判断，跟现有"只有price_stop_loss强制"的设计保持一致)。

## Step 4: 输出

1. 整体覆盖（不是追加）写 `pending_proposals.jsonl`。每条记录包含以下字段：

```jsonc
{
  "date": "2026-08-23",
  "timestamp": "2026-08-23T16:30:00-05:00",
  "symbol": "AAPL",
  "stage": "thesis",              // "screened" | "thesis" | "summary"
  "is_held": false,
  "thesis": "1-3句话，说明发生了什么变化、为什么可能重要。不用'这将会...'这种确定性预测语气，只能用'这可能暗示...'",
  "conviction": "medium",         // low | medium | high
  "invalidation_thesis": "什么情况发生时这个判断就不成立了",
  "direction": "long",            // long | avoid | exit_existing
  "risk_flags": [],
  "pct_below_52wk_high": 0.08,
  "trend_signal": {"institutional_holding_change_pct": 0.12, "analyst_rating_shift": 1},  // 仅当由趋势信号触发时填
  "source_cluster_size": 2,
  "sources": [{"outlet": "SEC 8-K", "url": "..."}, {"outlet": "Yahoo Finance News", "url": "..."}],
  "signal_check": {"price_move_60d_pct": 0.24, "volume_spike_multiple": 1.1, "pct_from_52wk_extreme": 0.08},
  "technical_signals_triggered": ["top_reversal"],  // 2026-08-23新增，check_all_signals()六个信号+2026-09-08新增的double_bottom/head_and_shoulders_bottom两个形态信号，triggered的信号名列表，全部False则给空数组
  "industry_context": {"rank": 2, "of": 75, "ytd_return": 2.3873},  // 2026-08-23新增，来自industry.get_industry_snapshot()，无有效同业时省略此字段
  "trend_gate": {  // 2026-08-31新增，来自check_trend_gate()，见3.1a
    "trend_regime": "pullback",           // strong_aligned | pullback | weak_rebound | bearish_confirmed | insufficient_history
    "weekly_support_level": 703.43,
    "pct_below_weekly_support": -0.3262,  // 负数=价格在支撑位上方；正数=已跌破支撑位
    "price_stop_loss_triggered": false    // 最近N个交易日收盘价连续低于支撑位*(1-阈值)才为true，见risk_rules.json的trend_gate
  },
  "valuation_ledger": {  // 2026-09-10新增，见3.7，仅当direction="long"时存在(不分conviction)，avoid/exit_existing没有这个字段
    "as_of_date": "2026-09-10",
    "current_price": 260.80,
    "industry_median_fwd_pe": 19.36,     // 来自market_calibration.json同业池
    "spy_trailing_pe": 24.6,             // 按3.7a规则该重建时重新拉取yf.Ticker('SPY').info['trailingPE']，否则沿用缓存
    "roe_used_as_roic_proxy": 0.14425,
    "cache_status": {  // 2026-09-11新增，见3.7a，标注这一轮A-E/F各自是重建还是沿用缓存，用于核对实际节省的token
      "guidance_and_benchmarks": "cached_from_2026-09-08",  // 或 "rebuilt"
      "dispersion_reason": "rebuilt"                         // 或 "cached_from_YYYY-MM-DD"
    },
    "rows": [
      {
        "type": "company_guidance_quarter",  // company_guidance_quarter | company_guidance_annual | analyst_current_fy | analyst_next_fy
        "period": "Q3 FY27",
        "sales": {"value": 933500000},              // 公司指引通常是单值或区间中值，分析师行则是{"low":...,"avg":...,"high":...,"num_analysts":...}
        "qoq_pct": 0.006, "yoy_pct": 0.151,
        "eps": {"value": 2.335, "basis": "non_gaap"},
        "notes": "EPS对比用了GAAP实际基数，口径不完全一致"
      },
      {
        "type": "analyst_next_fy",
        "period": "FY2028 (+1y)",
        "sales": {"low": 4083058000, "avg": 4143231770, "high": 4236368130, "num_analysts": 26},
        "yoy_pct": 0.123, "industry_median_yoy_pct": 0.098,
        "eps": {"low": 9.68, "avg": 10.25064, "high": 10.64, "num_analysts": 26},
        "implied_pe": {"low": 24.51, "avg": 25.44, "high": 26.94},
        "industry_median_implied_price": 198.44,
        "index_implied_price": 252.15,
        "terminal_value": {   // 见3.7节E，g=7%固定值，需定期复核来源数据是否过期
          "g": 0.07, "scenarios": [
            {"r": 0.09, "spread": 0.02, "c2_gate": "FAIL", "implied_price": 233.14, "reliable": true},
            {"r": 0.115, "spread": 0.045, "c2_gate": "FAIL", "implied_price": 82.59, "reliable": true}
          ]
          // reliable=false 的情形：本财年EPS为负(仍亏损)，或ROE明显处于周期高点/异常值(如>50%)，此时这个候选完全省略terminal_value字段或所有scenario都标false，不要展示一个不可信的数字当结论
        },
        "notes": "营收略超同业(12.3% vs 9.8%)，但EPS落后同业中位数(11.0% vs 21.4%)"
      }
    ],
    "analyst_dispersion_reason": "未查到具体机构归因"  // 或摘录查到的具体机构名字+逻辑，找不到就如实写这句，不要编
  }
}
```

2. 未触发信号或被 Step 1 过滤掉的候选，以 `stage: "screened"` 记录原因（不写完整论点）。
3. 末尾写一条 `stage: "summary"`，按 `rejected` / `no_signal` / `avoid` / `long` / `exit_existing` 分桶汇总本轮所有代码。
4. 把本轮所有候选（不只是触发信号的）的快照追加写入 `sentiment_history.jsonl`（对应 Step 2c）。
5. git add + commit（message 格式：`Phase A YYYY-MM-DD`）。**不 push，不下单，不生成操作清单。**

## 如何构建 market_calibration.json（首次跑 & 之后周期性刷新）

**2026-08-23已实际跑通一版，流程如下**（比最初设想的"手动凑同业池"简化很多，因为`industry.py`能免费自动拿同业名单）：

1. 对 `watchlist.json` 的 `symbols` + `held_positions` **并集**每只票，调 `industry.get_industry_snapshot(ticker, peer_limit=15)` 拿该票的`industry`名和`top_peers_by_weight`(按权重排的同业代码，已自动排除自身)。多只票同属一个行业时(比如NVDA和MU都是Semiconductors)，同业池取并集，只建一次。
2. 对池子里每只票（含自选股本身）调 `load_ohlcv()` 拿能拿到的最长历史（一般~5年，不需要精确凑300个交易日）。少于60个交易日历史的票跳过。
3. 对每只票的收盘价序列，在1/2/3/5/10/20日回看窗口下，逐个交易日滚动计算"trailing N日涨跌幅"和"未来5日涨跌幅"这一对样本，同一行业池内所有票、所有历史交易日的样本池化在一起（这一步是跨股票+跨时间双重池化，样本量远大于a-share版单纯"同业票数×1天"的池化方式）。
4. 按`trailing`涨跌幅的分位数(0/10/30/70/90/100百分位)切成`bottom10`/`p10_30`/`p30_70_normal`/`p70_90`/`top10`五桶，每桶算`drop5pct_5d`(未来5日跌5%+经验概率)和`rally5pct_5d`(涨5%+经验概率)，同时记录该桶对应的实际涨跌幅数值范围。
5. 同业池有效历史数据不足5只时，跳过该行业曲线构建（本次7只自选股覆盖的6个行业都有6-16只有效同业，未触发这条兜底；这条规则理论上存在，真触发时可以退回市值分层(大/中/小盘)池化，但目前没有现成代码，需要时再补）。
6. 距上次计算超过30天，或 `symbols`/`held_positions` 出现现有行业池覆盖不到的新行业时，重新构建——**`held_positions` 出现新行业属于必须立即处理的情形，不是"下次顺便"**，2026-09-04教训见上方3.3b小节。
7. **不要在每次Phase A里重新计算**——这是慢变量，Phase A只读取，不重算。构建脚本本身不在仓库里常驻(是次性跑的Python脚本)，产物`market_calibration.json`才是Phase A实际读取的东西。

## 可选：学术因子研究（不是每轮都用）

想验证某个筛选思路是否有学术依据、或者给 `market_calibration.json` 找新的候选特征时，可以用 `vibe_trading_optional.research_papers_search(query)` 搜 arXiv/OpenAlex，再用 `research_papers_read(paper_ids)` 让它提炼出该论文的具体信号定义/所需数据/声称的回测表现——**这些数字是论文自己声称的，没有被本系统复现验证过**，只能当研究线索，不能直接当作已验证的规律写进论点。

## 可选：超级趋势确认与供应链瓶颈发现（新增2026-09-11，方法论改写自[ai-berkshire](https://github.com/xbtlin/ai-berkshire)`skills/bottleneck-hunter.md`）

**判断修正**：这一节最初的版本判断`bottleneck-hunter`"宏观叙事驱动、跟自下而上原则冲突"，未采纳——用户指出这个判断只看了标题目录、太草率。完整读正文后纠正：它的"超级趋势确认"要求至少3个带日期/来源的**已发生验证事件**+具体资本开支规模数据，不是凭印象判断宏观；瓶颈识别是6条标准量化打分到S/A/B级；找到标的后估值仍是硬门禁(红绿灯系统，市值/TAM占比、PS、5年乐观收入预测倍数这些数字卡死，不会被"瓶颈叙事"盖过)——这套纪律跟我们系统一贯"估值不能被叙事盖过"的原则是一致的，不是冲突的，予以采纳。

**用途**：回答"看哪个行业/主题"这个问题——是下面"行业主题候选发现"的前置环节，那一节回答的是"这个行业里选哪几个标的"。**人工手动触发**(用户直接指定要判断的趋势，或让系统按"当前跟踪的超级趋势清单"逐个过一遍)，不是Phase A每轮自动跑的部分；判断节奏建议每季度做一次，见文末"提醒节奏"。

**新文件**：`super_trend_map.json`(仓库根目录)，记录已确认/已否决的趋势 + 对应供应链瓶颈地图 + `last_confirmed_date`：

```jsonc
{
  "last_confirmed_date": "2026-09-11",  // 最近一次跑完整第一步的日期，Phase C据此判断是否该提醒
  "watch_list": ["AI基础设施建设", "能源转型", "国防现代化", "半导体再工业化", "太空经济"],  // 起点清单，持续维护
  "trends": [
    {
      "name": "AI基础设施建设",
      "status": "confirmed",  // confirmed | insufficient_evidence
      "core_driver": "一句话",
      "verification_events": [{"date": "2026-08-01", "event": "...", "source": "..."}],
      "capex_scale_usd_per_year": 500000000000,
      "supply_demand_gap": "demand_outpacing_supply",
      "confirmed_date": "2026-09-11",
      "layers": { "layer0": [...], "layer1": [...], "layer2_focus": [...], "layer3": [...], "layer4": [...] },
      "layer1_key_players": [{"name": "英伟达", "symbol": "NVDA", "why_key": "GPU架构迭代的功耗密度曲线直接决定Layer2液冷/供电瓶颈的紧张程度"}],  // 2026-09-12新增，见2.3
      "customer_supplier_graph": [  // 2026-09-12新增(二次修正版)，见2.2，关系列表不是叙述段落
        {"customer": "微软/谷歌/亚马逊/Meta(四大云厂商)", "customer_layer": 0, "supplier": "英伟达/AMD", "supplier_layer": 1, "relationship": "direct_purchase", "what": "GPU/AI加速器直接采购，capex指引即采购规模先行信号"},
        {"customer": "微软/谷歌/亚马逊/Meta(自建电站BYOP)", "customer_layer": 0, "supplier": "GE Vernova/西门子能源/三菱重工", "supplier_layer": 2, "relationship": "direct_purchase", "what": "重型燃气轮机，绕开电网并网排队"},
        {"customer": "受监管公用事业(跟上一条并行的第二条需求线)", "customer_layer": "4/终端用电方", "supplier": "同上电力设备供应商", "supplier_layer": 2, "relationship": "direct_purchase", "what": "电网升级用变压器——某瓶颈的demand_growth若来自多条独立客户线，都要列出，不能只写好搜到证据的一条"},
        {"customer": "英伟达/AMD GPU产品路线图(功耗密度曲线)", "customer_layer": 1, "supplier": "Vertiv/Boyd/CoolIT", "supplier_layer": 2, "relationship": "derived_requirement", "what": "芯片TDP上升倒逼液冷渗透率提高，不是芯片厂直接下单——这种间接关系意味着即使客户不砍单，只要功耗曲线本身趋缓，下游需求也会自然降温"}
      ],
      "bottlenecks": [
        {"segment": "HBM先进封装载板", "layer": 2, "criteria": {"supplier_concentration": "red", "expansion_lead_time": "red", "substitutability": "yellow", "utilization": "red", "demand_growth": {"level": "red", "reason": "...(必须具体指出对应customer_supplier_graph里的哪条关系，不能只贴历史增速百分比)", "traced_to_customer_supplier_graph": true}, "qualification_cycle": "yellow"}, "tier": "S", "last_updated": "2026-09-11"}
      ]
    }
  ]
}
```

### 第一步：超级趋势确认

只追同时满足全部4条的趋势，不在小风口里找幻觉：

| 标准 | 要求 |
|------|------|
| 持续性 | 至少3-5年确定性增长 |
| 物理性 | 需要实际硬件/材料/设备建设，不是纯软件/概念叙事 |
| 规模性 | 全球资本开支>500亿美元/年 |
| 加速性 | 需求增速>供给扩产速度 |

每条都要用`WebSearch`找具体证据(行业预测报告、头部玩家capex指引、供需缺口数据)，不能凭印象打勾。产出至少3个**已经发生的验证事件**(带日期+来源，不是预测/展望)。四条标准+验证事件都过了才算"确认"，写进`super_trend_map.json`；任一条不满足，如实标"证据不足，暂不追踪"，同样记录留痕，不是判断"不追"就删掉不提。

用户没有指定具体趋势时，从这份起始清单逐个过一遍(原样保留ai-berkshire给的初始清单，不代表这几个已经确认，只是"从哪几个方向开始想"的起点)：AI基础设施建设(数据中心/GPU集群/网络互联/电力)、能源转型(核电重启/电网升级/储能)、国防现代化(西方军费上升周期/供应链重构)、半导体再工业化(美欧日补贴建厂/设备材料瓶颈)、太空经济(卫星互联网/发射频次激增)。这份清单本身也应该随时间更新——发现新的候选超级趋势时加进去，`super_trend_map.json`里持续维护，不是写死不变的。

### 第二步：供应链物理拆解

#### 2.1 分层

把确认的趋势拆成物理层级：Layer 0(终端产品/服务)→Layer 1(核心组件，通常已被市场充分定价，比如AI基础设施趋势下的GPU/HBM本身)→Layer 2(子组件/材料，**alpha集中区**)→Layer 3(上游设备/原料)→Layer 4(电力/冷却/土地这类基础设施)。

**这一刀切的是"市场关注度/定价充分度"，不是纯技术供应链深度**——判断某个环节进Layer1还是Layer2，标准是"这是不是已经被反复讨论、股价已经计入这个叙事的名字"，不是"技术上离终端产品有几步"。这条标准本身是agent基于领域常识做的判断，**不像下面2.2/第三步那样逐项要求独立信源**，存在"叙事关注度偏差"的风险——同一次判断有可能把"其实已经被小众资金盯上了"的环节误判成"没人关注"。缓解办法：Layer1候选(2.3)和Layer2-3瓶颈候选(第四步)最终都会各自算出PE/PS这类倍数，**如果发现某个被归进Layer2的名字估值倍数已经跟Layer1核心龙头差不多贵，这就是"定价充分度判断错了"的信号，要回头修正分层，不是硬着头皮按错误的分层继续走**。

#### 2.2 客户-供应商关系图（新增2026-09-12，用户要求：先搞清楚谁是谁的客户/供应商，再谈瓶颈；2026-09-12二次修正：初版只写了每层一句泛泛的驱动力叙述，没有真正列出具体客户-供应商关系、也没有回填Step3，是错误的执行，本版重做）

**在给Layer2-3打瓶颈分之前，先回答"这条产业链上具体是谁向谁采购、买的是什么、这层关系是不是真的直接"，这是比瓶颈打分更前置的问题**——瓶颈打分回答"这个环节紧不紧"，这一步回答"这个环节的紧张程度是被哪个具体客户的哪个具体行为决定的"。

**产出是一张关系列表，不是一段叙述**，每条至少包含：`customer`(具体公司名，不能是抽象层名)、`supplier`(具体公司名)、`relationship`类型(`direct_purchase`直接采购/`derived_requirement`技术规格派生的间接需求，不是直接下单/`vertical_integration`客户和供应商是同一家公司内部关系/`subsidy_driven`政策资助驱动而非市场化采购)、`what`(买卖的具体是什么，以及这条关系解释了Step3哪个瓶颈的紧张程度)。写进`super_trend_map.json`每个趋势的`customer_supplier_graph`字段。

**关键要求，不能省略**：
1. 一个趋势至少要覆盖Layer0→1和Layer1→2两层关系，Layer2-3之间的关系（比如"台积电是味之素ABF树脂膜的客户"）也要写，不能只写到Layer1为止。
2. **不要把关系简化成一句"XX会影响YY"**——要写清楚是直接采购(数字口径能对上，比如资本开支指引就是采购规模)还是间接的技术规格派生需求(比如芯片功耗曲线不是芯片厂直接向散热设备商下单，而是倒逼数据中心运营商去采购更强的散热方案)，这两种关系对应的"紧张程度会不会缓解"的逻辑是不一样的：直接采购关系里，客户砍单立刻传导；派生需求关系里，即使客户不砍单，只要技术规格本身变化放缓(比如芯片功耗曲线趋于平缓)，下游需求也会自然降温。
3. 如果同一个瓶颈的需求来自**多条独立客户线**（比如变压器瓶颈同时被"云厂商自建电站"和"公用事业电网升级"两条线拉动），必须把两条都列出来，不能只写其中一条更好搜到证据的。
4. 如果某个瓶颈的供应商本身就是下游客户的一部分（比如国防现代化里SRM产能就在主承包商自己手里），这是一种特殊的`vertical_integration`关系，必须标出来——这种情况下"瓶颈会不会缓解"取决于客户自己的资本配置意愿，不是外部供应商扩产能力的问题，跟一般的第三方供应商瓶颈的分析逻辑不一样。
5. **第三步每个瓶颈的`demand_growth`字段的`reason`，必须能具体指出对应到这张图里的哪条/哪几条关系**（标`traced_to_customer_supplier_graph: true`），不能只贴一个历史增速百分比——历史增速是结果，这张图解释的是"这个增速从哪个具体客户的哪个具体行为来的、这个行为会不会变"。

#### 2.3 Layer1候选记录（新增2026-09-12，2026-09-12二次修正：不在发现流程里做技术面扫描，只负责列名单）

Layer1"已被市场充分定价"不代表不该被跟踪——**它只是意味着这类候选的入场逻辑不是"低估的瓶颈玩家"，而是"验证过的行业龙头，等因为行情/情绪原因被错杀时找机会介入"**，是两套完全不同的投资逻辑，不能用同一套估值红绿灯或早期公司检查去套。

**这一步的产出只是一份名单，不做技术面判断**：

1. 对每个Layer1环节，列出实际的上市龙头公司(名称+代码)，跟Layer2-3的瓶颈候选一样追加写入`industry_discovery_log.jsonl`，用`candidate_type: "layer1_leader_momentum_dip"`区分（Layer2-3瓶颈候选标`candidate_type: "layer2_3_bottleneck_value"`），两类候选不能混在同一个逻辑框架里评估。
2. **不走估值红绿灯/早期公司检查**——这些是已经被定价的成熟龙头，"贵"是常态，用估值红绿灯去卡它们大概率全部亮红灯，卡了也没有意义。
3. **也不在这一步跑`check_all_signals()`/`check_trend_gate()`去判断"现在有没有错杀信号"**（2026-09-12修正：初版曾在这一步做过一次性技术面扫描，写进`notes`说"当前有没有处于错杀窗口"——这个设计本身是错的，一次性扫描当天就过期，没有意义，而且重复了本系统已有的机制）。**"有没有错杀"这个判断应该由symbol被加进`watchlist.json`之后，每天正常跑的Phase A去持续回答，不是在发现流程里snapshot一次**——不需要在发现阶段单独做、也不应该做（因为发现阶段做的判断不会跟着时间刷新，容易被误当成"最新结论"一直沿用）。
4. **重要澄清（2026-09-12三次修正，避免被误读成"Layer1候选只走技术面"）**：Layer1龙头一旦加进`watchlist.json`，**跟其他任何symbol一样，完整跑3.1-3.8全套（3.2基本面/3.7估值台账/3.5综合裁决一条都不能少）**，不存在"Layer1只看技术面、瓶颈候选才走全套"这种分岔——上面第2点"不走估值红绿灯/早期公司检查"，指的仅仅是**发现阶段那道给亏损小公司设计的估值检查**（3.2a/3.7对已经充分定价、通常盈利的成熟龙头也基本没意义，这道筛子本来就是为瓶颈型早期公司补的缺口），不代表进了watchlist之后3.7的常规估值台账也不用做——龙头照样要有分析师一致预期/同业倍数/终值法这些数据支撑"贵不贵、贵多少"，不能因为"反正是龙头"就跳过。Layer1候选唯一的真实区别是：**3.1/3.1a技术面这部分需要额外多做判定**（更仔细看六信号+趋势闸门+`market_calibration.json`概率校准，因为这是回答"现在是不是错杀窗口"这个入场逻辑核心问题的直接依据），是"3.1这一层加强"，不是"只看3.1"。
5. `industry_discovery_log.jsonl`里这类记录的`notes`字段只需要说明"这是Layer1龙头、加入watchlist后走完整3.1-3.8流程、其中3.1技术面判断是入场决策的核心依据"，**不写任何时点性的价格/信号判断**——加不加watchlist是用户的决定，加了之后要不要买是每天Phase A的常规产出，这一步不越权替后面两个环节下结论。

### 第三步：瓶颈识别

对Layer 2-3每个环节，按6条标准打分(供给集中度/扩产周期/替代难度/产能利用率/需求增速/客户验证周期，每条🔴/🟡/🟢)：≥4个🔴→**S级**(单点故障，最高优先级)，3个🔴→**A级**，1-2个🔴→**B级**，没有🔴→不算瓶颈跳过。写进`super_trend_map.json`的瓶颈地图，标注评级和6条各自的具体理由，不能只给一个字母评级不说依据。**`demand_growth`这一条的理由必须能对应回2.2的传导链**（引用具体是哪个Layer0/Layer1关键节点的哪个行为在驱动这个环节的需求增长），不能只贴一个历史增速百分比了事。

### 第四步：从瓶颈到候选（接入"行业主题候选发现"，不重复写一套）

对每个S级/A级瓶颈，把这个瓶颈环节的名字(比如"HBM先进封装载板")当成下面"行业主题候选发现"第1步的输入词，直接复用它已有的美股+ADR扫描→严格版硬指标粗筛→行业级偏见自查这一套流程，不另写一套平行逻辑。**额外补一道早期公司估值红绿灯检查**（见下）——瓶颈型标的经常是小市值甚至尚未盈利的公司，"行业主题候选发现"复用的3.2a质量闸门/3.7终值法在这类公司身上大概率直接返回`no_data`或"本财年仍亏损，模型不适用"，需要一套专门给早期公司设计的估值检查补这个缺口，不是跳过估值不查。

### 估值红绿灯（早期/小市值公司专用，补3.2a/3.7对亏损公司失效的缺口）

- **红灯**(满足任一→信号强度封顶★★，标注"估值透支")：市值>TAM的20%；PS>30x且收入增速<100%(增速>100%可豁免但仍要标注"需持续验证")；市值>5年乐观收入预测的10倍；近60天内有过增发且股价翻倍(情绪驱动特征明显)。
- **黄灯**(需要额外解释才能进入下一步)：亏损+PS>15x；PS是同业已盈利公司的5倍以上；PE>80x。
- **绿灯**(加分项)：PS<10x且收入在增长；PE<30x且有护城河。
- **安全边际检验(每个候选必做)**：用`financial_rigor.three_scenario_valuation()`算"当前价格买入，10年后按25x PE退出，年化回报是多少"，年化回报<10%标"不具备安全边际"。这条跟3.7 E2用的是同一个工具、同样"不能心算"的要求，但退出倍数(25x)是给早期成长股的经验值，跟E2里取值更贴合已上市成熟公司同业/分析师区间不是同一套假设，两处的25x/取值依据不能互相混用当作同一个数字。

### 反向验证（芒格式，进入watchlist候选清单前必做）

逐条找到具体答案，找不到如实标"未查到反面证据"，不能跳过不做：聪明人为什么不买这只股票？这个瓶颈能不能被替代技术绕过？中国/其他玩家能不能很快复制产能？下游终端需求如果放缓50%这家公司会怎样？管理层有没有在高点增发稀释过？

### 提醒节奏

`super_trend_map.json`的`last_confirmed_date`距今超过90天(约一季度)时，下次跑`PHASE_C_TASK.md`(组合层面审视)要在输出里加一句提醒"超级趋势判断已经N天未更新，建议重新跑一遍"——不新开一个定时任务，搭Phase C本来就是每周/每季度手动触发的既有节奏，顺带检查这个字段成本很低。用户随时可以手动触发，不需要等提醒。

## 可选：行业主题候选发现（不是每轮都用，新增2026-09-11，方法论改写自[ai-berkshire](https://github.com/xbtlin/ai-berkshire)`skills/industry-funnel.md`）

**用途**：Step 1的候选来源(`watchlist.json`手动维护 + 动量类预置筛选)都是被动的，没有"我判断某个行业/主题有机会，系统性找出这个主题下所有值得关注的美股"这种主动发现能力。这一节补这个空白——**用户明确提出一个行业/主题词时才触发**（可以直接由用户指定，也可以是上面"超级趋势确认"第四步传下来的瓶颈环节名），不是Phase A每轮自动跑的一部分，也不直接写`pending_proposals.jsonl`，产出是"建议加入`watchlist.json`的候选清单"，加不加还是人工决定。

**执行流程**：

1. **市场扫描**（范围收窄到美股+相关ADR，原版skill的A股/港股/日韩台欧扫描对我们没意义）：`WebSearch`该行业/主题相关的NASDAQ/NYSE上市公司+相关ADR，尽量覆盖大中小市值，不要只搜龙头——产出候选清单：代码+一句话定位+是否"纯正标的"(主营占比高 vs 多元化公司里只是沾边一块业务)。预期产出量：视主题宽窄而定，没有硬性数字要求，宁可多列不要遗漏明显相关的标的。

2. **硬指标粗筛**（比3.2a的7指标去劣闸门**门槛更严格**——3.2a是"排除确定不好的"，这里是"从一大批里挑出配得上花时间深挖的"，两套门槛服务的目的不同，不要混用）：
   - 复用`fundamentals_analysis.get_quality_screen(symbol)`算出的原始数值（`avg_roe`/`cumulative_fcf`/`avg_ocf_to_ni`），但这里按更严格的标准重新判定：avg_roe>15%(不是8%)、OCF/NI(5年均值)>0.7(沿用)、5年累计FCF为正(沿用)。
   - 资产负债率<60%：`market_data.get_balance_sheet(symbol, freq='annual')`读最新一年`Total Debt`/`Total Assets`，用`financial_rigor.exact_calc()`算比值，不心算。
   - PE合理性：`info['trailingPE']`跟`market_calibration.json`该行业同业中位数(若已建池)或`industry.compare_peer_returns()`横向比，明显高于同业中位数且没有对应的增速优势时标"偏贵"，不是硬性否决项，是disclosure。
   - 护城河快评★1-5：定性判断，五类护城河（品牌定价权/转换成本/网络效应/规模效应/技术牌照壁垒）逐条打分，参考`PHASE_A_TASK.md` 3.2的生意本质框架，不是另起一套标准。
   - **保留规则**：以上四条全过→保留；3条过+1条接近→保留但标注"边界"；不足3条→淘汰，注明理由，淘汰的也要留名字，不能黑箱。目标缩到10家以内，超过则把护城河门槛提到★★★★再筛一轮。

3. **行业级筛选偏见自查**（原版skill专门为"扫描一整个行业"这个场景写的清单，跟3.5"综合裁决"里针对单个候选的偏见自查是不同维度，两个都要做，不能互相替代）：
   - 龙头偏好：大市值公司资料多、分析篇幅容易更长，不代表真的更值得投，按硬指标和护城河打分不按资料量排序
   - 故事偏好：高涨幅+媒体热度高的"概念股"，要区分"该业务真实收入占比"和"故事占比"
   - 当下偏好：只看当前财务数据好的公司，容易漏掉转型期但趋势在改善的候选
   - 上市偏好：只扫描已上市公司可能漏掉产业链里关键的未上市/近期IPO玩家，标注一句"未来IPO候选"供参考，但这类候选进不了后续流程(本系统只交易已上市美股)

4. **产出**：粗筛后剩下的候选，逐一标注"已在watchlist.json里"还是"建议新增"，追加写入新文件 `industry_discovery_log.jsonl`（append-only，仅在触发本流程时写，不是每天写），记录本次扫描的主题词、日期、完整候选清单(含被淘汰的和理由)、粗筛后名单。**不自动写入`watchlist.json`**——`watchlist.json`的`symbols`字段是人工维护的既定原则(见README)，这一步只产出建议清单，加不加、加哪些由用户决定，用户确认后手动编辑`watchlist.json`。
5. 用户确认新增的symbol，下一轮Phase A会按"自选股首次强制深度调研"（Step 2e）正常纳入流程，不需要为这条发现路径单独开辟一套调研逻辑。
