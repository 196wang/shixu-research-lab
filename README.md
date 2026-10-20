# 时序研究室

一个本地运行的 BTC/ETH 时间规则研究网站。包含八字历法、江恩时间候选、月相和印度占星时间参考、TradingView 免费嵌入图，以及 Coinbase 历史行情的固定回测报告。

> 本项目是研究工具。页面中的候选日期、方向和历史符合率尚不能证明预测能力，也不构成交易建议。请保留失败样本、缺失数据和事前登记记录。

## 运行

需要 Python 3.12。仓库包含原交付的 Windows x64 依赖副本；其他平台应通过 `requirements.txt` 安装兼容版本。克隆仓库后运行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app_server.py --port 8766
```

打开 <http://127.0.0.1:8766/>。在 macOS/Linux 上使用 `.venv/bin/python`。服务只绑定本机 `127.0.0.1`。

TradingView 图表是官方免费嵌入组件；它不向本站提供 OHLC。历史回测和预测核验读取单独保存的 Coinbase 数据。仓库附带固定研究结果和协议，**不附带**本机行情数据库。新克隆的实例可查看固定报告；需要本地实时核验时，在网站中更新 Coinbase 数据。

## 仓库内容

- `app_server.py`、顶层 Python 模块：本地服务、历法、规则与复核逻辑。
- `web/`：前端与 TradingView 嵌入图。
- `coinbase_study_v21/`、`month_study_v17/`、`exploration_v16/`、`confluence_v20/`：冻结的研究协议与结果。历史探索和前瞻登记应分开解读。
- `test_*.py`、`test_*.cjs`：回归测试。
- `AGPL-3.0.txt`、`LICENSE_NOTICES.md`：项目和依赖许可。

运行 `python -m unittest discover -p "test_*.py"` 可以执行 Python 测试。部分测试涉及本机历史数据或网络，缺少数据时应按测试说明跳过；请勿把缺数据记成预测失败。

## 数据与复现边界

固定报告包含当时的价格快照和来源哈希；运行时的 `local_data/`、原始请求、缓存和用户资料不在仓库内。若要重新计算研究结果，请先核对协议、交易所、报价币种、时区、缺口处理和样本截止日期。公开版从空本地数据库启动，不会继承原作者的前瞻登记链。

## 参与修改

欢迎提交 Issue 或 Pull Request。请先读 [CONTRIBUTING.md](CONTRIBUTING.md)，特别是数据来源、前瞻登记和回测口径。代码按 AGPL-3.0 授权；第三方组件及其数据保留各自条款。
