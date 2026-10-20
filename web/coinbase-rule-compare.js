'use strict';
/* Compare fixed retrospective rules by civil year; keep prospective scoring separate. */
(() => {
 const E=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const P=v=>Number.isFinite(v)?v.toFixed(1)+'%':'—';
 const D=v=>Number.isFinite(v)?(v>=0?'+':'')+v.toFixed(1)+'pp':'—';
 const BeijingDate=v=>new Date(v).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false});
 const directionNames={'DAY-STEM-METALWATER-UP':'金水日上涨','DAY-STEM-FIRE-DOWN':'火日下跌','DAY-BRANCH-YIN-DOWN':'寅日下跌'};
 const timingNames={gann:'江恩等距日',moon_phase:'四种月相日',nakshatra_ingress:'月亮换宿日'};
 const metric=(year,id)=>year.timing.metrics.find(item=>item.id===id);
 class RuleCompare {
  constructor(host){this.host=host;this.data=null;this.forward=null;this.visible=false;this.loading=false;this.symbol='BTCUSD';
   this.host.innerHTML='<div class="section-title"><h3>规则对照面板</h3><span class="tag amber">历史探索 / 前瞻分列</span></div><p class="muted">从已封存的 Coinbase 日线重新汇总展示，不修改规则或旧预测。</p><p class="muted" role="status">打开历史回测后读取。</p><div class="cb-compare-body"></div>';
  }
  setSymbol(symbol){if(this.symbol===symbol)return;this.symbol=symbol;this.render();}
  setVisible(visible){this.visible=visible;if(visible)this.load();}
  async load(){if(this.loading)return;this.loading=true;try{
    const [back,forward]=await Promise.all([
     fetch('/api/coinbase-rule-comparison',{cache:'no-store'}),
     fetch('/api/coinbase-forward-status',{cache:'no-store'})]);
    const [data,status]=await Promise.all([back.json(),forward.json()]);
    if(!back.ok)throw Error(data.error||'历史规则对照读取失败');
    if(data.studyId!=='CB-USD-HX-001'||!data.retrospective)throw Error('历史对照版本不符');
    this.data=data;this.forward=forward.ok?status:null;this.render();
    this.host.querySelector('[role=status]').textContent=forward.ok?'历史与前瞻状态已分别读取。':'历史已读取；前瞻状态暂不可用。';
   }catch(error){this.host.querySelector('[role=status]').textContent='暂不能展示：'+error.message;}finally{this.loading=false;}}
  render(){if(!this.data)return;const symbol=this.data.symbols.find(item=>item.symbol===this.symbol);if(!symbol)return;
   const years=symbol.years;
   const directionRows=years.flatMap(year=>year.directionRules.map(rule=>`<tr><th scope="row">${E(year.year)}</th><td>${E(directionNames[rule.id]||rule.id)}</td><td>${E(rule.n)}</td><td>${E(rule.correct)} / ${E(rule.n)}</td><td>${P(rule.hitRatePct)}${rule.unmatchedN?`<small>匹配组 ${P(rule.matchedTriggerPct)}</small>`:''}</td><td>${P(rule.matchedBaselinePct)}<small>匹配 ${E(rule.matchedN)} · 无对照 ${E(rule.unmatchedN)}</small></td><td>${D(rule.matchedDeltaPp)}</td></tr>`)).join('');
   const timingRows=years.flatMap(year=>['gann','moon_phase','nakshatra_ingress'].map(id=>{const item=metric(year,id),baseline=metric(year,'all_dates');return `<tr><th scope="row">${E(year.year)}</th><td>${E(timingNames[id])}</td><td>${E(item.n)}</td><td>${P(item.exactRatePct)}</td><td>${P(item.within3RatePct)}</td><td>${P(baseline.within3RatePct)}<small>共同可评 ${E(year.timing.commonEligibleDays)} 日</small></td><td>${P(item.coveragePct)}</td></tr>`;})).join('');
   const confluenceRows=years.map(year=>{const groups=Object.fromEntries(year.confluence.map(item=>[item.id,item]));return `<tr><th scope="row">${E(year.year)}</th><td>${E(year.eligibleDays)} / ${E(year.days)}</td><td>${E(groups.direction_conflict.n)}</td><td>${E(groups.bazi_and_timing.n)}</td><td>${E(groups.unknown_time_inputs.n)}</td></tr>`;}).join('');
   const forward=this.forward,registration=forward?.registration,summary=forward?.latestEvaluation?.summary;
   let forwardHtml='<p>ETH 只作历史对照，没有 ETH 事前登记。</p>';
   if(this.symbol==='BTCUSD'){
    if(!forward)forwardHtml='<p>前瞻状态暂不可用。</p>';
    else if(!registration)forwardHtml='<p>尚无事前登记，不能把历史结果算作前瞻成绩。</p>';
    else forwardHtml=`<p>已于 <strong>${E(BeijingDate(registration.registeredAtUtc))}（北京）</strong> 登记 ${E(registration.schedule.length)} 天 · 原登记哈希 ${E(registration.contentSha256.slice(0,16))}…</p>${summary?`<p>截至最近复核：方向已评 ${E(summary.directionScoredDays)} 天；未来 ${E(summary.directionFutureDays)} 天；缺行情 ${E(summary.directionMissingDays)} 天；时间已评 ${E(summary.timeScoredDays)} 天；方向冲突 ${E(summary.directionConflictDays)} 天。</p><div class="table-wrap"><table class="data-table"><thead><tr><th>事前方向规则</th><th>已评</th><th>符合率</th><th>全日同方向基准</th></tr></thead><tbody>${summary.directionRules.map(rule=>`<tr><th scope="row">${E(directionNames[rule.id]||rule.id)}</th><td>${E(rule.n)}</td><td>${P(rule.hitRatePct)}</td><td>${P(rule.allDayBaselinePct)}</td></tr>`).join('')}</tbody></table></div><p>时间事件 ±3 日：全日基准 ${P(summary.allDateWithin3Pct)}；窗口覆盖 ${P(summary.timeEventCoveragePct)}。未到期与缺行情不计错。</p>`:'<p>已经登记，尚无可评分结果。</p>'}${forward.lastAutoError?'<p class="muted">最新自动行情刷新失败，沿用已保存的复核。</p>':''}`;
   }
   this.host.querySelector('.cb-compare-body').innerHTML=`<details class="cb-compare-fold"><summary>查看分年份规则对照</summary><p class="muted">${E(this.symbol)} · 历史截至 ${E(this.data.sourceEndDate)}。2026 为部分年份。历史数字均为事后复盘；同日多规则可重复触发，样本不能相加。</p><h4>方向规则 · 同年同节气月对照</h4><p class="muted">差值只按有匹配对照的触发日计算；若有无对照日，表内另外显示匹配组符合率。</p><div class="table-wrap"><table class="data-table cb-compare-table"><thead><tr><th>年</th><th>规则</th><th>触发日</th><th>符合 / 触发</th><th>符合率</th><th>未触发日基准</th><th>差值</th></tr></thead><tbody>${directionRows}</tbody></table></div><h4>无方向时间 · 转点与覆盖</h4><p class="muted">±3 日同时显示全日基准；时间事件没有涨跌方向。时间共同输入不完整时显示零候选和不可评估。</p><div class="table-wrap"><table class="data-table cb-compare-table"><thead><tr><th>年</th><th>事件</th><th>候选日</th><th>精确日</th><th>±3 日</th><th>全日 ±3 日</th><th>窗口覆盖</th></tr></thead><tbody>${timingRows}</tbody></table></div><h4>冲突与共振 · 只计同日出现</h4><div class="table-wrap"><table class="data-table cb-compare-table"><thead><tr><th>年</th><th>可评 / 全部</th><th>八字方向冲突</th><th>八字＋时间同日</th><th>时间输入不完整</th></tr></thead><tbody>${confluenceRows}</tbody></table></div><h4>事前登记 · 单独核验</h4><div class="cb-compare-forward">${forwardHtml}</div><p class="muted">ETH 只作跨资产历史对照；BTC 的事前登记不外推到 ETH。规则表现仅用于研究比较，不是交易指令。</p><p class="small"><a href="/api/coinbase-rule-comparison" target="_blank" rel="noopener">查看只读对照数据 JSON ↗</a></p></details>`;
  }
 }
 window.createCoinbaseRuleCompare=host=>new RuleCompare(host);
})();
