# 美股数据工具包 v0.1（us-stock-data）

给 `us-stock-screener` 用的最小可用数据层。**不是**要复刻 a-stock-data 的十层47端点规模——那是它自己在真实运行中踩坑迭代出来的（见其 CHANGELOG）。这里先建够 Phase A/B 跑起来的三层，之后跟着真实运行的坑再加。

**全部函数免费、无需 API key**（除 SEC 要求的 User-Agent 联系方式，见下）。数据获取代码大部分移植改造自 [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents) 的 `dataflows/` 模块（Apache-2.0，见 `LICENSE_TRADINGAGENTS`）——那是它和自家 LangGraph 多智能体编排解耦的纯数据层，本身不调用任何按 token 计费的 LLM。

## 环境准备

```bash
# 已装好的独立venv，不污染系统Python
~/.claude/skills/us-stock-data/.venv/bin/python3

# SEC EDGAR 要求每个请求带真实联系方式的 User-Agent，否则403。自己设置：
export SEC_EDGAR_CONTACT="Your Name your.email@example.com"
```

调用方式：把下面的函数当模块用，`PYTHONPATH` 指到技能根目录：

```bash
PYTHONPATH=~/.claude/skills/us-stock-data ~/.claude/skills/us-stock-data/.venv/bin/python3 -c "
from lib import market_data
print(market_data.get_fundamentals('AAPL'))
"
```

## 端点路由速查

| 需要什么 | 调哪个函数 | 来源 |
|---|---|---|
| 日K OHLCV | `market_data.get_YFin_data_online(symbol, start, end)` | yfinance |
| 当前报价(现价/前收/日高低/量/市值) | `market_data.get_quote(ticker)` | yfinance fast_info |
| 技术指标(单指标近N天序列) | `market_data.get_stock_stats_indicators_window(symbol, indicator, curr_date, look_back_days)` | yfinance+stockstats |
| 基本面概览(PE/市值/margin等) | `market_data.get_fundamentals(ticker)` | yfinance |
| 资产负债表/现金流/利润表 | `market_data.get_balance_sheet/get_cashflow/get_income_statement(ticker, freq, curr_date)` | yfinance |
| 内部人交易(Form 4) | `market_data.get_insider_transactions(ticker)` | yfinance |
| 机构持仓(13F衍生) | `market_data.get_institutional_holders(ticker)` | yfinance |
| 分析师评级分布+近期升降级 | `market_data.get_analyst_recommendations(ticker)` | yfinance |
| 个股新闻 | `news.get_news_yfinance(ticker, start, end)` | Yahoo Finance |
| 宏观/大盘新闻 | `news.get_global_news_yfinance(curr_date)` | Yahoo Finance Search |
| 公司近期8-K(重大事件公告，巨潮公告的美股对应物) | `sec_edgar.get_recent_8k_filings(ticker, limit)` | SEC EDGAR submissions API |
| 读取8-K原文全文(遇到`[READ REQUIRED]`标记时必须调用) | `sec_edgar.get_filing_text(url, max_chars=6000)` | SEC EDGAR，去HTML标签的纯文本 |
| 公司全部近期filings(不限form) | `sec_edgar.get_recent_filings(ticker, forms=None, limit)` | SEC EDGAR submissions API |
| 跨公司关键词全文检索 | `sec_edgar.search_filings_fulltext(query, forms, ciks, date_from, date_to)` | SEC EDGAR full-text search |
| 补充扫描候选(强势股/最活跃/做空最多等) | `screener.run_supplementary_scan(scan_type, limit)` | yfinance predefined screens |
| 行业分类+同业权重/评级榜+行业内YTD排名 | `industry.get_industry_snapshot(ticker, peer_limit=12)` | yfinance Sector/Industry |
| 指定几只票的1d/5d/20d/60d涨跌幅对比 | `industry.compare_peer_returns(symbols, curr_date)` | yfinance OHLCV |
| RSI+MACD+MA共振技术信号(上车/下车/启动/爆发/顶部反转/顶背离) | `technical_signals.check_all_signals(symbol, curr_date)` | yfinance+stockstats，见下方专节 |
| 通用技术形态(金叉死叉/MACD独立金叉死叉/多头空头排列/布林挤压突破/放量突破确认) | `chart_patterns.check_all_patterns(symbol, curr_date)` | yfinance+stockstats |
| 7指标去劣质量闸门(ROE/FCF/利息覆盖/毛利率/OCF-NI/净利率/股本稀释+豁免判定) | `fundamentals_analysis.get_quality_screen(ticker)` | yfinance年度三大报表，见下方专节 |
| 资产负债表异常扫描(应收/存货增速跑赢营收、OCF-NI缺口扩大、资本化开支突增) | `fundamentals_analysis.get_balance_sheet_anomalies(ticker)` | yfinance年度三大报表 |
| 近4季度分析师EPS一致预期兑现历史(surprise%+beat_rate) | `fundamentals_analysis.get_earnings_surprise_history(ticker)` | yfinance `earnings_history` |
| 市值/估值倍数精确验算(Decimal，禁止心算) | `financial_rigor.verify_market_cap/verify_valuation(...)` | 纯计算，零外部依赖 |
| 多源数据交叉验证(取中位数+容差标记离群源) | `financial_rigor.cross_validate(field, values, unit, tolerance_pct)` | 纯计算 |
| Benford定律财务数字造假快检(需≥50个样本) | `financial_rigor.benford_check(values)` | 纯计算 |
| 牛/中/熊三情景N年目标价(替代/互补3.7E终值法) | `financial_rigor.three_scenario_valuation(...)` | 纯计算 |

