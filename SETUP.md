# 在你自己的电脑上使用

本仓库是**空白模板**：方法论(PHASE_A/B/C_TASK.md)、数据层代码(skills/us-stock-data)、风控规则、行业校准文件都在，
但**没有任何人的持仓、交易记录或论点输出**——这些会在你自己运行后生成。

## 1. 安装数据层 skill
```bash
mkdir -p ~/.claude/skills
cp -r skills/us-stock-data ~/.claude/skills/us-stock-data
cd ~/.claude/skills/us-stock-data
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```
任务文件里的命令默认 skill 装在 `~/.claude/skills/us-stock-data`，路径不同请自行替换。

## 2. SEC EDGAR 要求真实联系方式
SEC 会拒绝匿名请求(403)。运行前设置(换成你自己的姓名和邮箱，**不要写进任何会提交的文件**)：
```bash
export SEC_EDGAR_CONTACT="Your Name your.email@example.com"
```

## 3. 改成你自己的账户
- `risk_rules.json` 的 `account.starting_capital_usd`：改成你的美元本金(示例是10000)；`account_type`/仓位上限等按你的情况调整。
- `watchlist.json`：`symbols` 换成你自己的自选股；`held_positions` 只在你**实际买卖后手动**更新——系统不接券商，
  这是它唯一信任的持仓来源。

## 4. 运行
在 Claude Code 里让它按 `PHASE_A_TASK.md` 执行 Phase A(收盘后)，再按 `PHASE_B_TASK.md` 执行 Phase B(次日开盘后)。
系统只生成投研论点和操作清单，**不下单**。

## 已知需要注意
- `market_calibration.json`/`signal_calibration.json` 是基于2026年9月前后行情构建的快照(行业同业池的历史涨跌分布)，
  30天以上就应该按 PHASE_A_TASK.md 末尾"如何构建 market_calibration.json"重建；构建脚本是一次性脚本、不在仓库里常驻。
- 数据源是免费的 yfinance + SEC EDGAR，yfinance 偶有整日缺口/口径变动，详见 skills/us-stock-data/SKILL.md 的踩坑记录。
- 本项目不构成投资建议，作者的历史回测/论点均不代表未来收益。
