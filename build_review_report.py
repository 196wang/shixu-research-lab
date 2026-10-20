"""Render the frozen research outputs into a readable report; no scoring here."""
from pathlib import Path
import json

HERE=Path(__file__).resolve().parent


def build(target):
    summary=json.loads((target/'research_summary.json').read_text(encoding='utf-8'))
    periods=summary['study']['periods']
    lines=['# v0.6 研究结果：事件依据与金水日历史检验','',
        '**结论：两段区间都包含零，目前不足以确认“金水日有稳定收益优势”。**','',
        '这里只检验一个窄定义：北京时间00:00开收日，日干庚辛壬癸为G1，其余为G0，比较BTCUSDT现货简单日收益均值。它不等于完整八字理论，更不是可交易策略的收益回测。','',
        '## 1. 实际计算结果','',
        '| 固定时期 | 完整/预计日 | G1平均日收益 | G0平均日收益 | G1−G0（bp） | 95%区间（bp） |',
        '|---|---:|---:|---:|---:|---|']
    for p in periods:
        ci=p['bootstrap']['ci95Bp']
        lines.append(f"| {p['start']}～{p['end']} | {p['observedDays']}/{p['expectedDays']} | {p['groups']['1']['mean']*100:.4f}% | {p['groups']['0']['mean']*100:.4f}% | {p['deltaBp']:.4f} | {ci[0]:.4f}～{ci[1]:.4f} |")
    lines += ['', '1 bp = 0.01个百分点。两段均实际完成10,000次有效区块抽样。区间包含零，不宜据此建立自动看涨规则；这也不证明绝对没有任何效应。','',
        '## 2. 为什么另立 v0.2 探索协议','',
        '旧v0.1要求时期内每一天都有24根完整小时线；2020–2024仍不满足。此次完整日统计明确以“可观察的完整日期”为研究对象，保留缺失日的日历位置、组别和掩码，抽取30个日历日的配对区块，然后仅用有效日计算均值。缺失日不是零收益，也没有被从时间轴压掉。','',
        '这是已检查数据且已看过旧描述统计后的方案修订；执行前保存了本次参数、输入、掩码、测试与代码哈希，不能称为事前预注册。2025–2026已受案例阅读影响，不是干净样本外。30日区块是一种固定近似，不保证消除非平稳和缺失选择偏差。','',
        '### 逐年结果（全部保留）','',
        '| 年份 | 完整/预计日 | G1−G0（bp） | G1缺日/预计 | G0缺日/预计 |',
        '|---|---:|---:|---:|---:|']
    for p in periods:
        for y in p['yearly']:
            g1,g0=y['missingByGroup']['1'],y['missingByGroup']['0']
            lines.append(f"| {y['year']}{'（截至9月26日）' if y['partialYear'] else ''} | {y['observedDays']}/{y['expectedDays']} | {y['deltaBp']:.4f} | {g1['missing']}/{g1['expected']} | {g0['missing']}/{g0['expected']} |")
    lines += ['', '逐一留年结果、极端收益日清单及连续缺失段见 study_results.json；没有因正负或极端收益而删日期或改窗口。','',
        '## 3. 行情恢复与冲突','',
        '- 补回2026-09-26北京时间日缺少的16个小时；同交易所官方K线API为来源。',
        '- 2461天中2443天完整，仍有18个历史日期、39个不合格小时位置。',
        '- 106份旧ZIP重新通过SHA256与CRC核验。近期日档CHECKSUM在本轮仍404，未伪称已下载。',
        '- 档案与API有3条实质行差异，包括旧完整小时与新异常小时之间的价格/时间分歧；保留原件，不按收益选择版本。',
        '- 部分日期有官方维护依据，未证实原因的仍标未知；停市证据不等于允许补价。','',
        '细目及官方来源见 market_gap_audit.md/json，HTTP原始响应见 recovery_raw。','',
        '## 4. 诞生事件不等于已经核定的本命盘','',
        '| 候选事件 | UTC | 本研究UTC+8四柱 |', '|---|---|---|']
    for e in summary['events']:
        pillars='、'.join(e['pillars'][k] for k in ('year','month','day','hour'))
        lines.append(f"| {e['label']} | {e['eventAtUtc']} | {pillars} |")
    lines += ['',
        '**三条事件按当前口径都不能直接复现既有同资产四柱。** 这可能涉及选择不同事件、时区及日界约定；不能凭日期相近或行情吻合替原作者补造依据。',
        'ETH Frontier换算北京时间为23:26:13，当前00:00换日日柱为丁未，时柱壬子；用户给定A版日柱为戊申。晚子时口径是需核查的具体差异，未据此断言A版错误。',
        'BTC创世头记录时间不证明实际挖出或广播的准确时刻；白皮书邮件显示EDT不证明作者所在地。ETH Frontier秒值来自官方回顾时间线，同期公告只交叉确认当天。',
        'ETH创世头Timestamp=0是协议字段，不采用Unix字面1970作为出生时间。ETH白皮书只保留2013-11-27日期线索，不擅自补午夜。',
        '所有候选出生地点留空，不启用宫位；BTC原四柱、ETH A/B两版保持独立，未自动选出优胜盘。','',
        '详见 birth_research.md、birth_candidates.json、event_comparisons.json。','',
        '## 5. 下一步研究顺序','',
        '1. 对照原图/原文查原四柱的日期、时区和晚子时设置；先解释版本差异，再选择需要跟踪的事件假设。',
        '2. 把八字规则中的“合冲”“开库”“金水”拆成明确输入、触发、时间窗口和失败标准；一次登记一条，保留失败。',
        '3. 江恩锚点/比例、占星相位与运限分别建立规则，避免多个体系任意互相解释结果。',
        '4. 只有未来另行确定前瞻起点、样本长度和停止规则，才能积累新的独立验证；本次未建立定时任务或交易执行。','']
    (target/'研究结果.md').write_text('\n'.join(lines),encoding='utf-8')
    readme='''# 时序研究室 v0.6

本版新增“验证进展”页：候选事件时间与原四柱对照、行情缺口恢复、金水日历史探索结果。八字和占星计算基线沿用v0.5。

## 打开

双击本目录 `启动研究室.cmd`，访问 http://127.0.0.1:8766/ 。默认只允许本机使用，不需要API账号和付费服务。Windows x64、Python 3.12依赖已随vendor附带。

若之前版本正在运行，先在它的终端按Ctrl+C，再启动本版；同端口不能同时运行两个版本。

启动脚本若发现相邻的 `时序研究室_v0.5/local_data/research.sqlite3`，会继续使用那里保存的笔记；否则在本版 `local_data` 创建数据库。当前机器运行的v0.6已复用v0.5记录目录。旧判断和复盘不迁移、不删除、不重写。备份时停服务再复制实际使用的数据库。

## 本次结果

查看 [研究结果](研究结果.md)。两个固定时期的95%区间均包含零，尚不能确认稳定优势。v0.2是看过旧结果后另立的探索协议，原v0.1失败门槛保留；没有把其包装成策略盈利或完整理论验证。

事件记录不等于已经选定资产本命盘：保留BTC原四柱和ETH A/B两个版本；本版未开启宫位或资产本命运限。

## 文件

- `birth_candidates.json` / `birth_research.md`：一手出处、时区、时间精度、拒用时间。
- `event_comparisons.json`：三条事件的UTC+8排盘和印度地心星位；原盘不覆盖。
- `market_gap_audit.*` / `market_recovery.json` / `recovery_raw`：缺口、来源差异和HTTP响应。
- `study_protocol_frozen.json` / `study_registration.json`：本次执行前冻结的后验修订及哈希。
- `daily_input.json` / `study_grid.json` / `study_results.json`：输入、完整日历掩码、结果与逐年敏感性。
- `unit_tests.json`：13项新增测试通过，包括独立日历、掩码重采样朴素对照和来源冲突。
- `v0.5_基础计算说明.md`：基础排盘口径及尚未完成的JHora/独立精度核对。

## 复算

在本目录运行 `python -B -X utf8 masked_study.py` 可离线验证登记哈希并复算历史结果，不访问网络。冻结输入不得原地改动；要修订请新建版本与执行记录。`freeze_study.py`拒绝覆盖已有登记。

`recover_market.py` 与 `audit_market_gaps.py` 复查旧档时还需要原v0.4目录，本包不重复附带全部旧行情ZIP。网络恢复会另取公开响应，不能把重新采集等同复现旧快照。公开资料的时间与价格版本差异都在审计文件中保留。

## 许可与使用范围

本机计算无按次API费用。AGPL程序源码及第三方许可见 LICENSE_NOTICES.md、source_bundle.zip、third_party_sources；外部文章与案例保留原出处。未开启朋友访问、付费API、云部署、定时任务或交易执行。

当前结果用于研究；既不证明八字/占星能预测价格，也没有建立交易规则胜率。下一步优先核对原盘的时区/日界和完善下一条规则的可检验定义。
'''
    (target/'README.md').write_text(readme,encoding='utf-8')


if __name__=='__main__':
    build(HERE.parents[1]/'outputs/时序研究室_v0.6')