技术指标名单（`get_stock_stats_indicators_window`/`get_stockstats_indicator` 的 `indicator` 参数）：`close_50_sma`/`close_200_sma`/`close_10_ema`/`macd`/`macds`/`macdh`/`rsi`/`boll`/`boll_ub`/`boll_lb`/`atr`/`vwma`/`mfi`。

补充扫描类型（`screener.SUPPLEMENTARY_SCAN_TYPES` 的 key）：`day_gainers`/`most_actives`/`day_losers`/`most_shorted`/`undervalued_growth`/`small_cap_gainers`/`aggressive_small_caps`。

## When to Activate

`us-stock-screener` 的 Phase A / Phase B 需要任何美股行情/基本面/公告/新闻数据时。

## 已知环境适配（2026-08-23首次验证记录）

- **三层全部用 AAPL(大盘)/DUOL(中盘)/RDDT(近期IPO小盘) 三只真实标的验证通过**：OHLCV、技术指标、基本面、三大报表、内部人交易、机构持仓、分析师评级、新闻、SEC 8-K、SEC全文检索、补充扫描（day_gainers/most_shorted）全部拿到真实数据，没有一个是空跑。
- **SEC全文检索的 `_id` 字段不是现成URL**：格式是 `{accession带横杠}:{文件名}`，横杠去掉+配合 `_source.ciks[0]` 才能拼出可点击的 `sec.gov/Archives/edgar/data/...` 链接——首次实现漏了这步，拼出来的URL打不开，已在 `sec_edgar.py` 修好。
- **8-K的items编号不是都能"望文生义"**（2026-08-23发现的系统性漏洞）：`1.01`/`2.03`/`8.01`等几个编号本身不透露具体内容，之前的用法是"看到编号就当作催化事件已确认"，直到发现NVDA一条`1.01,2.03,7.01`的8-K实际是NVIDIA为OpenAI的数据中心租约做了1050亿美元的剩余价值担保——这种规模的信息光看编号完全看不出来。现在`get_recent_8k_filings()`会给需要读原文的行打`[READ REQUIRED]`标记（编号列表见`sec_edgar.ITEMS_REQUIRING_FULL_READ`），标记出现时必须调`get_filing_text(url)`读原文，不能只看编号就下结论。
- **`yfinance.screen()` 已知会不稳定**（社区issue `ranaroussi/yfinance#2419` 记录过 Yahoo 服务端改动导致整个端点失败，不是版本没更新的问题）。本次验证时能跑通，不代表以后一直稳定——`screener.run_supplementary_scan()` 设计成失败时返回空列表而不是抛异常，调用方（Phase A Step 1）要把"补充扫描今天没有候选"当正常情况处理，不要因此中断整轮筛选。
- **SEC请求限流**：`sec_edgar.sec_get()` 内置约4次/秒的节流+随机抖动（SEC官方上限10次/秒），批量跑一整个watchlist不会撞到限流——这条纪律是从 a-stock-data 自己"东财封IP"的教训（[[eastmoney-selfinflicted-rateban]]）直接搬过来的：先主动限流，不要等被封了才处理。
- **`normalize_symbol()` 不处理股票分类代码的点/横杠转换**：Yahoo 要 `BRK-B`，但有些来源写 `BRK.B`。目前watchlist里如果加了这类代码（伯克希尔B股、福特B股等），先手动确认成 Yahoo 认的横杠写法，出现"查不到数据"时第一反应检查这个，而不是假设标的错误。
- **`get_cik()` 查不到纯OTC/粉单股票**：SEC `company_tickers.json` 只收录在SEC注册报告的公司，纯OTC不受SEC全套披露义务约束的股票会查不到CIK——这类票本来也该被 `risk_rules.json` 的 `exclude_otc_pink_sheet` 过滤掉，查不到属于预期行为，不是bug。

