# 时序研究室

一个本地运行的 BTC/ETH 时间规则研究网站。包含八字历法、江恩时间候选、月相和印度占星时间参考、TradingView 免费嵌入图，以及独立的 Bitstamp BTC/USD 2013–2026 长期复盘和 Coinbase 固定回测报告。

交节换月附近的实际 BTC/USD 涨跌另见[交节事件研究](research/jie-event-study-2026-10-23.md)和[2022–2026 跨年份规律复核](research/jie-pattern-study-2026-10-23.md)：包含逐事件、逐年份、同月普通日期及节气前后每日对照。两份都是探索性复盘，不更改封存的前瞻计划。

日干、寅日、庚戌日及不同定义的金水月另见[五行与月令跨年份复核](research/element-pattern-study-2026-10-24.md)：按完整北京时间日线与节气月分别统计，并保留逐月上涨、下跌日期。

2013 年起的 BTC 长期结果见[Bitstamp 独立回测](research/bitstamp-long-study-2013-2026.md)。原始小时蜡烛仅保留在被 Git 忽略的本机 `local_data/`；可用 `python build_bitstamp_long_history.py` 重新采集，再运行 `python build_bitstamp_long_study.py` 生成报告。Bitstamp 与 Coinbase 是不同交易所，页面将它们分开展示。

> 本项目是研究工具。页面中的候选日期、方向和历史符合率尚不能证明预测能力，也不构成交易建议。请保留失败样本、缺失数据和事前登记记录。

## 运行

需要 Python 3.12。仓库包含原交付的 Windows x64 依赖副本；其他平台应通过 `requirements.txt` 安装兼容版本。克隆仓库后运行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app_server.py --port 8766
```

打开 <http://127.0.0.1:8766/>。在 macOS/Linux 上使用 `.venv/bin/python`。服务只绑定本机 `127.0.0.1`。

TradingView 图表是官方免费嵌入组件；它不向本站提供 OHLC。历史回测和预测核验读取单独保存的 Coinbase 数据。仓库附带固定研究结果、协议及[公开前瞻登记](PUBLIC_REGISTRATION.md)，**不附带**本机行情数据库。新克隆的实例可查看封存计划，并用已有的 Coinbase 缓存做只读复核；公开登记不会自动变成本机复核记录。服务只使用指定的本机数据目录，不自动读取相邻旧版目录。前瞻页面会显示正在使用的行情库、登记文件与公开计划路径。

在“前瞻验证”页点击“补齐已收盘行情”，会明确向 Coinbase Exchange 获取本期计划及转点邻域内已经收盘的 BTC/USD 小时线，合成北京时间日线写入本机缓存；随后页面进行公开计划的只读复核。逐日表显示事前方向、实际开高低收、缺口原因、日线内容哈希和原始请求 ID。此操作不会创建本地登记或评价记录。未来日期保持未到期。

逐日表可按日期范围、流月、方向规则、时间事件、实际涨跌和复核状态组合筛选，并随时清除。筛选只影响下方可见日期及“显示 N / 30 日”，上方整期统计与已封存计划始终保持原口径；筛选出的子集不是新的预测准确率。

本地服务运行期间会在北京时间每日 00:20 后检查公开前瞻计划所需的已收盘日线，补缺后生成只读复核，并将新的行情输入版本追加到 `local_data/public_forward_audit/`。页面会显示最近尝试、失败原因和最近 7 份记录的哈希。审计记录与本地正式评价链分开，服务关闭时不会后台运行；本机哈希链也不是第三方时间戳公证。

## 仓库内容

- `app_server.py`、顶层 Python 模块：本地服务、历法、规则与复核逻辑。
- `web/`：前端与 TradingView 嵌入图。
- `coinbase_study_v21/`、`month_study_v17/`、`exploration_v16/`、`confluence_v20/`：冻结的研究协议与结果。历史探索和前瞻登记应分开解读。
- `test_*.py`、`test_*.cjs`：回归测试。
- `public_data/`、`examples/`：封存登记及明确标为合成的评分样本。
- `AGPL-3.0.txt`、`LICENSE_NOTICES.md`：项目和依赖许可。

运行 `python -m unittest discover -p "test_*.py"` 可以执行 Python 测试。部分测试涉及本机历史数据或网络，缺少数据时应按测试说明跳过；请勿把缺数据记成预测失败。

## 数据与复现边界

固定报告包含当时的价格快照和来源哈希；运行时的 `local_data/`、原始请求、缓存和用户资料不在仓库内。若要重新计算研究结果，请先核对协议、交易所、报价币种、时区、缺口处理和样本截止日期。公开版从空本地数据库启动，不会继承原作者的前瞻登记链。仓库里的公开登记可核验封存哈希和计划内容，但缺少原始行情快照及登记时一份完整的源文件字节快照，不能单凭仓库重演登记生成全过程。见[复现说明](PUBLIC_REGISTRATION.md)。

## 参与修改

欢迎提交 Issue 或 Pull Request。请先读 [CONTRIBUTING.md](CONTRIBUTING.md)，特别是数据来源、前瞻登记和回测口径。代码按 AGPL-3.0 授权；第三方组件及其数据保留各自条款。
