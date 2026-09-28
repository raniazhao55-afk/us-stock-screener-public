# us-stock-screener

美股版每日筛选 + 投研论点生成系统，跑在 [Claude Code](https://claude.com/claude-code) 里。架构借鉴 [FriesTrader](https://github.com/YizhiSong/FriesTrader) 的纪律设计（机械风控优先、dry-run门槛、append-only审计日志、仓库即持久化状态），数据层改用 [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents) 的 `dataflows/` 模块（免费无key，Apache-2.0，只借数据获取代码，不用它的LangGraph多智能体编排），公告类数据用 SEC EDGAR 官方披露接口（免费无key）——都只借设计/代码思路，推理仍然全部在 Claude Code 会话内用 Pro/Max 订阅完成，不接按 token 计费的外部 LLM API。

**现金账户，无自动下单。** Robinhood Agentic Trading 目前是美国居民专属 beta（2026年多个信源确认"US-only sandbox"），本系统设计上完全不连接 Robinhood 或任何券商API——Phase B 只生成"今日操作清单"，下单永远人工在券商 App 完成。

**这是一个个人项目的空白模板**：仓库里的方法论、风控规则、数据层代码都在，但不含任何人的持仓、交易记录或历史论点——这些是你自己运行后才会生成的本地状态。**不构成投资建议**，作者的任何历史回测/论点都不代表未来收益，风险自负。

> **想直接跑起来？先看 [SETUP.md](SETUP.md)。** 下面是系统设计说明。

## 设计原则

- **只用 Claude Pro/Max 订阅**：所有推理都在 Claude Code 会话内完成（手动触发或 Claude Code 自带的定时 routine），不接外部 LLM API，不产生按量计费的开销。
- **不自动下单**：系统只做到"生成今日操作清单"，最后一步下单永远由人工在券商 App 完成——即便美股（尤其Robinhood Agentic Trading）实际具备自动下单能力，本系统也刻意不用，保守优先。
- **机械风控优先于 AI 判断**：仓位上限、止损、GFV结算违规守卫、部署节奏(单日最多新开几仓/单周最多投入多少资金)等规则写死在 `risk_rules.json`，AI 不能绕过或自己修改。
- **仓库即状态**：`pending_proposals.jsonl`、`trade_log.jsonl`、`sentiment_history.jsonl` 都是这个仓库的一部分，每次运行后 git commit，形成完整审计轨迹。

## 三阶段流程

- **Phase A**（交易日收盘后，`PHASE_A_TASK.md`）：构建候选池 → 抓信号（含机构持仓/内部人交易/分析师评级变化的趋势对比）→ 生成投研论点(含估值台账/持仓论文台账) → 覆盖写 `pending_proposals.jsonl`。不下单，不生成操作清单。
- **Phase B**（次日开盘一段时间后，`PHASE_B_TASK.md`）：用最新数据复核论点 → 机械风控过滤（含现金账户GFV结算守卫）→ 生成"今日操作清单" → 追加写 `trade_log.jsonl`。仍然不下单。
- **Phase C**（建议每周一次，`PHASE_C_TASK.md`，2026-09-11新增）：Phase A/B都是逐票视角，Phase C审视"这些票放在一起是不是合理的组合"——集中度/相关性/机会成本排序/压力测试，复用Phase A已有数据不重新研究，输出建议不生成操作清单，追加写 `portfolio_review_log.jsonl`。

下面三节把每个阶段的每一步展开，都是`PHASE_*_TASK.md`原文的摘要，不是另一套说法——细节和例外情况以原文件为准。

### Phase A 具体做什么

**Step 1 构建候选池**：读`watchlist.json`的`symbols`+`held_positions` → 用`risk_rules.json`的`universe.supplementary_scan_type`(默认`most_actives`/`undervalued_growth`/`day_gainers`/`day_losers`四种)各拉一批补充候选、按symbol去重合并 → 对每个候选机械过滤(市值上限/仙股价格阈值/OTC粉单/上市天数不足/排除交易所/报价异常) → 按`watchlist_max_candidates`/`supplementary_scan_max_candidates`截断 → **`held_positions`里的票无论过没过滤都强制保留**。候选来源只有"手动维护watchlist"+"被动动量扫描"两条，都是被动来源。

**Step 2 抓信号**：2a价格/量能信号(60日涨跌幅、量比、距52周极值) → 2b趋势信号(机构持仓/内部人交易/分析师评级变化) → 2d按"偏离阈值程度"打分排队，只有`signal_triggered_research_budget`(默认10)个名额真正深挖，排不进的标`deprioritized_by_budget`(不是否定判断，只是这轮没排上)——**但`held_positions`、首次调研的新自选股(2e)、`undervalued_growth`来源的基本面豁免候选，这三类完全不占这个预算，每轮都强制出论点**。

**Step 3 生成投研论点**：对进入这一步的每个候选依次做3.1技术面(六信号+K线形态)→3.1a月周趋势闸门(算止损位+封顶conviction)→3.2基本面/8-K→3.2a质量去劣闸门(7条硬指标)→3.2b资产负债表异常扫描→3.3资金面→3.3a行业同业对比→3.3b定量概率校准(同业池历史涨跌分布)→3.4新闻去重→3.5综合裁决(先列失败路径再选最强反方证据，定conviction)→3.6方向判定(定direction)→3.7估值台账(仅`direction=long`时，公司指引+分析师预期+同业倍数+指数倍数+终值法+三情景+分析师分歧原因六个视角)→3.8持仓论文台账(仅持仓且long时，建假设清单+分级红线+健康度评分)。

**Step 4 输出**：整体覆盖写`pending_proposals.jsonl`(不是追加)，未触发信号的候选写`screened`记录，末尾写summary分桶汇总，追加`sentiment_history.jsonl`，git commit。

### Phase B 具体做什么

次日开盘一段时间后跑，**不下单**，只生成人工执行用的操作清单。

**Step 1 复核**：拿最新报价判断今日是否疑似不可交易；判断论点是否还成立(价格大幅偏离基准→`thesis_stale`，invalidation条件已发生→`invalidated`归`avoid`)；**周线价格止损**——仅对`is_held:true`的持仓生效，`trend_gate.price_stop_loss_triggered`为true时**无论当天direction是什么(哪怕是long)一律强制归为`sell`**，这是独立于论点判断的价格纪律；**持仓论文台账检查**——读`thesis_ledger.json`的健康度评分，健康度≤2且触发`severity:fatal`红线时同样强制`sell`，其余情况只在`reason`里提示不强制。

**Step 2 机械风控过滤**（AI不能绕过，规则全部来自`risk_rules.json`）：单票仓位上限 → 单日/单周亏损上限(触发后当天/当周只能sell/hold不能新开仓) → **最大并发持仓数**(用`held_positions`当前真实笔数比，**不能预支同一轮里其他候选"待执行的sell"腾出的名额**——sell建议再明确，没在券商App里真实执行、`watchlist.json`没同步更新之前都不算数) → 现金账户GFV结算守卫(取代A股的T+1，用未结算资金买入的仓位在结算完成前不能卖，查不清楚按`not_tradeable`保守处理) → 不可交易检查。全部通过后，`size_pct_of_capital`按`position_sizing_by_conviction`从conviction直接查表(high 20%/medium 12%/low 6%)，不是临场估的数字。

**Step 2b 仓位偏离提示**：持仓实际仓位%跟conviction对应的目标仓位差超过2个百分点时，只在`reason`里提示"可考虑补仓/仓位超出目标"，**action仍是hold，不自动变成buy/sell**。

**Step 2c 部署节奏限制**：只影响新开仓，按"conviction高低→无警告技术信号优先→历史回撤概率更低优先→symbol字母序"排队，超过`max_new_positions_per_day`的候选标`defer`(不是论点有问题，是节奏被延后)。**"今天用了几个新开仓名额"必须查`held_positions`里`date_acquired`是今天的真实条目数，不能用`trade_log.jsonl`里的buy推荐条数算**——推荐不等于执行。

**Step 3-4**：生成操作清单(buy/sell/hold/avoid/defer，`avoid`/`not_tradeable`/`defer`的条目也要写清理由留审计轨迹)，追加写`trade_log.jsonl`，git commit。

### Phase C 具体做什么

建议每周一次，跟Phase A/B不是同一套视角——A/B都是逐票判断，C回答"这些票放在一起是不是合理的组合"，**复用Phase A已有数据，不重新做研究**，不生成操作清单。

**Step 0**：检查`super_trend_map.json`的`last_confirmed_date`是否超过90天，超过就在输出开头提醒一句"该重新跑超级趋势确认了"(见下节)。

**Step 1 解析持仓**：读`held_positions`，算每只的市值/占比/盈亏%，`现金占比=1-持仓占比总和`(不是真实到账现金，是隐含值)。

**Step 2 单仓位体检**：直接复用`thesis_ledger.json`的健康度评分 + `pending_proposals.jsonl`里已有的`valuation_ledger`隐含价，**不重新调用yfinance/SEC**。

**Step 3 组合层面分析**：3.1集中度(第一大持仓占比/前三大占比/持仓数/隐含现金占比) → 3.2相关性与隐性关联(复用`market_calibration.json`的行业分组，找出"看似不同其实同向"的持仓组合，比如同属半导体或同受一国汇率影响) → 3.3机会成本排序(用已有隐含价算"确定性加权预期年化"，跟WebSearch实时查到的货币市场基金收益率对比，排名最后且低于这个基准的持仓要点出来，但不直接建议清仓) → 3.4压力测试(定性，几个粗粒度宏观情景下哪些持仓受冲击最大)。

**Step 4-5**：输出调仓建议表(加仓/减仓/清仓/继续观察/不动)，**这是建议不是操作清单**，不会被Phase B自动消费；追加写`portfolio_review_log.jsonl`，git commit。

## 可选工具：超级趋势确认与供应链瓶颈发现

`PHASE_A_TASK.md`末尾"可选"这一节，**不在每天自动跑的Step1-4里**，是给候选池找新symbol的主动发现工具，方法论改写自[xbtlin/ai-berkshire](https://github.com/xbtlin/ai-berkshire)的`bottleneck-hunter.md`。

**什么时候会被调用**：
1. **人工手动触发**——直接说"帮我判断一下XX趋势"，或者不指定、从文件里给的起始清单(AI基础设施建设/能源转型/国防现代化/半导体再工业化/太空经济)逐个过。
2. **Phase C的Step0过期提醒**——`super_trend_map.json`的`last_confirmed_date`超过90天(约一季度)，跑Phase C时会提醒一句，但仍需要你手动去触发，不会自动执行。

**四步流程**：
1. **超级趋势确认**：4条硬标准(持续性≥3-5年/物理性(要实际硬件建设，不是纯概念)/规模性(全球资本开支>500亿美元/年)/加速性(需求增速>供给扩产速度))全部满足，且找到至少3个**已经发生**的验证事件(带日期来源，不是预测)，才算"确认"；任一条不满足如实标"证据不足"，同样记录留痕。
2. **供应链物理拆解**：把确认的趋势拆成Layer0(终端)→Layer1(核心组件，通常已被市场充分定价)→Layer2(子组件/材料，**alpha集中区**)→Layer3(上游设备原料)→Layer4(基础设施)，同时画出客户-供应商关系图(谁向谁采购什么，是直接采购/技术规格派生需求/供应商即客户自己/政策资助驱动)，重点扫描没人盯着的Layer2-3。
3. **瓶颈识别**：对Layer2-3每个环节按6条标准(供给集中度/扩产周期/替代难度/产能利用率/需求增速/客户验证周期)打分，≥4个红灯→S级(最高优先级)，3个→A级，1-2个→B级，没有红灯不算瓶颈。
4. **从瓶颈到候选**：S/A级瓶颈接入"行业主题候选发现"(市场扫描→严格版硬指标粗筛→行业级偏见自查)，额外加一道给亏损早期公司专用的估值红绿灯(常规估值台账对这类公司常常失效)+芒格式反向验证5问。**Layer1的已充分定价龙头也会被记录**，但走的是完全不同的逻辑——不是"低估"，是"验证过的龙头，等技术性错杀时介入"，这类候选进watchlist后照样跑完整3.1-3.8，不会因为是"龙头"就走捷径。

**产出**：候选清单写进`industry_discovery_log.jsonl`(含被淘汰的和理由，不只记通过的)，**不自动写入`watchlist.json`**——加不加、加哪些由你自己决定，确认要加的symbol手动写进`watchlist.json`的`symbols`后，就变成普通watchlist symbol，交给Phase A的日常流程处理。

## 文件说明

| 文件 | 作用 |
|---|---|
| `risk_rules.json` | 账户参数（USD，现金账户）、候选池规则、信号阈值、执行模式 |
| `watchlist.json` | 自选股代码列表（人工维护）、`held_positions`真实持仓（唯一可信来源） |
| `PHASE_A_TASK.md` | Phase A 完整执行规范 |
| `PHASE_B_TASK.md` | Phase B 完整执行规范 |
| `PHASE_C_TASK.md` | Phase C（组合层面审视）完整执行规范，2026-09-11新增 |
| `pending_proposals.jsonl` | Phase A 输出，每轮整体覆盖 |
| `trade_log.jsonl` | Phase B 输出，append-only，完整审计轨迹 |
| `portfolio_review_log.jsonl` | Phase C 输出，append-only，2026-09-11新增 |
| `sentiment_history.jsonl` | 每日候选票的机构持仓/内部人交易/评级快照，append-only，供趋势对比用 |
| `researched_baseline.json` | 记录每只自选股"第一次被深度调研"的日期，新加入 watchlist 的票首轮强制调研不受信号阈值限制 |
| `market_calibration.json` | 同业池化统计校准曲线，慢变量，Phase A只读取，构建方法见`PHASE_A_TASK.md`末尾 |
| `valuation_cache.json` | 估值台账(3.7 A-E)缓存，2026-09-11新增，按新8-K/7天有效期刷新，见`PHASE_A_TASK.md` 3.7a |
| `thesis_ledger.json` | 持仓论文追踪台账，2026-09-11新增，只对`is_held:true`的long持仓生效，见`PHASE_A_TASK.md` 3.8 |
| `industry_discovery_log.jsonl` | 行业主题候选发现记录，append-only，2026-09-11新增，只在手动触发该可选流程时写入，见`PHASE_A_TASK.md`末尾"可选：行业主题候选发现" |
| `super_trend_map.json` | 超级趋势确认 + 供应链瓶颈地图，2026-09-11新增，人工触发(建议每季度)，`last_confirmed_date`过期90天由Phase C提醒，见`PHASE_A_TASK.md`末尾"可选：超级趋势确认与供应链瓶颈发现" |

## 和 a-stock-screener 的关键差异

| | a-stock-screener (A股) | us-stock-screener (美股) |
|---|---|---|
| 数据层 | a-stock-data skill (东财/腾讯等) | us-stock-data skill (yfinance + SEC EDGAR) |
| 涨跌停/T+1 | 主板10%/创业板20%涨跌停锁死，T+1不能当天卖出买入的票 | **美股无每日涨跌幅限制**；账户是现金账户，用 **Good Faith Violation 结算守卫**替代——用未结算资金买入的仓位，结算完成前不能卖 |
| 补充扫描 | 强势股/涨停池/龙虎榜/北向资金/概念热点 | Yahoo预置screen（最活跃/最大涨幅/做空最多等）——龙虎榜/北向这类无美股对应披露制度的直接砍掉，不硬凑 |
| 公告类数据 | 巨潮公告 `cninfo_announcements()` | SEC EDGAR 8-K 全文/列表（法定强制披露，性质最接近） |
| 行业池化校准 | 按"主板/创业板"板块类型兜底 | 按"大/中/小市值"分层兜底（无涨跌停机制可对标） |

## 首次运行前需要做的事

1. 编辑 `watchlist.json`，填入你的自选股代码（美股ticker，如 `AAPL`）。
2. 检查 `risk_rules.json` 里的每一个阈值——默认值只是示例，不是建议，尤其 `account` 和 `universe.exclude_*`。
3. 设置 `SEC_EDGAR_CONTACT` 环境变量（SEC要求真实联系方式的User-Agent，否则请求会被403），见 `~/.claude/skills/us-stock-data/SKILL.md`。
4. 先在 Claude Code 里手动触发一次 Phase A（不设定时任务），看 `pending_proposals.jsonl` 输出是否合理。
5. 跑通后再考虑挂 Claude Code 的定时 routine。
6. `execution.mode` 保持 `"dry_run"`，人工审阅若干轮 `trade_log.jsonl` 后再评估是否需要调整——即便调整，本系统也不具备下单能力，这只影响系统内部的措辞/提示逻辑。

## 持续维护：`held_positions` 必须手动更新

系统不接券商账户，不知道你实际持有什么。`risk_rules.json` 里的 `max_concurrent_positions` 和 `max_position_pct_per_symbol` 这两条风控，靠的是 `watchlist.json` 里 `held_positions` 这个字段判断"你现在手上有几只、分别是哪些"。

**每次你在券商App里真实买入或卖出后，回来手动更新这个字段**——不维护这个字段，Phase B 的并发持仓数检查就没有真实数据可用，风控形同虚设。

## 数据依赖

需要本地装好 `us-stock-data` skill（仓库自带在 `skills/us-stock-data/`，装到 `~/.claude/skills/` 的具体步骤见 [SETUP.md](SETUP.md)；依赖装进独立 venv，避免污染系统 Python）。三层数据全部免费无需付费key，仅 SEC EDGAR 要求设置 `SEC_EDGAR_CONTACT` 环境变量。

## License

见 [LICENSE](LICENSE)。数据层代码移植自 [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents)（Apache-2.0）、[HKUDS/Vibe-Trading](https://github.com/HKUDS/Vibe-Trading)（MIT）、[xbtlin/ai-berkshire](https://github.com/xbtlin/ai-berkshire)（MIT）的部分模块，各自版权声明保留在 `skills/us-stock-data/LICENSE_*` 与相关文件头部。