## 行业同业对比（2026-08-23新增）：`industry.py`

补上了 us-stock-data 一直缺的一块——a-stock-data 那种"同业股票池涨跌对比"。用 `yfinance.Industry(industryKey)` 免费拿：
- `top_peers_by_weight`：该行业按市值权重排的同业公司+评级（已排除自身）
- `top_performers_ytd` + `ticker_ytd_rank_in_industry`：**这只票在全行业(不只是watchlist)里YTD涨幅排第几**——首次验证时查到SNDK在"Computer Hardware"(75家)里排第1(YTD+572%)，MU在"Semiconductors"(75家)里排第2(YTD+239%)，这条排名信息之前完全没有
- `industry_overview`：行业公司数/总市值/行业描述

`compare_peer_returns(symbols, curr_date)` 补充做1d/5d/20d/60d涨跌幅横向对比表，需要显式传一小串代码(比如目标票+`get_industry_snapshot`拿到的几个权重最高的同业)，不要传太多(每个都要单独拉一次OHLCV)。

**和 `market_calibration.json` 的分工**：`industry.py`回答"我的同业现在长什么样、我在里面排第几"（实时快照）；`market_calibration.json`回答"这种涨幅/换手率历史上未来5天跌5%+的概率有多高"（池化统计的经验曲线，慢变量）。两个不是一回事，都要用。

**已知局限**：部分ADR/外国发行人在yfinance里没有`industryKey`，`get_industry_snapshot()`这时返回`{"ok": false, "error": ...}`而不是抛异常；有些行业类目定义得很窄导致"同业"名不副实——比如AAPL被分到"Consumer Electronics"，这个类目里AAPL自己占了99.9%的市值权重，实际没有真正意义上的同业可比，遇到这种情况如实说"该行业分类下无有效同业"，不要硬凑对比。

**2026-08-23：`top_peers_by_weight`直接拿来给`us-stock-screener`的`market_calibration.json`当同业池用了**——原来3.3b写的时候还在设想"手动列一批同行业股票凑池"，现在`industry.py`能免费自动拿到，`us-stock-screener/PHASE_A_TASK.md`的"如何构建market_calibration.json"一节已更新成实际跑通的流程。

## RSI+MACD+MA共振技术信号（2026-08-23新增）：`technical_signals.py`

**这是用户提供的一套具体零售技术策略（附6张图）的实现，不是通用技术分析框架**——上车/下车/启动/爆发/顶部反转/顶背离六个信号，RSI白/黄/紫三线对应RSI6/12/24。详细定义、量化口径(什么算"零轴附近"、"一阳穿多线"怎么判定、"最近才形成"的时间窗口)全部写在模块的docstring里，不在这里重复。

**v1→v2教训**：v1把"共振"理解成"三个指标在同一个3天窗口内各自都要有新鲜交叉"，结果在MSFT 7/29-30那次教科书级别的暴涨上测出0信号——因为RSI领先3天穿越、MACD更早穿越、MA5隔了3天才追上，三者根本不在同一窗口。v2改成"状态+最近才形成"（检查当前是不是处于对齐状态，且这个状态不是老早以前就有的），重测后MSFT/NVDA测出顶部反转信号、MU测出上车信号，和真实图形对得上。**这条教训的通用启示**：零售图表描述的"信号同时出现"，现实里几乎总是"各指标依次出现、状态持续一段时间"，写成代码时按"状态"建模比按"同一时刻的交叉事件"建模更贴近实际。

