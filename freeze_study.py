"""Validate and freeze this retrospective amendment before first scoring."""
from pathlib import Path
import hashlib
import io
import json
import platform
import unittest
from datetime import datetime,timezone
import masked_study

HERE=Path(__file__).resolve().parent


def write(name,obj):
    (HERE/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    if (HERE/'study_registration.json').exists():raise RuntimeError('registration already frozen; do not overwrite')
    log=io.StringIO()
    suite=unittest.defaultTestLoader.discover(str(HERE),pattern='test_research_v06.py')
    result=unittest.TextTestRunner(stream=log,verbosity=2).run(suite)
    write('unit_tests.json',{'runAtUtc':datetime.now(timezone.utc).isoformat(),'testsRun':result.testsRun,
                           'failures':len(result.failures),'errors':len(result.errors),'skipped':len(result.skipped),'passed':result.wasSuccessful()})
    (HERE/'unit_tests.log').write_text(log.getvalue(),encoding='utf-8')
    if not result.wasSuccessful():raise RuntimeError('tests failed')
    protocol=json.loads((HERE/'study_protocol_proposal.json').read_text(encoding='utf-8'))
    protocol['status']='frozen_before_this_revision_first_scoring_after_prior_data_and_results_seen'
    protocol['periods']=protocol['immutableChoices']['periods']
    protocol['limitations']=[
        '研究对象为来源可核验且24小时完整日期的组均值差；不恢复被剔除日期的真实收益。',
        '30日区块同时抽取分组、缺失标记与收益；不会证明缺失随机、平稳或因果。',
        '已看过旧描述统计后新增此缺失数据方案，属于后验探索修订，不是事前预注册。',
        '2025–2026已受案例阅读影响；2026仅到9月26日，不是干净样本外。',
        '没有纳入费用、滑点、下单时点、杠杆或资产头寸；不是可执行交易策略回测。',
        '原无缺口协议2020–2024依旧未通过；新结果不能覆写该历史记录。',
        '单一日干分组不代表完整八字、占星或江恩体系；样本内差异不验证这些理论。'
    ]
    write('study_protocol_frozen.json',protocol)
    doc=json.loads((HERE/'daily_input.json').read_text(encoding='utf-8'))
    grids=[masked_study.make_grid(doc,p['start'],p['end']) for p in protocol['periods']]
    write('study_grid.json',grids)
    recovery=json.loads((HERE/'market_recovery.json').read_text(encoding='utf-8'))
    audit=json.loads((HERE/'market_gap_audit.json').read_text(encoding='utf-8'))
    if audit['sourceRecoveryReportSha256']!=sha(HERE/'market_recovery.json'):raise ValueError('gap audit must refer to current recovery')
    if audit['frozenArchiveCountVerified']!=106 or audit['remainingIncompleteDays']!=recovery['remainingIncompleteDays']:raise ValueError('audit counts disagree')
    if sum(not r['mask'] for g in grids for r in g)!=recovery['remainingIncompleteDays']:raise ValueError('mask counts disagree')
    inputs=['daily_input.json','study_protocol_proposal.json','study_protocol_frozen.json','masked_study.py','legacy_scorer.py',
            'recover_market.py','legacy_market.py','calendar_adapter.py','calendar_validation.py','calendar_reference.json','bazi_engine.py',
            'market_recovery.json','market_gap_audit.json','calendar_validation_evidence.json','unit_tests.json','test_research_v06.py','study_grid.json','freeze_study.py']
    write('study_registration.json',{'experimentId':'EXP-BZ-DAY-001-20260927-MASK-02',
          'registeredAtUtc':datetime.now(timezone.utc).isoformat(),'registrationType':'post_inspection_retrospective_amendment_pre_this_run',
          'notProspectivePreregistration':True,'knownPriorResults':protocol['priorKnowledge'],
          'inputHashes':{name:sha(HERE/name) for name in inputs},'python':platform.python_version(),
          'command':'python -B -X utf8 masked_study.py','forwardMonitoringStarted':False,'marketExecutionEnabled':False})
    print(json.dumps({'tests':result.testsRun,'registered':True,'maskedDays':sum(not r['mask'] for g in grids for r in g)},ensure_ascii=False))


if __name__=='__main__':main()
