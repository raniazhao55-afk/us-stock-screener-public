# Changelog

## v0.1.0 (2026-08-23)

首次构建，给 `us-stock-screener` 用。

- Layer 1（行情/基本面）：`lib/market_data.py` — OHLCV、技术指标(50/200SMA/10EMA/MACD系列/RSI/布林/ATR/VWMA/MFI)、基本面概览、三大报表、内部人交易、机构持仓、分析师评级、当前报价。绝大部分移植改造自 [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents) 的 `dataflows/y_finance.py`（Apache-2.0），`get_institutional_holders`/`get_analyst_recommendations`/`get_quote` 是新写的（原项目没有）。
- Layer 2（公告/新闻）：`lib/news.py` 移植自 `dataflows/yfinance_news.py`；`lib/sec_edgar.py` 是新写的，包 SEC EDGAR 官方免费接口（submissions API + 全文检索），作为巨潮公告的美股对应物。
- Layer 3（补充扫描）：`lib/screener.py` 新写，包 `yfinance.screen()` 的预置查询。
- 用 AAPL(大盘)/DUOL(中盘)/RDDT(近期IPO小盘) 三只真实标的全量验证通过，包括SEC全文检索的URL拼接bug（`_id`字段格式踩坑）当场发现并修复。
- v0.1 范围内不做：FRED宏观、Alpha Vantage、Reddit/StockTwits情绪——参照 TradingAgents 现成实现，需要时再移植。

## v0.1.1 (2026-08-23)

新增 Layer 4（可选）：`lib/vibe_trading_optional.py`，从 [HKUDS/Vibe-Trading](https://github.com/HKUDS/Vibe-Trading)（MIT，见 `LICENSE_VIBETRADING`）借三个净增量能力——期权链(`get_options_chain`)、Polymarket预测市场(`prediction_market_search`/`prediction_market_event`)、学术金融论文搜索+factor brief提炼(`research_papers_search`/`research_papers_read`)。只import它的`src.tools`直接调用这几个纯函数，不跑它自己的agent/swarm/LLM编排层。

评估过程中用真实调用测出该项目另外几个和Layer1-3重叠的工具有bug（`technical_indicators`不带`.US`后缀会误路由到tushare/A股路径；`screen_market(market="us")`会超时连接到不该碰的push2.eastmoney.com；`get_institutional_holdings(mode="ticker_holders")`的CUSIP反查依赖SEC"fails-to-deliver"文件对大多数股票查不到），所以没有采用这些重叠工具，只挑了三个净增量。

安装 `vibe-trading-ai` 把本venv的pandas从3.0.5降到2.3.3（它pin了`<3.0.0`）——已重新跑过 Layer 1-3 的验证确认降级后仍正常。

## v0.2.0 (2026-08-23)

新增两个模块，都是同一天真实Phase A跑完后用户反馈"技术面只是描述图形，没有策略"+"行业分析太弱"补上的：

- `lib/technical_signals.py`：用户提供的具体RSI+MACD+MA共振策略（附6张图），六个信号——上车/下车(图1/2)、启动/爆发(图3/4)、顶部反转警告/顶背离(图5/6，原文字未提，看图后新增)。**v1→v2**：v1把"共振"实现成"3天窗口内三个交叉事件都要发生"，实测MSFT真实的7/29-30暴涨0命中(RSI/MACD/MA分别在不同天穿越)；v2改成"状态是否成立+是否最近才形成"，重测后MSFT/NVDA测出顶部反转、MU测出上车信号，和图形描述吻合。图5/图6需要额外写局部波峰波谷检测，是和前四个交叉判断完全不同的算法。
- `lib/industry.py`：补上一直缺的同业对比（a-stock-data有、us-stock-data原来没有）。用`yfinance.Industry(industryKey)`免费拿同业权重/评级表+**全行业YTD涨幅排名**(不只是watchlist内部对比)+行业规模概览。首次验证查到SNDK在其行业(75家)排YTD第1(+572%)、MU排第2(+239%)——这条信息之前完全没有覆盖到。已知局限：部分ADR无`industryKey`会返回`ok:false`；有的行业分类过窄导致"同业"没有实际可比性(如AAPL的"Consumer Electronics"类目里自己占99.9%权重)。

## v0.3.0 (2026-08-23)

用户继续指出两处不够的地方，同一天补上：

- `lib/chart_patterns.py`：`technical_signals.py`之外的五个通用技术形态——金叉/死叉(50/200日)、MACD独立金叉死叉、多头/空头排列(独立读数，不挂靠MACD/RSI)、布林带挤压+突破、放量突破确认。7只自选股验证通过，全部无报错可序列化。发现一个有意思的例子：MSFT财报暴涨后价格已经很高，但50日均线当时仍在200日均线下方(死叉状态)——短期价格结构和长周期均线结构可能不同步。
- 用`industry.py`的`top_peers_by_weight`首次真实构建了`us-stock-screener/market_calibration.json`：对7只自选股所属的6个行业，各自动拉最多15+只同业(按市值权重)的历史价格，跨股票+跨时间池化，按trailing涨跌幅分位数(而不是固定区间——不同回看窗口的自然波动幅度差一个数量级)分5桶，算每桶未来5日跌/涨5%+的经验概率。复现出和a-stock-screener一致的**U型规律**：涨跌两个极端分位桶的未来下跌概率都比正常区间高，且"暴跌后反弹"概率往往比"暴涨后续涨"概率更高(均值回归)。SNDK(10日+31.7%落入top10分位，跌5%+概率37.9% vs正常21.8%)和MSFT(20日+26.8%落入top10分位，跌5%+概率21.1% vs正常14.7%)是本轮统计信号最极端的两个。

## v0.3.1 (2026-08-23)

修了一个系统性漏洞：`sec_edgar.get_recent_8k_filings()`之前只给items编号，`1.01`(签重大协议)/`8.01`(其他重大事项)这类编号本身不透露具体内容，却被当成"催化事件已确认"直接跳过没读。起因是回头去读NVDA一条`1.01,2.03,7.01`的8-K，发现是NVIDIA为OpenAI在俄亥俄数据中心项目的租约做了**最高1050亿美元的剩余价值担保**(OpenAI违约NVIDIA代付)——这种规模的或有负债信息，光看编号完全看不出来。新增`ITEMS_REQUIRING_FULL_READ`集合(1.01/1.02/2.01/2.03/2.05/2.06/3.02/3.03/4.01/4.02/5.01/8.01)和`get_filing_text(url)`函数，`get_recent_8k_filings()`现在会给需要读原文的行打`[READ REQUIRED]`标记。已验证NVDA的两条历史记录都被正确标记。