图5(顶部反转)/图6(顶背离)是原文字描述里没提到、看图才发现的额外信号，需要先做局部波峰波谷检测(`_local_maxima`/`_local_minima`)，和前四个纯交叉判断是不同的算法家族。

**`chart_patterns.py`(2026-08-23新增)是这套用户专属策略之外的通用技术面补充**——金叉/死叉(50/200日)、MACD独立金叉死叉(不要求RSI/MA同时确认)、多头/空头排列(不挂靠MACD/RSI的独立均线读数)、布林带挤压+突破、放量突破确认，五个都是标准技术分析里的常见概念，和`technical_signals.py`是互补关系不是替代关系。7只自选股验证时发现一个有意思的例子：MSFT财报后价格已经暴涨，但50日均线当时仍在200日均线下方（死叉状态），说明短期价格结构和长周期均线结构可能不同步——追高判断不能只看价格本身。

## Layer 4（可选，2026-08-23新增）：期权链 / 预测市场 / 学术因子研究

来自 [HKUDS/Vibe-Trading](https://github.com/HKUDS/Vibe-Trading)（MIT license）的三个**净增量能力**——不是重复Layer 1-3已有的东西，是我们完全没有、自己写成本也不低的三块：

| 需要什么 | 调哪个函数 |
|---|---|
| 美股期权链(strike/bid/ask/IV/OI/ITM) | `vibe_trading_optional.get_options_chain(ticker, expiration=None)` |
| Polymarket事件概率搜索 | `vibe_trading_optional.prediction_market_search(query)` |
| 按id查具体Polymarket事件 | `vibe_trading_optional.prediction_market_event(event_ids)` |
| 学术金融/ML论文搜索(arXiv+OpenAlex) | `vibe_trading_optional.research_papers_search(query, limit=10)` |
| 按id读论文+提炼factor brief | `vibe_trading_optional.research_papers_read(paper_ids)` |

**依赖**：`pip install vibe-trading-ai`（已装进本skill的venv；会把这个venv的pandas从3.0.5降到2.3.3，Layer 1-3已重新验证过降级后仍正常）。这是个几十个依赖的重型包，只用它这三个纯数据函数，**绝不导入/运行它自己的agent/swarm/LLM那一层**——那层靠外部按token计费的LLM API驱动，和本项目"只用Claude订阅推理"的原则冲突。

**只挑这三个的原因**：Vibe-Trading还有一堆和Layer 1-3重叠的工具(`get_sec_filings`/`get_financial_statements`/`get_institutional_holdings`/`screen_market`/`technical_indicators`)，实测(2026-08-23)发现真实bug，没有直接采用：
- `technical_indicators(symbol="AAPL")` **不带`.US`后缀会内部误路由到tushare/A股路径**，报"请设置tushare pro的token"然后返回"No data returned"——必须写成`"AAPL.US"`。
- `screen_market(market="us", ...)` **超时**，而且诡异地在connect `push2.eastmoney.com`(一个"us"市场扫描不该碰的中国接口)。
- `get_institutional_holdings(mode="ticker_holders")` 靠SEC"fails-to-deliver"文件反查CUSIP，这个文件只覆盖发生过交割失败的证券，对绝大多数普通股票查不到——`mode="top_managers"`没这个问题。

这些重叠工具本身架构很讲究(比如13F处理专门处理了"value字段2023年前后从千美元变美元导致1000倍误差"这种真实坑)，但接进来还是得再包一层踩坑记录，工作量和我们已验证好用的Layer 1-3差不多，不值得为了重叠功能多背一个大依赖。

## 财务严谨性工具 + 机械基本面闸门（2026-09-11新增）：`financial_rigor.py` + `fundamentals_analysis.py`

从 [xbtlin/ai-berkshire](https://github.com/xbtlin/ai-berkshire)（MIT license）深挖 `skills/` 目录20个skill正文（不是README）后新增，弥补之前只搬了`terminal_value.py`的遗漏。

**`financial_rigor.py`**（465行原版CLI工具的等价port，改成dict返回值供程序消费而不只是打印）：
- `verify_market_cap(price, shares, reported_cap, currency)`：市值=股价×股本反算校验，>5%偏差标`fail`——抓的是股本没更新(回购/增发)或单位搞混。
- `verify_valuation(price, eps=, bvps=, fcf_per_share=, dividend=, revenue_per_share=)`：从每股口径数据精确算出PE/PB/ROE/P-FCF/FCF Yield/股息率/PS，Decimal计算不用LLM心算。
- `cross_validate(field_name, source_values, unit, tolerance_pct=2.0)`：同一个数字多个来源时取中位数做共识值，标出偏差>容差的来源——CRCL的`info.forwardPE`(59.4x) vs `earnings_estimate`推算值(66.78x)这种脱节，本该用这个函数抓出来而不是靠肉眼比对。
- `benford_check(values)`：财务数字首位数分布Benford定律快检，需要≥50个样本(喂一家公司多年多科目的完整数字，不是几个头条数字)，不符合不等于造假，但值得深挖是哪类科目偏离。
- `three_scenario_valuation(...)`：牛/中/熊三情景N年目标价(EPS复利增长×退出PE)，跟`us-stock-screener/PHASE_A_TASK.md` 3.7E的终值法(永续折现)是互补关系——终值法在r≤g时数学上无定义(2026-09-10已实测撞到过这堵墙)，这个方法没有这个问题，代价是没有折现回今天的现值，纯N年后目标价。

**`fundamentals_analysis.py`**（方法论从ai-berkshire的`skills/quality-screen.md`/`skills/earnings-review.md`改写，不是照搬代码——原skill假设WebSearch拿10年数据，这里改成从yfinance结构化年度报表取数）：
- `get_quality_screen(ticker)`：7条硬指标去劣闸门(10年平均ROE≥8%/5年累计FCF≥0/利息覆盖≥2倍/毛利率≥15%/OCF平均/净利润≥0.7/净利率≥5%/5年股本膨胀≤20%)+3条豁免规则里能机械判定的A(战略投入期)/B(主动低利润率)两条，C(会员制/高周转薄利模式)需要定性判断标注`mechanically_eligible: null`留给人工。**已知数据天花板**：yfinance免费年度三大报表最多给约5年(2026-09-11在AAPL身上验证：2021-2025共5列，不是10年)，函数返回`years_used`如实报告实际拿到几年，不假装凑够了10年——调用方必须把`years_used`一起展示，不能只展示pass/fail结论。已知两个真实案例踩坑：①**回购驱动的超高ROE**(AAPL实测167%)——不是数据错误，是苹果常年大额回购把股东权益压得很低导致ROE分母失真，>50%时需要跟3.7E一样标注"数值可能失真，参考价值有限"；②**近期IPO公司的历史科目失真**(CRCL实测利息覆盖-82.9x、净利率-18.6%)——大概率是IPO相关一次性股权激励费用等因素拉低了报表期内的GAAP数字，`years_used`覆盖到IPO前后时结果需要额外谨慎，交叉参照信息丰富度分级。
- `get_balance_sheet_anomalies(ticker)`：应收账款增速>营收增速(塞渠道)/存货增速>营收增速(压库存)/经营现金流-净利润比值同比恶化/资本化开支同比>50%(资本化开支突增)四类红旗，只报flag+数值，不做verdict——是否算问题要结合具体公司情境判断，不自动扣分。
- `get_earnings_surprise_history(ticker)`：yfinance `earnings_history` 近4季度EPS实际vs一致预期的surprise%和beat_rate，跟`us-stock-screener/PHASE_A_TASK.md` 3.7A追踪的"公司自己给的指引vs实际"是两条不同的线(这个是分析师一致预期的历史准确度，不是公司官方指引的历史兑现率)，两个都有价值，不要混着当一回事。

## v0.1 明确不做（等真实运行证明这三层不够用再加）

FRED宏观数据、Alpha Vantage（需付费/免费key）、Reddit/StockTwits社交情绪（需OAuth）——参照 TradingAgents 的 `dataflows/` 里都有现成实现，需要时再移植。
