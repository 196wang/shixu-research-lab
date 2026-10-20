# 许可与第三方依赖

本仓库中自有程序和网页源码按 GNU Affero General Public License v3.0 提供，完整条款见 `AGPL-3.0.txt`。研究文章、截图、外部数据及其他第三方材料不因本项目许可而重新授权。

`requirements.txt` 固定了运行依赖。仓库也保留原交付中的 `vendor/` 依赖副本及对应许可；其中的 Windows 扩展文件只适用于兼容的 Windows/Python 环境：

- `lunar-python==1.4.8`：MIT，6tail。
- `astronomy-engine==2.1.19`：MIT，Don Cross。
- `pysweph==2.10.3.6`：Swiss Ephemeris 的社区 Python 包装；Swiss Ephemeris 由 Astrodienst AG 提供，遵循其 AGPL/商业许可选项。上游发布源码保存在 `third_party_sources/`。请在重新分发或部署时核对上游许可。

本项目使用 Moshier 后端，不附带外部星历文件。TradingView 图表通过官方嵌入组件显示，Coinbase 行情与历法参考各自保留来源和适用条款。
