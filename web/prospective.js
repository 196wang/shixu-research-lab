'use strict';
/* Prospective protocol controls. Reading this page never registers or scores a study. */
(() => {
 const RULES=[['BZ-E01','日干属金水'],['BZ-E02','日干属火'],['BZ-E03','原局与流日六冲'],['BZ-E04','原局与流日六合']];
 const E=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const n=value=>typeof value==='number'&&Number.isInteger(value)&&value>=0?value:0;
 const pct=value=>typeof value==='number'&&Number.isFinite(value)?(value>0?'+':'')+value.toFixed(2)+'%':'—';
 const pp=value=>typeof value==='number'&&Number.isFinite(value)?(value>0?'+':'')+value.toFixed(2)+' 个百分点':'—';
 const time=value=>{const d=new Date(value);return value&&Number.isFinite(d.getTime())?d.toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'尚无';};
 class ProspectiveStudy {
  constructor(){this.host=document.getElementById('prospective-study');this.data=null;this.visible=false;this.loading=false;this.busy=false;this.ticket=0;this.page=0;this.pageSize=15;this.rule='BZ-E01';this.horizon=1;this.group='triggered';this.status='all';this.createUI();this.bind();}
  el(id){return document.getElementById(id);}
  createUI(){
   this.host.innerHTML=`<div class="section-title"><h2>前瞻验证 · 首期 BTC</h2><span id="prospective-badge" class="tag gray">尚未读取</span></div><p class="muted prospective-fixed">固定使用 BTC-BZ-V01 ／ BTCUSDT。顶部选择 ETH 或其他分析时刻，不会改变本期协议。</p><div id="prospective-protocol" class="prospective-protocol"><p class="muted">打开此页后读取协议与登记状态。</p></div><div class="prospective-actions"><button type="button" id="prospective-register" class="primary" disabled>登记本期协议</button><button type="button" id="prospective-evaluate" class="secondary" disabled>复核本地行情</button><button type="button" id="prospective-refresh" class="quiet">刷新状态</button><button type="button" id="prospective-export" class="quiet" disabled>导出本期 JSON ↓</button></div><p id="prospective-notice" class="prospective-notice" role="status" aria-live="polite"></p><p class="muted prospective-manual">先在“K 线与时间 → 币安更新行情”更新 BTC 行情，再点击此处复核。此页只使用本地缓存；没有自动更新行情、评分或定时任务。</p><div id="prospective-candidates" class="prospective-candidates"></div><div id="prospective-progress" class="prospective-progress"></div><div id="prospective-evaluation-meta" class="muted prospective-evaluation-meta"></div><section class="prospective-results" aria-label="按验证窗口查看结果"><div class="prospective-result-heading"><h3>候选规则与基准</h3><label>验证窗口<select id="prospective-horizon"><option value="1">1 日 · 主验证</option><option value="3">3 日 · 探索</option><option value="7">7 日 · 探索</option><option value="30">30 日 · 探索</option></select></label></div><p id="prospective-outcome-note" class="muted"></p><div id="prospective-summary"></div></section><section class="prospective-dates" aria-label="逐日验证清单"><h3>逐日清单</h3><div class="prospective-date-controls"><label>候选规则<select id="prospective-rule">${RULES.map(([id,title])=>`<option value="${id}">${title}</option>`).join('')}</select></label><label>日期分组<select id="prospective-group"><option value="triggered">触发日</option><option value="baseline">未触发基准日</option><option value="all">全部日期</option></select></label><label>进度<select id="prospective-status-filter"><option value="all">全部进度</option><option value="future">未来窗口</option><option value="pending">待完整行情</option><option value="scored">已评分</option></select></label></div><div id="prospective-date-table"></div><div class="prospective-pagination"><button type="button" id="prospective-prev" class="quiet" disabled>← 上一页</button><span id="prospective-page-label" class="muted"></span><button type="button" id="prospective-next" class="quiet" disabled>下一页 →</button></div></section><details class="prospective-audit"><summary>协议版本、来源与完整性记录</summary><div id="prospective-audit-body"></div></details>`;
  }
  bind(){
   this.el('prospective-refresh').addEventListener('click',()=>this.load());
   this.el('prospective-register').addEventListener('click',()=>this.mutate('register'));
   this.el('prospective-evaluate').addEventListener('click',()=>this.mutate('evaluate'));
   this.el('prospective-export').addEventListener('click',()=>this.export());
   this.el('prospective-horizon').addEventListener('change',e=>{const h=Number(e.target.value);if(![1,3,7,30].includes(h))return;this.horizon=h;this.page=0;this.renderResults();});
   this.el('prospective-rule').addEventListener('change',e=>{if(!RULES.some(([id])=>id===e.target.value))return;this.rule=e.target.value;this.page=0;this.renderDates();});
   this.el('prospective-group').addEventListener('change',e=>{if(!['all','triggered','baseline'].includes(e.target.value))return;this.group=e.target.value;this.page=0;this.renderDates();});
   this.el('prospective-status-filter').addEventListener('change',e=>{if(!['all','future','pending','scored'].includes(e.target.value))return;this.status=e.target.value;this.page=0;this.renderDates();});
   this.el('prospective-prev').addEventListener('click',()=>{if(this.page>0){this.page--;this.renderDates();}});
   this.el('prospective-next').addEventListener('click',()=>{if((this.page+1)*this.pageSize<this.filteredDates().length){this.page++;this.renderDates();}});
  }
  setVisible(visible){this.visible=visible;if(visible&&!this.busy&&!this.loading)this.load();}
  announce(message,error=false){this.el('prospective-notice').textContent=message;this.el('prospective-notice').classList.toggle('error',error);}
  async request(path,body,signal){const r=await fetch(path,body===undefined?{signal}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal});const data=await r.json();if(!r.ok)throw new Error(data.error||'前瞻验证请求未完成');return data;}
  async load(afterMutation=false){
   if(this.busy&&!afterMutation)return false;const ticket=++this.ticket;this.controller?.abort();const controller=new AbortController();this.controller=controller;this.loading=true;this.updateButtons();this.announce('正在读取登记状态与本地验证结果…');
   try{const data=await this.request('/api/prospective-status',undefined,controller.signal);if(ticket!==this.ticket)return false;this.data=data;this.render();this.announce('已读取本期状态；未自动登记、获取行情或评分。');return true;}
   catch(error){if(error.name==='AbortError'||ticket!==this.ticket)return false;this.announce('状态读取失败：'+error.message+(this.data?'；保留上次结果。':''),true);return false;}
   finally{if(ticket===this.ticket){this.loading=false;this.updateButtons();}}
  }
  async mutate(action){
   if(this.busy||this.loading||!this.data||!['register','evaluate'].includes(action))return;
   if(action==='register'&&!this.canRegister()||action==='evaluate'&&!this.canEvaluate())return;
   ++this.ticket;this.controller?.abort();this.busy=true;this.updateButtons();this.announce(action==='register'?'正在登记本期协议…':'正在按本期协议复核本地行情…');
   let accepted=false;
   try{await this.request('/api/prospective-'+action,{});accepted=true;const refreshed=await this.load(true);this.announce((action==='register'?'本期协议已登记。':'本期本地行情已复核。')+(refreshed?' 当前结果已刷新；没有改动人工研究笔记。':' 状态暂未刷新，请点击“刷新状态”确认。'),!refreshed);}
   catch(error){this.announce((accepted?'操作已完成，但状态读取失败：':action==='register'?'登记未完成：':'复核未完成：')+error.message,true);}
   finally{this.busy=false;this.updateButtons();}
  }

  isRegistered(){return Boolean(this.data?.registration);}
  canRegister(){return Boolean(this.data?.canRegister&&this.data?.status!=='integrity_error');}
  canEvaluate(){return Boolean(this.data?.canEvaluate&&this.isRegistered()&&this.data?.status!=='integrity_error');}
  updateButtons(){
   this.el('prospective-register').disabled=this.busy||this.loading||!this.canRegister();
   this.el('prospective-register').textContent=this.isRegistered()?'本期已登记':'登记本期协议';
   this.el('prospective-evaluate').disabled=this.busy||this.loading||!this.canEvaluate();
   this.el('prospective-refresh').disabled=this.busy||this.loading;
   this.el('prospective-export').disabled=this.busy||this.loading||!this.isRegistered();
  }
  protocolRule(id){return this.data?.protocol?.rules?.find(r=>r.id===id);}
  direction(rule){return rule?.candidateDirection==='up'?'候选上涨':rule?.candidateDirection==='down'?'候选下跌':'无方向观察';}
  render(){
   if(!this.data)return;const d=this.data,p=d.protocol||{},registered=d.registration;
   const now=Date.parse(d.nowUtc),start=Date.parse(p.startDate+'T00:00:00+08:00'),end=Date.parse(p.observationEndExclusiveAt);
   const phase=d.status==='integrity_error'?'完整性待核对':!registered?(d.canRegister?'尚未登记':'登记期已结束'):now<start?'尚未到验证期':now<end?'前瞻观察中':'全部窗口已到期';
   this.el('prospective-badge').textContent=phase;
   this.el('prospective-protocol').innerHTML=`<div class="prospective-protocol-line"><strong>${E(p.startDate)} — ${E(p.endDate)}</strong><span>每日 00:00 · 北京时间</span></div><p>1 日为主验证，3／7／30 日为探索。最后一个窗口观察至 2027-01-29，需等 ${E(time(p.observationEndExclusiveAt))} 才到期。</p><p class="muted">${registered?'登记于 '+E(time(registered.registeredAtUtc))+'（北京时间），已固定本期协议与全部触发／未触发日。':'登记截止 '+E(time(p.registrationDeadlineAt))+'（北京时间）。登记后固定完整日历清单，不能根据后续行情修改。'}</p>${now<start?'<p class="prospective-phase">尚未到验证期；现在不会产生已评分的命中率。</p>':''}${d.status==='registration_closed'?'<p class="callout">登记窗口已关闭。本期不能补登记为事前验证，需要创建新的未来协议版本。</p>':''}${d.status==='integrity_error'?`<div class="callout">协议或已登记资料的完整性核对未通过，登记和复核已停用；已有结果继续保留。<ul>${(d.integrityErrors||[]).map(x=>`<li>${E(x)}</li>`).join('')}</ul></div>`:''}`;
   this.el('prospective-candidates').innerHTML=(p.rules||[]).map(r=>`<article class="prospective-candidate"><div><span class="tag gray">${E(r.id)}</span><span class="prospective-direction">${this.direction(r)}</span></div><h3>${E(r.title)}</h3><p>${E(typeof r.definition==='string'?r.definition:JSON.stringify(r.definition||''))}</p><p class="muted">${E(r.directionNote||'只记录结构条件，不预设涨跌方向。')}</p></article>`).join('');
   const report=d.latestReport;
   this.el('prospective-evaluation-meta').innerHTML=report?`<p>最近复核 ${E(time(report.createdAtUtc))} · 评分截止 ${E(time(report.asOfUtc))}（北京时间）。</p><p>所用币安缓存取回时间 ${E(time(report.marketAsOfUtc))}；共保留 ${Number(d.evaluationCount)||0} 次评价。更新行情后需再次点击复核。</p>`:'<p>尚无复核结果；登记和读取页面都不会自动评分。</p>';
   const sourceVersion=p.matchingRulesVersion||p.sourceRuleVersion||p.ruleCatalogVersion||p.matchingRuleVersion;
   this.el('prospective-audit-body').innerHTML=`<p>协议 ${E(p.id)} · v${E(p.version||d.version)} · BTC-BZ-V01 ／ BTCUSDT。</p><p>${sourceVersion?'来源规则版本 '+E(sourceVersion)+'。':'条件匹配沿用冻结的 E01–E04；确切输入版本与摘要保留于下方协议。'}E01 上涨、E02 下跌是本期新登记的项目候选方向，尚未获验证。</p><p>方向命中只比较窗口末日收盘与信号日开盘，不是实盘策略胜率。改动规则、方向或窗口需要新建协议版本，旧登记和评价继续保留。</p><details><summary>协议、登记摘要与评价版本记录</summary><pre>${E(JSON.stringify({protocol:p,registration:registered,evaluationHistory:d.evaluationHistory||[],marketValidated:d.marketValidated},null,2))}</pre></details>`;
   this.updateButtons();this.renderResults();
  }
  dateEntries(){
   if(!this.data?.registration)return [];
   const reportByDate=new Map((this.data.latestReport?.daily||[]).map(d=>[d.date,d]));
   return (this.data.schedule||[]).map(day=>{
    const report=reportByDate.get(day.date),h=report?.horizons?.[String(this.horizon)];
    const endExclusive=h?.endExclusiveAt||new Date(Date.parse(day.atLocal)+this.horizon*86400000).toISOString();
    const endDate=h?.endDate||new Date(Date.parse(day.atLocal)+(this.horizon-1)*86400000+8*3600000).toISOString().slice(0,10);
    const due=Date.parse(endExclusive)<=Date.parse(this.data.nowUtc);
    const status=h?.status==='scored'?'scored':h?.status==='missing'?'missing':due?'awaiting_review':'future';
    return {day,h,status,endExclusive,endDate};
   });
  }
  renderResults(){
   if(!this.data)return;const entries=this.dateEntries();
   if(!this.isRegistered())this.el('prospective-progress').innerHTML='<p class="muted">尚未固定本期日期清单。登记后可查看触发日与同规则未触发基准日。</p>';
   else{const count=status=>entries.filter(e=>status.includes(e.status)).length;
    this.el('prospective-progress').innerHTML=`<div class="prospective-progress-heading">按 ${this.horizon} 日窗口统计 ${entries.length} 个公历日${this.horizon===1?' · 主验证':' · 探索窗口'}</div><div class="prospective-counts"><article><strong>${count(['future'])}</strong><span>未来／未到期</span></article><article><strong>${count(['missing','awaiting_review'])}</strong><span>待行情／待复核</span></article><article><strong>${count(['scored'])}</strong><span>已评分</span></article></div><p class="muted">每个日期只计一次，不按四条规则重复计数。尚未到期的窗口不能提前评分；到期后缺任一天完整行情，整窗等待。</p>`;
   }
   this.el('prospective-outcome-note').textContent=`${this.horizon===1?'主验证':'探索'}：信号日开盘至第 ${this.horizon} 日收盘，包含信号当天。基准为同一规则的未触发日；命中率分母含持平日。重叠窗口并非独立样本。`;
   const rows=(this.data.latestReport?.summary||[]).filter(s=>s.horizonDays===this.horizon);
   const rate=value=>typeof value==='number'&&Number.isFinite(value)?value.toFixed(2)+'%':'—';
   const sample=g=>`${n(g.scored)} / ${n(g.eligible)}<small>未到期 ${n(g.pending)} · 缺行情 ${n(g.missing)}</small>`;
   const outcome=(g,direction)=>!direction?'不设方向':g.scored?`${n(g.hits)} / ${n(g.misses)} / ${n(g.ties)}`:'尚无评分';
   this.el('prospective-summary').innerHTML=rows.length?`<div class="table-wrap"><table class="data-table prospective-summary-table"><thead><tr><th scope="col">规则 / 组别</th><th scope="col">已评分 / 计划样本</th><th scope="col">命中 / 失败 / 持平</th><th scope="col">方向命中率</th><th scope="col">均值收益</th><th scope="col">平均收盘路径回撤</th><th scope="col">触发－基准均值差</th></tr></thead><tbody>${rows.map(s=>{const rule=this.protocolRule(s.ruleId)||s;return ['triggered','baseline'].map((group,i)=>{const g=s[group];return `<tr data-rule-id="${E(s.ruleId)}" data-group="${group}"><th scope="row">${i===0?E(s.title||rule.title):'<span class="muted">↳ 同规则基准</span>'}<small>${i===0?'触发日':'未触发日 · 同方向对照'} · ${this.direction(rule)}</small></th><td>${sample(g)}</td><td>${outcome(g,rule.candidateDirection)}</td><td>${rule.candidateDirection&&g.scored?rate(g.hitRatePct):'—'}</td><td>${pct(g.meanReturnPct)}</td><td>${pct(g.meanMaxDrawdownPct)}</td><td>${i===0?pp(s.comparison?.meanReturnDeltaPctPoints):'—'}</td></tr>`;}).join('');}).join('')}</tbody></table></div><p class="muted">基准按相同方向作比较，非已发预测；其符合／不符计数只供对照。“不设方向”的规则仅比较价格路径，命中率留空。回撤沿「起点开盘＋逐日收盘」计算，表内为各窗最大回撤的平均值，不是日内高低价回撤。均值差不代表因果、策略获利或预测已经成立。</p>`:`<p class="prospective-empty">${this.isRegistered()?'尚无复核结果。日期清单已固定；点击“复核本地行情”才会生成评价。':'登记协议后才能生成本期验证结果。'}</p>`;
   this.renderDates();
  }
  filteredDates(){return this.dateEntries().filter(entry=>{
   const triggered=Boolean(entry.day.rules?.[this.rule]?.triggered||entry.day.matches?.some(m=>m.ruleId===this.rule));
   const group=this.group==='all'||(this.group==='triggered'?triggered:!triggered);
   const status=this.status==='all'||(this.status==='pending'?['missing','awaiting_review'].includes(entry.status):entry.status===this.status);
   return group&&status;
  });}
  renderDates(){
   const entries=this.filteredDates();this.page=Math.min(this.page,Math.max(0,Math.ceil(entries.length/this.pageSize)-1));const offset=this.page*this.pageSize,shown=entries.slice(offset,offset+this.pageSize),rule=this.protocolRule(this.rule);
   const labels={future:'未来／未到期',awaiting_review:'已到期，待复核',missing:'待完整行情',scored:'已评分'};
   this.el('prospective-date-table').innerHTML=shown.length?`<div class="table-wrap"><table class="data-table prospective-date-table"><thead><tr><th scope="col">信号日期</th><th scope="col">规则条件</th><th scope="col">窗口末日</th><th scope="col">进度</th><th scope="col">收开收益</th><th scope="col">收盘路径最大回撤</th><th scope="col">方向结果</th></tr></thead><tbody>${shown.map(e=>{const r=e.day.rules?.[this.rule],triggered=Boolean(r?.triggered||e.day.matches?.some(m=>m.ruleId===this.rule)),direction=rule?.candidateDirection,outcome=e.h?.directions?.[direction],reason=r?.reason||e.day.matches?.find(m=>m.ruleId===this.rule)?.reason||'本日未触发此规则';return `<tr data-date="${E(e.day.date)}" data-status="${e.status}"><th scope="row">${E(e.day.date)}<small>${E(e.day.pillars?.day||'')}日</small></th><td><details><summary>${triggered?'触发':'未触发 · 基准'}</summary><p>${E(reason)}</p></details></td><td>${E(e.endDate)}</td><td>${labels[e.status]}${e.h?.missingDates?.length?`<small>缺 ${e.h.missingDates.length} 日</small>`:''}</td><td>${e.status==='scored'?pct(e.h?.returnPct):'—'}</td><td>${e.status==='scored'?pct(e.h?.maxDrawdownPct):'—'}</td><td>${!direction?'无方向':e.status!=='scored'?'—':(triggered?{hit:'命中',miss:'失败',tie:'持平'}:{hit:'对照符合',miss:'对照不符',tie:'持平'})[outcome]||'待核对'}</td></tr>`;}).join('')}</tbody></table></div>`:`<p class="prospective-empty">${this.isRegistered()?'当前筛选没有日期。':'尚未登记日期清单。'}</p>`;
   this.el('prospective-page-label').textContent=entries.length?`${offset+1}–${offset+shown.length} / ${entries.length} 日 · ${this.horizon} 日窗口`:'0 条日期';
   this.el('prospective-prev').disabled=this.page===0;this.el('prospective-next').disabled=offset+this.pageSize>=entries.length;
  }
  async export(){
   if(this.busy||this.loading||!this.isRegistered())return;
   try{const data=await this.request('/api/prospective-export');const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json;charset=utf-8'}));const link=document.createElement('a');link.href=url;link.download='BTC-prospective-study-v0.13.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);this.announce('已生成导出文件，请查看浏览器下载。');}
   catch(error){this.announce('导出未完成：'+error.message,true);}
  }
 }
 window.createProspectiveStudy=()=>new ProspectiveStudy();
})();
