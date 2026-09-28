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
