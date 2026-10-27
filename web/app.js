'use strict';
const $ = id => document.getElementById(id);
const state = {config:null, result:null, dirty:true, loading:false, request:0, calendarRequest:0, calendar:null, calendarFilter:'all'};
const bodies = {Sun:'太阳',Moon:'月亮',Mercury:'水星',Venus:'金星',Mars:'火星',Jupiter:'木星',Saturn:'土星',Uranus:'天王星',Neptune:'海王星',Pluto:'冥王星',Rahu:'罗睺',Ketu:'计都'};
const signs = ['白羊','金牛','双子','巨蟹','狮子','处女','天秤','天蝎','射手','摩羯','水瓶','双鱼'];
const columns = ['year','month','day','hour'];
const slots = {'natal.year':'原局年','natal.month':'原局月','natal.day':'原局日','natal.hour':'原局时','flow.year':'流年','flow.month':'流月','flow.day':'流日','flow.hour':'流时'};
const judgements = {observation:'观察',up:'上涨',down:'下跌',range:'震荡',volatility:'波动增大'};
const outcomes = {pending:'待核验',supported:'人工复盘：符合',contradicted:'人工复盘：不符合',ambiguous:'人工复盘：无法判定'};
function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function fraction(v){if(typeof v==='number')return v;const a=String(v).split('/').map(Number);return a[0]/(a[1]||1);}
function cnTime(iso){return new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(iso));}
function toAt(value){if(!value)throw new Error('请填写分析时刻');return value+'+08:00';}
function notify(text,error=false){$('notice').textContent=text;$('notice').classList.toggle('error',error);}
async function api(path,body){const r=await fetch(path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const data=await r.json();if(!r.ok)throw new Error(data.error||'本地服务请求失败');return data;}
function download(name,value){const a=document.createElement('a');const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json;charset=utf-8'}));a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
function table(headers,rows){return `<div class="table-wrap"><table class="data-table"><thead><tr>${headers.map(x=>`<th scope="col">${esc(x)}</th>`).join('')}</tr></thead><tbody>${rows.map(r=>`<tr>${r.map(c=>`<td>${esc(c)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;}
function rawDetails(value,title='查看计算设置与原始结果'){return `<details><summary>${title}</summary><pre>${esc(JSON.stringify(value,null,2))}</pre></details>`;}
let historyStudy='bitstamp';
function setHistoryStudy(study,visible=!$('panel-exploration').hidden){if(!['bitstamp','coinbase','rules','month','confluence','previous'].includes(study))return;historyStudy=study;for(const id of ['bitstamp','coinbase','rules','month','confluence','previous'])$('history-study-'+id).setAttribute('aria-pressed',String(id===study));$('bitstamp-study').hidden=study!=='bitstamp';$('coinbase-study').hidden=study!=='coinbase';$('rule-validation').hidden=study!=='rules';$('month-study').hidden=study!=='month';$('confluence-study').hidden=study!=='confluence';$('historical-exploration').hidden=study!=='previous';window.researchBitstamp?.setVisible(visible&&study==='bitstamp');window.researchCoinbase?.setVisible(visible&&study==='coinbase');window.researchRuleValidation?.setVisible(visible&&study==='rules');window.researchMonth?.setVisible(visible&&study==='month');window.researchConfluence?.setVisible(visible&&study==='confluence');window.researchExploration?.setVisible(visible&&study==='previous');}
for(const id of ['bitstamp','coinbase','rules','month','confluence','previous'])$('history-study-'+id).addEventListener('click',()=>setHistoryStudy(id));
function setTab(tab){document.querySelectorAll('[role=tab]').forEach(b=>{const on=b.dataset.tab===tab;b.setAttribute('aria-selected',String(on));b.tabIndex=on?0:-1;$('panel-'+b.dataset.tab).hidden=!on;});window.researchTradingView?.setVisible(tab==='chart');window.researchHistory?.setVisible(tab==='chart');window.researchCoinbaseForward?.setVisible(tab==='prospective'||tab==='chart');window.researchProspective?.setVisible(tab==='prospective');window.researchMonthInteraction?.setVisible(tab==='prospective');window.researchTiming?.setVisible(tab==='timing');window.researchTimeCalendar?.setVisible(tab==='calendar');window.researchPredictions?.setVisible(tab==='prediction');setHistoryStudy(historyStudy,tab==='exploration');}
document.querySelectorAll('[role=tab]').forEach(b=>{b.addEventListener('click',()=>setTab(b.dataset.tab));b.addEventListener('keydown',e=>{const tabs=[...document.querySelectorAll('[role=tab]')];let n=tabs.indexOf(b);if(e.key==='ArrowRight')n=(n+1)%tabs.length;else if(e.key==='ArrowLeft')n=(n+tabs.length-1)%tabs.length;else if(e.key==='Home')n=0;else if(e.key==='End')n=tabs.length-1;else return;e.preventDefault();tabs[n].focus();setTab(tabs[n].dataset.tab);});});

const elementGroups = {木:'甲乙寅卯',火:'丙丁巳午',土:'戊己辰戌丑未',金:'庚辛申酉',水:'壬癸亥子'};
const elementClasses = {木:'wood',火:'fire',土:'earth',金:'metal',水:'water'};
function elementOf(character){return Object.keys(elementGroups).find(e=>elementGroups[e].includes(character))||'';}
function elementCharacter(character,tag=false){const e=elementOf(character);return `<span class="element-character el-${elementClasses[e]||'unknown'}" title="${esc(character+' · '+e)}" aria-label="${esc(character+'，五行'+e)}"><span aria-hidden="true">${esc(character)}</span>${tag?`<small aria-hidden="true">${esc(e)}</small>`:''}</span>`;}
function coloredPillar(pillar,tag=false){return `<span class="colored-pillar">${[...String(pillar||'')].map(c=>elementCharacter(c,tag)).join('')}</span>`;}
function elementLegend(){return `<div class="element-legend" aria-label="五行颜色图例">${Object.keys(elementGroups).map(e=>`<span class="el-${elementClasses[e]}"><i aria-hidden="true"></i>${e}</span>`).join('')}<span class="legend-note">每字分别着色</span></div><p class="muted element-explainer">例如 ${coloredPillar('庚午')}＝干金／支火。地支颜色表示本气五行，藏干另列；颜色不代表涨跌。</p>`;}
function pillarTable(title,pillars,prefix,rels,flow=false){
 const hidden=state.result.bazi.features?.hiddenStems||[];
 return `<div class="section-title"><h2>${title}</h2><span class="muted">${flow?'分析时刻的年月日时':'已记录四柱 · 未由出生时地重排'}</span></div><table class="four-pillars ${flow?'flow':''}"><thead><tr><th scope="col" class="pillar-row-label">四柱</th>${['年柱','月柱','日柱','时柱'].map(x=>`<th scope="col">${x}</th>`).join('')}</tr></thead><tbody><tr class="pillar-ten-gods"><th scope="row">十神</th>${columns.map(k=>{const token=rels.tokens.find(x=>x.slot===prefix+'.'+k);return `<td><span class="pillar-role">${esc(prefix==='natal'&&k==='day'?'日主':token?.tenGod?.name||'—')}</span></td>`;}).join('')}</tr><tr><th scope="row">天干</th>${columns.map(k=>`<td class="pillar-character-cell">${elementCharacter(pillars[k][0],true)}</td>`).join('')}</tr><tr><th scope="row">地支</th>${columns.map(k=>`<td class="pillar-character-cell">${elementCharacter(pillars[k][1],true)}</td>`).join('')}</tr>${!flow?`<tr class="hidden-stems-row"><th scope="row">藏干</th>${columns.map(k=>`<td>${hidden.filter(x=>x.slot===prefix+'.'+k).map(x=>`<div class="hidden-stem">${elementCharacter(x.stem,true)}<span class="pillar-role">${esc(x.tenGod)}</span></div>`).join('')||'<span class="muted">—</span>'}</td>`).join('')}</tr>`:''}</tbody></table>`;
}
function renderBazi(){const {calendar,relationships:r}=state.result.bazi;const matches=r.branchRelations;
 const pairs=matches.pairMatches.map(x=>`<div class="relation"><b>${esc(x.pattern)} · ${esc(x.type)}</b><small>${x.slots.map(s=>esc(slots[s])).join(' / ')}</small></div>`).join('');
 const groups=matches.completeGroups.map(x=>`<div class="relation"><b>${esc(x.pattern)} · ${esc(x.type)}</b><small>支齐全 · 合化未定</small></div>`).join('');
 $('panel-bazi').innerHTML=elementLegend()+pillarTable('原局',r.profile.pillars,'natal',r)+pillarTable('流时',calendar.pillars,'flow',r,true)+`<p class="token-note">十神均以原局日主「${esc(r.natalDayReference.stem)}」为参照。${calendar.hourStemReference==='next_day_late_zi'?'晚子时：日柱仍按当天，时干参考次日日干。':'北京时间 00:00 换日，未使用真太阳时。'}</p>`+
 (calendar.nearBoundaryWarnings.length?'<div class="callout">接近节气边界，秒级时刻尚未独立核验。</div>':'')+
 `<section class="calendar-section" aria-labelledby="calendar-heading"><div class="section-title"><h2 id="calendar-heading">年月日 · 五行日历</h2><span class="tag gray">日干标签 ≠ 涨跌信号</span></div><div id="calendar-browser" aria-live="polite"><p class="muted">正在读取当前时刻的日历…</p></div></section>`+
 `<details class="chart-plan"><summary>K 线与时间：图上怎样读五行？</summary><ol><li><strong>K 线主图保留涨跌色。</strong>默认不逐根贴字；需要观察的规则事件再以开关控制。</li><li><strong>图下增加五行细条。</strong>日干、月干与年干分别成行；缩放后显示对应层级，交节边界标出精确时刻。</li><li><strong>鼠标悬停或点选看详情。</strong>显示北京时间、四柱、开高低收、触发条件和验证状态；日期轴只保留简短干支标签。</li></ol><p class="muted">日干火、日干金水先作为时间属性。将来若显示候选涨跌判断，需同时附规则版本与检验状态。“K 线与时间”页可查看固定 BTC 历史复盘，上方使用 TradingView 嵌入图查看 Coinbase BTC／ETH 的4小时、日、周、月K线；下方保留旧研究样本与时间带，币安网络更新已关闭。两种模式都保留日干／日支条和流月交接；未来30至365天候选可预览，正式登记单独确认。</p></details>`+
 `<div class="groups"><div class="section-title"><h2>地支关系</h2><span class="tag gray">结构匹配</span></div><p class="muted">并列显示六合、六冲、三合、三会；关系出现不等于合化或涨跌。</p><div class="relations">${pairs+groups||'<span class="muted">本次未出现已实现的完整组合。</span>'}</div></div>`+rawDetails({calendarPolicy:calendar.policy,profile:r.profile,reference:r.natalDayReference,branchRelations:matches,sourceRefs:r.sourceRefs});
}
function invalidateCalendar(message='设置已改变，重新计算后显示对应日历。'){
 ++state.calendarRequest;state.calendar=null;const container=$('calendar-browser');
 if(container){container.setAttribute('aria-busy','true');container.innerHTML=`<p class="muted calendar-pending">${esc(message)}</p>`;}
}
async function loadCalendar(at){
 const ticket=++state.calendarRequest,calculation=state.request;
 const container=$('calendar-browser');if(!container)return;
 container.setAttribute('aria-busy','true');container.innerHTML='<p class="muted calendar-pending">正在读取对应年份、交节边界与逐日干支…</p>';
 try{const document=await api('/api/calendar-browser?'+new URLSearchParams({at}));
  if(ticket!==state.calendarRequest||calculation!==state.request||state.dirty||state.result?.input.at!==at)return;
  state.calendar=document;renderCalendarBrowser();
 }catch(error){if(ticket!==state.calendarRequest||calculation!==state.request)return;container.setAttribute('aria-busy','false');container.innerHTML=`<div class="callout">日历暂未加载：${esc(error.message)}。可重新计算重试。</div>`;}
}
function calendarShortDate(at){if(!at)return '范围外';return String(at).slice(5,10).replace('-','/')+' '+String(at).slice(11,16);}
function calendarDateTime(at){if(!at)return '范围外';return String(at).slice(0,19).replace('T',' ');}
function calendarCard(item,type){
 const pillar=item.pillar||item.ganzhi,at=item.at||item.clickAt,unavailable=item.available===false||!at||!pillar;
 const label=type==='year'?String(item.year)+'年':(item.termName||item.term);
 const title=`${label} ${pillar||'支持范围外'}；区间 ${calendarDateTime(item.startAt)} 至 ${calendarDateTime(item.endAt)}（不含）；点击切换分析时刻为 ${calendarDateTime(at)} UTC+8`;
 return `<button type="button" class="calendar-card ${item.selected?'is-selected':''}" data-calendar-at="${esc(at||'')}" data-calendar-kind="${type}" aria-pressed="${Boolean(item.selected)}" title="${esc(title)}" aria-label="${esc(title)}" ${unavailable?'disabled':''}><span class="calendar-card-name">${esc(label)}</span>${pillar?coloredPillar(pillar):'<span class="muted">—</span>'}<small>${unavailable?'范围外':calendarShortDate(item.clippedStart?item.displayStartAt:item.startAt)}</small>${item.clippedStart?'<small>支持起点截断</small>':''}</button>`;
}
function calendarDayMatches(pillar){const e=elementOf(String(pillar||'')[0]);return state.calendarFilter==='all'||(state.calendarFilter==='metal-water'?(e==='金'||e==='水'):e==='火');}
function calendarDay(day){
 const pillar=day.pillar||day.dayGanzhi,at=day.at||day.clickAt,e=elementOf(pillar[0]),matches=calendarDayMatches(pillar);
 const transitions=day.termTransitions||[],boundary=day.hasTermBoundary;
 const boundaryInfo=transitions.map(t=>(t.termName||t.term)+' '+calendarDateTime(t.at)+(t.changesMonth?'，流月'+t.beforePillar+'→'+t.afterPillar:'')).join('；');
 const title=`${day.date} ${pillar}，日干${e}；点击按当日 12:00 北京时间计算${boundaryInfo?'；'+boundaryInfo:''}`;
 return `<button type="button" class="calendar-day ${day.selected?'is-selected':''} ${matches?'is-match':'is-unmatched'}" data-calendar-at="${esc(at)}" data-calendar-kind="day" data-day-number="${day.dayNumber}" aria-pressed="${Boolean(day.selected)}" aria-label="${esc(title)}" title="${esc(title)}"><span class="day-number">${day.dayNumber}${boundary?'<i class="term-dot" aria-hidden="true"></i>':''}</span>${coloredPillar(pillar)}<small class="day-element el-${elementClasses[e]}">干${e}</small>${boundary?`<span class="day-term">${esc(transitions.map(t=>t.termName||t.term).join('/'))||'节气'}</span>`:''}</button>`;
}
function renderCalendarBrowser(){
 const c=state.calendar,container=$('calendar-browser');if(!c||!container)return;
 const selected=c.selected,year=c.year||selected.year,month=c.month||selected.month;
 const first=c.days[0],padding=(new Date(first.date+'T00:00:00Z').getUTCDay()+6)%7;
 const transitionDays=c.days.filter(d=>d.hasTermBoundary);
 const nav=c.navigation||{};
 container.setAttribute('aria-busy','false');
 container.innerHTML=`<p class="muted">年柱在立春交接，月柱在十二「节」交接。点年／月卡切换到可用区间起点后 3 分钟（通常为交节后），点日期切换到当天 12:00。均为北京时间。</p><div class="calendar-row-title"><h3>流年</h3><span>立春起始 · 可横向浏览</span></div><div class="calendar-scroll year-scroll" role="group" aria-label="流年选择">${c.years.map(y=>calendarCard(y,'year')).join('')}</div><div class="calendar-row-title"><h3>流月</h3><span>${esc(c.baziYear)} 立春年 · 下方为交节起点</span></div><div class="calendar-scroll month-scroll" role="group" aria-label="流月选择">${c.months.map(m=>calendarCard(m,'month')).join('')}</div><div class="calendar-month-heading"><h3>${esc(year)}年 ${esc(month)}月 <small>公历日历</small></h3><div class="calendar-nav"><button type="button" class="quiet" data-calendar-at="${esc(nav.previousMonthAt||'')}" data-calendar-kind="browse" aria-label="上一个公历月，切换到1日12点" ${nav.previousMonthAt?'':'disabled'}>← 上月</button><button type="button" class="quiet" data-calendar-at="${esc(nav.nextMonthAt||'')}" data-calendar-kind="browse" aria-label="下一个公历月，切换到1日12点" ${nav.nextMonthAt?'':'disabled'}>下月 →</button></div></div><div class="calendar-filter" role="group" aria-label="按日干高亮日期"><span>日干高亮</span>${[['all','全部'],['metal-water','金／水'],['fire','火']].map(([key,label])=>`<button type="button" data-calendar-filter="${key}" aria-pressed="${state.calendarFilter===key}">${label}</button>`).join('')}</div><div class="calendar-weekdays" aria-hidden="true">${['一','二','三','四','五','六','日'].map(d=>`<span>${d}</span>`).join('')}</div><div class="calendar-days" role="group" aria-label="${year}年${month}月日期">${Array.from({length:padding},()=>'<span class="calendar-blank" aria-hidden="true"></span>').join('')}${c.days.map(calendarDay).join('')}</div><p class="calendar-key muted"><i class="term-dot" aria-hidden="true"></i>节气日 · 边框标出所选日；“干金／干水／干火”只按日干归类，不表示上涨或下跌。</p><p class="muted">所选分析时刻：${esc(calendarDateTime(selected.at))}。流年／流月以此时刻的精确边界选中，交节日内可能不同。</p>${transitionDays.length?`<details class="calendar-boundaries"><summary>本月 ${transitionDays.length} 个节气日期与交接时刻</summary><ul>${transitionDays.flatMap(d=>(d.termTransitions||[]).map(t=>`<li><strong>${esc(t.termName||t.term)}</strong> ${esc(calendarDateTime(t.at))}${t.changesMonth?` · 流月 ${coloredPillar(t.beforePillar)} → ${coloredPillar(t.afterPillar)}`:' · 中气，不换月柱'}${t.changesYear?' · 同时换年柱':''}</li>`)).join('')}</ul><p class="muted">交接时刻前后分别计算；日历格采用当日中午快照，不将整天统一当成交节后的月柱。秒级节气边界尚未独立核验。</p></details>`:''}`;
 container.querySelectorAll('.calendar-scroll').forEach(scroller=>{const active=scroller.querySelector('.is-selected');if(active)scroller.scrollLeft=active.offsetLeft-scroller.offsetLeft-scroller.clientWidth/2+active.clientWidth/2;});
}
$('panel-bazi').addEventListener('click',event=>{
 const filter=event.target.closest('[data-calendar-filter]');if(filter){state.calendarFilter=filter.dataset.calendarFilter;renderCalendarBrowser();$('calendar-browser').querySelector(`[data-calendar-filter="${state.calendarFilter}"]`)?.focus();return;}
 const button=event.target.closest('[data-calendar-at]');if(!button||button.disabled||!button.dataset.calendarAt||state.loading)return;
 const local=new Date(new Date(button.dataset.calendarAt).getTime()+8*3600000).toISOString().slice(0,19);
 $('at').value=local;markDirty();state.calendarSelectionNote='日历已将分析时刻切换至 '+local.replace('T',' ')+'，未保存记录。';run();
});
$('panel-bazi').addEventListener('keydown',event=>{
 const button=event.target.closest('.calendar-day');if(!button)return;const offset={ArrowLeft:-1,ArrowRight:1,ArrowUp:-7,ArrowDown:7}[event.key];if(offset===undefined)return;
 const target=$('calendar-browser').querySelector(`[data-day-number="${Number(button.dataset.dayNumber)+offset}"]`);if(target){event.preventDefault();target.focus();}
});
function degree(v){return Number(v).toFixed(4)+'°';}
function renderIndian(){const data=state.result.indian;if(!data.available){$('indian-result').innerHTML=`<div class="callout">${esc(data.reason)}</div>`;return;}
 const rows=data.positions.map(x=>[bodies[x.body]||x.body,degree(x.longitudeDeg),x.divisions.d1.signZh,x.divisions.d9.signZh,(x.nakshatraName||('宿 '+x.divisions.nakshatra.number))+' · '+x.divisions.nakshatra.padaNumber+' 足']);
 $('indian-result').innerHTML=`<div class="section-title"><h2>恒星黄道 · D1 / D9</h2><span class="tag">Lahiri</span></div><p class="muted">分析时刻的地心星位；不含上升点或宫位。岁差 ${degree(data.ayanamsaDeg)}。</p>`+table(['天体','恒星黄经','D1 星座','D9 星座','月宿 / 足'],rows)+
 (data.boundaryWarnings.length?`<div class="callout">${data.boundaryWarnings.map(x=>esc(x.bodyLabel||x.body)).join('、')} 接近分段边界；时间与星位误差可能改变分类。</div>`:'')+rawDetails({settings:data.settings,engine:data.engine,time:data.time,validation:data.validation},'查看印度占星计算设置');
 renderDasa(data);
}
function renderDasa(data){if(!data.vimshottari){$('dasa-result').innerHTML='<p class="muted">未指定资产出生事件，因此默认不生成资产本命运限。</p>';return;}
 const v=data.vimshottari;const rows=v.mahadashas.filter(m=>m.visibleFromBirth).map(m=>[bodies[m.lord]||m.lord,cnTime(m.visibleFromBirth.startAtUtc),cnTime(m.visibleFromBirth.endAtUtcExclusive),m.containsBirth?'首限余期':m.fullLordYears+' 年']);
 const active=v.mahadashas.find(m=>m.containsBirth).antardashas.filter(x=>x.visibleFromBirth);
 $('dasa-result').innerHTML=`<div class="callout">假设起盘演算 · 不代表所选资产的真实本命运限。固定每年 365.2425 天，首限先排全长再截取。</div><p class="small">起点所在大限：${esc(bodies[v.initialMahadasha.lord])}；余期约 ${fraction(v.initialMahadasha.remainingYearsExact).toFixed(4)} 年。表内时间为北京时间，结束边界不包含。</p>`+table(['大限','可见起点','结束','说明'],rows)+`<details><summary>查看首限剩余小限</summary>${table(['小限','可见起点','结束'],active.map(m=>[bodies[m.lord]||m.lord,cnTime(m.visibleFromBirth.startAtUtc),cnTime(m.visibleFromBirth.endAtUtcExclusive)]))}</details>`+rawDetails(v,'查看完整运限时间线');
}
function renderWestern(){const sky=state.result.tropical;const rows=sky.positions.map(p=>[bodies[p.body]||p.body,signs[Math.floor(p.longitudeDeg/30)],degree(p.longitudeDeg%30),degree(p.longitudeDeg),degree(p.latitudeDeg)]);$('panel-western').innerHTML='<div class="section-title"><h2>回归黄道星位</h2><span class="tag gray">Astronomy Engine</span></div><p class="muted">地心 · 当日真黄道。与印度占星的恒星黄道分别显示。</p>'+table(['天体','星座','座内角度','黄经','黄纬'],rows)+`<div class="groups"><div class="section-title"><h2>几何相位</h2><span class="muted">容许度固定 1°</span></div>`+table(['天体 A','天体 B','相位','偏差'],sky.aspects.map(x=>[bodies[x.bodyA],bodies[x.bodyB],x.exactAngleDeg+'°',degree(x.deviationDeg)]))+'</div>'+rawDetails({engine:sky.engine,coordinates:sky.coordinates,time:sky.time,settings:sky.aspectSettings});}
function renderEvidence(){if(!state.config)return;const labels={BAZI:'八字',GANN:'江恩',INDIAN_ASTROLOGY:'印度占星',WESTERN_ASTROLOGY:'西洋占星'};$('panel-evidence').innerHTML='<div class="section-title"><h2>候选规则</h2><span class="tag amber">尚待验证</span></div><p class="muted">规则按理论组织。已有资料提供研究线索，输入定义和验证要求逐项保留。</p><p><a href="/october-source-audit" target="_blank" rel="noopener">查看 2026 年 10 月外部观点核验记录 ↗</a></p>'+state.config.rules.map(r=>`<article class="rule"><div class="rule-head"><h3>${esc(r.title)}</h3><span class="tag gray">${esc(labels[r.module]||r.module)}</span></div><p>${esc(r.statement)}</p><details><summary>查看条件与缺口</summary><p>需要：${r.requiredInputs.map(esc).join('；')}</p><p>待补：${r.missingDefinitions.map(esc).join('；')}</p><p>规则编号 ${esc(r.id)} · 版本 ${esc(r.version)}</p><p>资料证据 ${r.evidenceRefs.map(esc).join('、')}</p></details></article>`).join('')+'<div class="groups"><h2>算法资料</h2>'+state.config.sources.map(s=>`<div class="source"><a target="_blank" rel="noopener noreferrer" href="${esc(s.url)}">${esc(s.title)} ↗</a><p class="muted">${esc(s.supports)}</p></div>`).join('')+'</div>';}
function renderSummary(){const r=state.result;const p=state.config.profiles.find(x=>x.id===r.input.profile);$('summary-title').textContent=p.label;$('summary').innerHTML=`<div class="kv"><span>分析时刻</span><strong>${esc(cnTime(r.input.at))}</strong></div><div class="kv"><span>原局日主</span><strong>${elementCharacter(r.bazi.relationships.natalDayReference.stem,true)}</strong></div><div class="kv"><span>流日日干</span><strong>${elementCharacter(r.bazi.calendar.pillars.day[0],true)}</strong></div><p class="small">${p.entityType==='person'?'人物时辰假设，与资产盘分开。':'原局沿用记录；出生时地尚未确认。'}</p><details><summary>本次计算指纹</summary><p class="mono">${esc(r.snapshotSha256)}</p></details>`;}
function markDirty(){state.request++;state.loading=false;$('compute').disabled=false;state.dirty=true;state.calendarSelectionNote='';invalidateCalendar();window.researchHistory?.invalidateContext();window.researchTiming?.updateContext();$('save').disabled=true;$('export').disabled=true;notify('设置已改变，请重新计算。');}
async function run(){if(!$('controls').reportValidity())return;const ticket=++state.request;state.loading=true;invalidateCalendar('正在计算新时刻，日历将同步更新…');$('compute').disabled=true;$('save').disabled=true;$('export').disabled=true;notify('正在计算四柱、星位与分盘…');try{const q=new URLSearchParams({profile:$('profile').value,at:toAt($('at').value),scenario:String($('scenario').checked)});const result=await api('/api/calculate?'+q);if(ticket!==state.request)return;state.result=result;state.dirty=false;renderBazi();loadCalendar(result.input.at);renderFeatures();renderIndian();renderWestern();renderSummary();window.researchHistory?.onCalculation(result);$('save').disabled=false;$('export').disabled=false;notify(`已计算 · ${cnTime(result.input.at)}（UTC+8） · 结果保留设置与版本。${state.calendarSelectionNote?' '+state.calendarSelectionNote:''}`);}catch(e){if(ticket!==state.request)return;notify(e.message,true);state.dirty=true;invalidateCalendar('计算未完成，请检查设置后重新计算。');}finally{if(ticket===state.request){state.loading=false;$('compute').disabled=false;}}}
$('controls').addEventListener('submit',e=>{e.preventDefault();run();});
for(const id of ['profile','at'])$(id).addEventListener('input',markDirty);
$('scenario').addEventListener('change',()=>{markDirty();run();});
for(const [id,n] of [['prev',-1],['next',1]])$(id).addEventListener('click',()=>{const d=new Date(toAt($('at').value));d.setTime(d.getTime()+n*86400000);$('at').value=new Date(d.getTime()+8*3600000).toISOString().slice(0,19);markDirty();run();});
$('export').addEventListener('click',()=>{if(state.result&&!state.dirty)download('研究计算_'+state.result.input.profile+'.json',state.result);});
$('judgement').addEventListener('change',()=>{const show=$('judgement').value!=='observation';$('window-fields').hidden=!show;for(const id of ['window-start','window-end'])$(id).required=show;});
$('save-form').addEventListener('submit',async e=>{e.preventDefault();if(state.dirty||!state.result||state.loading)return;$('save').disabled=true;try{const directional=$('judgement').value!=='observation';const body={...state.result.input,snapshotSha256:state.result.snapshotSha256,note:$('note').value,judgement:$('judgement').value,windowStart:directional?toAt($('window-start').value):null,windowEnd:directional?toAt($('window-end').value):null};const r=await api('/api/records',body);$('save-status').textContent='已保存 '+cnTime(r.created)+'；原判断不会被复盘覆盖。';if($('note').value===body.note)$('note').value='';await refreshRecords();}catch(err){$('save-status').textContent=err.message;}finally{$('save').disabled=state.dirty;}});
async function refreshRecords(){try{const {records}=await api('/api/records');if(!records.length){$('records').innerHTML='<div class="empty">还没有记录。完成一次计算后可以保存。</div>';return;}$('records').innerHTML=records.map(r=>{const profile=state.config.profiles.find(p=>p.id===r.input.profile);return `<article class="record" data-id="${esc(r.id)}"><div class="record-header"><h3>${esc(profile?.label||r.input.profile)} · ${esc(judgements[r.judgement])}</h3><small>保存于 ${esc(cnTime(r.created))}</small></div><p class="muted">分析时刻 ${esc(cnTime(r.input.at))}${r.window_start?' · 验证窗口 '+esc(cnTime(r.window_start))+' — '+esc(cnTime(r.window_end)):''}</p>${r.timing_class==='historical_or_already_started'?'<p><span class="tag amber">历史或已开始窗口 · 不记作事前预测</span></p>':''}${r.input.scenario?'<p class="muted">含假设起盘运限演算</p>':''}<p>${esc(r.note||'未填写笔记')}</p><div class="review-list">${r.reviews.length?r.reviews.map(v=>`<p><strong>${esc(outcomes[v.outcome])}</strong> <small>${esc(cnTime(v.created))}</small><br>${esc(v.note)}</p>`).join(''):'<span class="muted">尚未追加复盘。</span>'}</div><div class="record-actions"><button class="quiet" data-action="export-record">导出记录</button></div><details><summary>追加人工复盘</summary><form class="review-form"><label>核验结果<select name="outcome"><option value="ambiguous">无法判定</option><option value="supported">符合原判断</option><option value="contradicted">不符合原判断</option><option value="pending">仍待核验</option></select></label><label>核验依据<textarea name="note" maxlength="4000" required placeholder="写明实际结果、数据来源与评价标准。"></textarea></label><button class="secondary" type="submit">追加复盘</button></form><p class="small">人工记录不等于自动回测或规则已获验证。</p><p class="review-status small" role="status"></p></details></article>`;}).join('');}catch(e){$('records').innerHTML=`<div class="callout">${esc(e.message)}</div>`;}}
$('records').addEventListener('click',async e=>{const b=e.target.closest('[data-action=export-record]');if(!b)return;try{const id=b.closest('[data-id]').dataset.id;download('研究记录_'+id+'.json',await api('/api/records/'+id));}catch(err){notify(err.message,true);}});
$('records').addEventListener('submit',async e=>{const f=e.target.closest('.review-form');if(!f)return;e.preventDefault();const card=f.closest('[data-id]');const b=f.querySelector('button');b.disabled=true;try{await api('/api/records/'+card.dataset.id+'/reviews',{outcome:f.elements.outcome.value,note:f.elements.note.value});await refreshRecords();}catch(err){card.querySelector('.review-status').textContent=err.message;b.disabled=false;}});
$('refresh-records').addEventListener('click',refreshRecords);
async function renderResearch(){
 try{
  const data=await api('/api/research');
  const bp=x=>Number(x).toFixed(2);
  const results=data.study.periods.map(p=>[p.start+' — '+p.end,p.observedDays+' / '+p.expectedDays,bp(p.deltaBp),p.bootstrap.ci95Bp?p.bootstrap.ci95Bp.map(bp).join(' 至 '):'不可用',String(p.bootstrap.replicatesValid)]);
  const sources=data.birthCatalog.sources.map(s=>`<li><a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.title)}</a></li>`).join('');
  const events=data.events.map(e=>`<div class="rule"><h3>${esc(e.label)}</h3><p>记录时间：${esc(e.eventAtUtc)}<br>北京时间：${esc(e.atResearchTimezone)}</p><p>按当前设置排得：<strong>${columns.map(k=>esc(e.pillars[k])).join(' · ')}</strong></p><p>${e.hourStemReference==='next_day_late_zi'?'晚子时：日柱保留当天，时干参考次日。':''}与已有同资产原局${e.sameFourPillarsUnderResearchConvention.length?'有四柱相符版本':'均不完全相符'}；不自动覆盖原局。</p></div>`).join('');
  $('research-result').innerHTML=`<div class="section-title"><h2>金水日 · 第一轮历史检验</h2><span class="tag amber">尚未验证</span></div><p class="muted">固定历史样本的研究记录，不随顶部分析时刻重新评分。</p><p>${esc(data.headline)}</p><div class="callout">这是已看过历史资料后的探索性修订。分组为日干庚辛壬癸与其余日期，比较北京时间开收收益；不代表完整八字体系。</div>`+
   table(['时期','完整 / 预计日','均值差 · bp','95%区间 · bp','有效抽样'],results)+
   `<p class="muted">1 bp = 0.01个百分点。区间含零，表示本次方法尚不能排除没有差异。每段固定30日块、10,000次抽样；2025–2026不是独立样本外。</p><details><summary>按年份看结果</summary>`+
   table(['年份','有效 / 预计日','均值差 · bp'],data.study.periods.flatMap(p=>p.yearly.map(y=>[y.year+(y.partialYear?'（部分年）':''),y.observedDays+' / '+y.expectedDays,bp(y.deltaBp)])))+'</details>'+
   `<div class="groups"><h2>行情数据复核</h2><p class="muted">补回${data.addedHours}小时后，${data.recovery.completeDays} / ${data.recovery.expectedDays}天完整，仍保留${data.recovery.remainingIncompleteDays}天缺口。档案与API另有3条实质来源差异，保留原件与冲突记录；没有补造价格。</p><p class="muted">旧版要求全段无缺口，2020–2024仍未通过。探索版在完整日上统计，抽样时保留缺失日期的位置。</p></div>`+
   `<div class="groups"><h2>候选诞生事件对照</h2><p class="muted">事件时间有出处，选择哪个事件作为本命盘仍待研究。地点未知，宫位关闭。以下全部使用当前北京时间日界，不能据此认定原作者的设置。</p>${events}<div class="callout">ETH创世头的零时间戳不能当作1970年出生；ETH白皮书资料只有日期线索，未擅自补午夜。原BTC四柱、ETH A/B两个版本均保留。</div></div>`+
   `<details><summary>原始时间依据</summary><ul>${sources}</ul></details>`+rawDetails(data.study,'查看完整检验、年度结果与限制')+
   `<p class="source"><a href="/api/research" target="_blank" rel="noopener">打开本版研究结果 JSON</a></p>`;
 }catch(e){$('research-result').innerHTML=`<div class="callout">研究结果读取失败：${esc(e.message)}</div>`;}
}

async function init(){try{window.researchTiming=window.createTimingStudy({getContext:()=>({profile:$('profile').value,label:state.config?.profiles.find(p=>p.id===$('profile').value)?.label||$('profile').value}),selectAt:async(at,symbol)=>{const profile=$('profile').value,mapping={'BTC-BZ-V01':'BTCUSDT','ETH-BAZI-A':'ETHUSDT','ETH-BAZI-B':'ETHUSDT'};if(!state.config?.profiles.some(p=>p.id===profile)||symbol&&mapping[profile]!==symbol)throw new Error('请先在顶部选择与时间投影一致的资产盘');const dt=new Date(at),local=new Date(dt.getTime()+8*3600000).toISOString().slice(0,19);if(local<'1992-01-01T00:00:00'||local>'2099-12-31T23:59:59')throw new Error('此时刻超出排盘支持范围');$('at').value=local;markDirty();state.calendarSelectionNote='由时间研究选择 '+local.replace('T',' ')+'，未保存记录。';await run();if(state.dirty||$('profile').value!==profile||state.result?.input.profile!==profile||new Date(state.result.input.at).getTime()!==dt.getTime())throw new Error('设置已改变或计算未完成');return true;}});window.researchCoinbaseForward=window.createCoinbaseForward();window.researchProspective=window.createProspectiveStudy();window.researchMonthInteraction=window.createMonthInteractionFuture();window.researchHistory=window.createHistoryChart({useCandidate:(candidate,snapshot)=>{setTab('prediction');window.researchPredictions.useExternalCandidate(candidate,snapshot);},getContext:()=>({profile:$('profile').value,at:$('at').value?toAt($('at').value):null,dirty:state.dirty,loading:state.loading}),selectAt:async (at,requestedProfile)=>{const profile=$('profile').value;if(profile!==requestedProfile||!['BTC-BZ-V01','ETH-BAZI-A','ETH-BAZI-B'].includes(profile))throw new Error('当前盘无匹配行情');$('at').value=new Date(new Date(at).getTime()+8*3600000).toISOString().slice(0,19);markDirty();state.calendarSelectionNote='由历史K线选择当日00:00，未保存记录。';await run();if(state.dirty||state.result?.input.profile!==profile||$('profile').value!==profile||new Date(state.result.input.at).getTime()!==new Date(at).getTime())throw new Error('设置已改变或计算未完成');return true;}});window.researchPredictions=window.createPredictionLedger({onChange:data=>window.researchHistory?.setPredictions(data),openChart:async(record,ledger)=>{if(record.symbol==='BTCUSD'||record.symbol==='ETHUSD'){const asset=record.symbol==='ETHUSD'?'ETH':'BTC';$('profile').value=asset==='ETH'?'ETH-BAZI-A':'BTC-BZ-V01';markDirty();setTab('chart');window.researchTradingView.showAsset(asset,record);return;}const mapping={'BTC-BZ-V01':'BTCUSDT','ETH-BAZI-A':'ETHUSDT','ETH-BAZI-B':'ETHUSDT'};if(mapping[$('profile').value]!==record.symbol){$('profile').value=record.symbol==='ETHUSDT'?'ETH-BAZI-A':'BTC-BZ-V01';markDirty();}window.researchHistory.setPredictions(ledger);setTab('chart');await window.researchHistory.openPrediction(record);}});window.researchBitstamp=window.createBitstampStudy();window.researchCoinbase=window.createCoinbaseStudy();window.researchExploration=window.createHistoricalExploration();window.researchMonth=window.createMonthStudy();window.researchRuleValidation=window.createRuleValidationDesk();window.researchConfluence=window.createConfluenceStudy();state.config=await api('/api/config');$('profile').innerHTML=state.config.profiles.map(p=>`<option value="${esc(p.id)}">${esc(p.label)}</option>`).join('');$('at').value=new Date(Date.now()+8*3600000).toISOString().slice(0,19);renderEvidence();await renderDefinitions();renderCalendarAccuracy();await renderResearch();await renderStructureStudy();await run();await refreshRecords();
 if(document.modelContext?.registerTool){const controller=new AbortController();document.modelContext.registerTool({name:'set_research_calculation',title:'选择研究盘与时刻',description:'更新本页的盘版本和分析时刻并显示计算结果，不保存记录。',inputSchema:{type:'object',properties:{profile:{type:'string',enum:state.config.profiles.map(x=>x.id)},at:{type:'string'}},required:['profile','at'],additionalProperties:false},annotations:{readOnlyHint:false,untrustedContentHint:false},execute:async input=>{if(!input||!state.config.profiles.some(x=>x.id===input.profile)||typeof input.at!=='string'||!/(Z|[+-]\d{2}:\d{2})$/.test(input.at))throw new Error('需要有效盘版本与带时区时间');const dt=new Date(input.at);if(!Number.isFinite(dt.getTime()))throw new Error('时间无效');await api('/api/calculate?'+new URLSearchParams({profile:input.profile,at:input.at,scenario:String($('scenario').checked)}));$('profile').value=input.profile;$('at').value=new Date(dt.getTime()+8*3600000).toISOString().slice(0,19);markDirty();await run();if(state.dirty)throw new Error('计算失败');return{profile:state.result.input.profile,at:state.result.input.at,pillars:state.result.bazi.calendar.pillars,saved:false};}},{signal:controller.signal});window.addEventListener('pagehide',()=>controller.abort(),{once:true});}
 }catch(e){notify(e.message,true);}}
window.researchTradingView=window.createTradingViewChart({getProfile:()=>$('profile').value});
window.researchCalendarPriceWindow=window.createCalendarPriceWindow();
window.researchTimeCalendar=window.createTimeCalendar({
 getContext:()=>({profile:$('profile').value}),
 selectAt:async(at,symbol)=>{const ok=await window.researchTiming.options.selectAt(at,symbol);if(ok)setTab('bazi');return ok;},
 openChart:async(at,event,gann)=>{const profile=$('profile').value,asset=profile==='BTC-BZ-V01'?'BTC':profile.startsWith('ETH-')?'ETH':null;if(!asset)throw new Error('当前人物盘没有对应的 Coinbase 行情，请先选择 BTC 或 ETH 盘');const date=new Date(new Date(at).getTime()+8*3600000).toISOString().slice(0,10);setTab('chart');window.researchTradingView.showAsset(asset);await window.researchCalendarPriceWindow.show({date,profile,selectedEvent:event,gann});return true;}
});
$('profile').addEventListener('change',()=>window.researchTradingView.syncProfile());
init();

function renderFeatures(){
 const f=state.result.bazi.features,t=f.treasury;
 const cnSlots=items=>items.map(x=>slots[x]||x).join('、')||'无';
 const featureRows=f.features.map(x=>{
  let detail='';
  if(['BZ-S01','BZ-S02','BZ-S06'].includes(x.id))detail=x.evidence.map(e=>e.pattern+'（'+cnSlots(e.slots)+'）').join('；')||'未出现';
  if(['BZ-S03','BZ-S04'].includes(x.id))detail=x.evidence.filter(g=>g.complete).map(g=>g.pattern+'：'+(g.natalAlreadyComplete?'原局已有':!g.crossLayerInstanceExists?'仅流动层齐全':g.flowDayNecessary?'流日参与补齐':'由流年/月补齐，流日非必需')+(g.flowOnlyComplete&&g.crossLayerInstanceExists?'；流动层亦齐全':'')).join('；')||'成员未齐';
  if(x.id==='BZ-S05')detail=t.abstractStorageBranch?t.wealthElement+'对应'+t.abstractStorageBranch+'；原局位置：'+cnSlots(x.evidence):'土财墓库取法有分歧，尚未定义';
  return[x.title,x.value===null?'未定义':x.value?'匹配':'未匹配',detail];
 });
 $('feature-result').innerHTML=`<div class="section-title"><h2>结构规则检查</h2><span class="tag gray">6 项 · v${esc(f.version)}</span></div><p class="muted">${esc(cnTime(f.evaluatedAt))} · 六合、六冲只比较原局与流日；三合、三会采用原局四柱＋流年、流月、流日。</p><p class="muted">流时与大运不计入本组检查。交节日的结构可能日内改变。</p>`+
 table(['检查项','结果','匹配依据'],featureRows)+
 `<div class="callout">匹配表示干支结构出现。合化、开库成败、喜忌与行情方向仍未判定。</div><div class="groups"><h2>财五行与墓库</h2><p class="small">日主${esc(state.result.bazi.relationships.natalDayReference.stem)}所克为${esc(t.wealthElement)}。${t.abstractStorageBranch?'其对应墓库支为'+esc(t.abstractStorageBranch)+'；原局'+(t.natalStoragePresent?'有该支':'没有该支')+'。':'土财墓库尚未统一取法。'}流入的库支位置：${esc(cnSlots(t.flowStoragePositions))}。</p><p class="muted">原局无库时，流入同一库支会另行记录，不能当作原局有库。</p></div>`+
 `<details><summary>藏干与同干显现明细</summary><p class="muted">仅检查原局藏干与明干的同字匹配；同五行但不同天干不算。标记日干自匹配；顺序无强弱权重。</p>`+
 table(['原局位置','藏干 / 十神','原局明干同字','流年月日明干同字'],f.hiddenStems.map(x=>[cnSlots([x.slot])+' '+x.branch,x.stem+' · '+x.tenGod+(x.matchesDayMasterStem?'（与日干同字）':''),cnSlots(x.natalVisibleAt),cnSlots(x.flowVisibleAt)]))+'</details>'+rawDetails(f,'查看规则范围、版本与完整结果');
}

async function renderDefinitions(){
 try{
  const d=await api('/api/definitions');
  const audit=d.profileAudit;
  const profileRows=audit.profiles.map(p=>[p.label,p.pillars,p.status,p.nextEvidence]);
  $('definition-result').innerHTML=`<h2>原局来源核对</h2><p class="muted">已记录四柱继续保留；原作者的排盘设置与本网站研究设置分别存放。</p>`+
  table(['盘版本','已记录四柱','原始设置','待补依据'],profileRows)+
  `<details><summary>查看找到的设置线索</summary>${audit.clues.map(c=>`<article class="rule"><h3>${esc(c.title)}</h3><p>${esc(c.finding)}</p><p>${esc(c.limit)}</p>${c.url?`<a href="${esc(c.url)}" target="_blank" rel="noopener noreferrer">查看出处 ↗</a>`:''}</article>`).join('')}</details>`+
  `<details><summary>日界差异演算 · 不修改原局</summary><p class="muted">同一个官方事件时间，固定北京时间，仅比较 00:00 与 23:00 换日。计算相符只能作为线索。</p>`+
  table(['事件','00:00 日界','23:00 日界','说明'],d.boundaryComparison.map(x=>[x.label,columns.map(k=>x.midnight[k]).join(' '),columns.map(k=>x.zi23[k]).join(' '),x.note]))+'</details>'+
  `<div class="groups"><h2>六冲检验规格与执行状态</h2><p>${esc(d.nextStudy.title)}</p><p class="muted">${esc(d.execution?.status==='exploration_completed'?'已按固定规格完成检验；结果见验证进展。原登记保留。':d.nextStudy.statusLabel)}</p><p class="small">${esc(d.execution?.status==='exploration_completed'?'给定 BTC 四柱条件下的历史探索，不预设涨跌方向。':d.nextStudy.summary)}</p><details><summary>触发条件、窗口与失败标准</summary>${table(['项目','定义'],d.nextStudy.rows)}</details></div>`+
  `<details><summary>六项结构规则的判定与反例</summary>${d.featureDefinitions.map(x=>`<article class="rule"><h3>${esc(x.id+' · '+x.title)}</h3><p>${esc(x.predicate)}</p><p>反例 / 未知：${esc(x.failure)}</p></article>`).join('')}</details>`+
  `<details><summary>基础定义来源</summary>${d.sources.map(s=>`<div class="source"><a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.title)} ↗</a><p class="muted">${esc(s.supports.join('；'))}</p></div>`).join('')}</details><p class="source"><a href="/api/definitions" target="_blank" rel="noopener">查看本版规则定义 JSON</a></p>`;
 }catch(e){$('definition-result').innerHTML=`<div class="callout">定义读取失败：${esc(e.message)}</div>`;}
}

const studyDecisionLabels={
 supported_positive_difference:'高于正向门槛',supported_negative_difference:'低于负向门槛',
 no_material_difference:'在预设门槛内无实质差异',no_material_difference_at_chosen_margin:'在预设门槛内无实质差异',
 unsupported_or_uncertain:'未获支持 / 不确定',uncertain:'未获支持 / 不确定',
 data_insufficient:'数据不足',insufficient_data:'数据不足',uncertainty_unavailable:'区间不可判定'
};
function studyDecision(code){return studyDecisionLabels[code]||code;}
function fixedNumber(value,digits=2){return typeof value==='number'&&Number.isFinite(value)?value.toFixed(digits):'不可用';}
function renderStudyIntervals(periods){
 const valid=periods.filter(p=>p.bootstrap?.available&&Array.isArray(p.bootstrap.ci95Bp));
 if(!valid.length)return '';
 const bound=Math.max(20,...valid.flatMap(p=>p.bootstrap.ci95Bp.map(Math.abs)),...valid.map(p=>Math.abs(p.deltaBp)));
 const extent=Math.ceil(bound/10)*10+10;
 const left=118,right=574,width=right-left,scale=x=>left+(x+extent)/(2*extent)*width;
 const ticks=[-extent,-10,0,10,extent];
 const height=96+periods.length*58;
 const axis=height-35;
 const lines=periods.map((p,i)=>{
  const y=46+i*58,ci=p.bootstrap?.ci95Bp;
  const rowLabel=p.start.slice(0,4)+'–'+p.end.slice(0,4);
  if(!p.bootstrap?.available||!ci)return `<text x="0" y="${y+4}">${esc(rowLabel)} · 区间不可用</text>`;
  return `<g><title>${esc(rowLabel+'：差值 '+fixedNumber(p.deltaBp)+' bp；区间 '+ci.map(x=>fixedNumber(x)).join(' 至 '))}</title><text x="0" y="${y+4}">${esc(rowLabel)}</text><line class="ci-range" x1="${scale(ci[0])}" x2="${scale(ci[1])}" y1="${y}" y2="${y}"/><circle class="ci-dot" cx="${scale(p.deltaBp)}" cy="${y}" r="5"/></g>`;
 }).join('');
 return `<figure class="interval-figure"><svg viewBox="0 0 600 ${height}" role="img" aria-label="两期均值差及95%区间，阴影为事先规定的正负10bp范围"><rect class="ci-band" x="${scale(-10)}" y="16" width="${scale(10)-scale(-10)}" height="${axis-16}"/><line class="ci-zero" x1="${scale(0)}" x2="${scale(0)}" y1="16" y2="${axis}"/>${lines}<line class="ci-axis" x1="${left}" x2="${right}" y1="${axis}" y2="${axis}"/>${ticks.map(t=>`<text class="ci-tick" text-anchor="middle" x="${scale(t)}" y="${axis+20}">${t}</text>`).join('')}</svg><figcaption>点＝均值差；线＝95%区间；浅色范围＝预设 ±10 bp 门槛。</figcaption></figure>`;
}

async function renderStructureStudy(){
 try{
  const r=await api('/api/structure-study');
  if(r.status==='not_run'||!r.periods?.length){$('structure-result').innerHTML='<div class="callout">六冲规格已登记，尚未执行评分。</div>';return;}
  const rows=r.periods.map(p=>[p.start.slice(0,4)+'–'+p.end.slice(0,4)+(p.end.slice(5)!=='12-31'?'*':''),p.groups['1'].n+' / '+p.groups['0'].n,
   fixedNumber(p.groups['1'].meanAbsoluteReturnBp),fixedNumber(p.groups['0'].meanAbsoluteReturnBp),fixedNumber(p.deltaBp)]);
  $('structure-result').innerHTML=`<div class="section-title"><h2>六冲日 · 收开绝对变动检验</h2><span class="tag amber">历史探索</span></div><p class="study-headline">${esc(r.headline)}</p><p class="muted">固定 BTC 四柱版本 01 · Binance BTCUSDT 现货 · 北京时间日界。此报告不随顶部盘版本或分析时刻重算。</p>`+
   renderStudyIntervals(r.periods)+table(['时期','六冲日 / 其他日','六冲日均值 · bp','其他日均值 · bp','差值 · bp'],rows)+
   `<p class="muted">* 第二期截至2026-09-26。结果量为 abs(收盘÷开盘−1)。1 bp＝0.01个百分点；例如100 bp表示1%的收开绝对变动。</p>`+
   r.periods.map(p=>`<article class="rule"><div class="rule-head"><h3>${esc(p.start+' — '+p.end)}</h3><span class="tag gray">${esc(studyDecision(p.decision))}</span></div><p>95%区间：${p.bootstrap?.ci95Bp?p.bootstrap.ci95Bp.map(x=>fixedNumber(x)).join(' 至 ')+' bp':'不可用'}；完整日 ${p.observedDays}/${p.expectedDays}，缺失 ${p.missingDays} 天。</p><p>有效抽样 ${p.bootstrap?.replicatesValid??0}/10000。覆盖率 ${fixedNumber(p.coverage*100)}%；每组至少100个完整日的门槛保持不变。</p></article>`).join('')+
   `<div class="callout">这是已看过历史资料后的探索性检验。变动幅度差异不表示涨跌方向；当前原盘设置仍未核定，也没有产生交易胜率。</div>`+
   `<details><summary>怎么判断支持、无实质差异或不确定</summary><p class="small">95%区间整体高于+10bp或低于−10bp，才达到预设的差异门槛；整体位于±10bp内，记作该门槛下无实质差异；其余情况为未获支持或不确定。每期覆盖至少95%，每组至少100完整日，有效抽样至少9900次。</p><p class="muted">六冲日是给定原局子、子、申、酉与流日相冲；等价于流日支午、寅、卯。同一天多条冲边仍只记一次。没有比较其他盘或扫描参数。</p></details>`+
   rawDetails(r,'查看完整结果、冻结依据与限制')+
   `<div class="study-actions"><a id="export-structure-study" class="quiet" href="/downloads/structure-study.json" download="H-BZ-STRUCT-002-results.json">导出本次检验 JSON ↓</a><a href="/api/structure-study" target="_blank" rel="noopener">打开结果 JSON</a></div>`;
  $('study-note').textContent=r.headline+' 点“验证进展”查看完整依据。';
 }catch(e){$('structure-result').innerHTML=`<div class="callout">六冲检验读取失败：${esc(e.message)}</div>`;}
}

async function renderCalendarAccuracy(){
 const host=$('calendar-accuracy-result');if(!host)return;
 try{const d=await api('/api/calendar-accuracy'),s=d.summary||{};
  const categories=[['用户截图完整四柱',s.userScreenshotFullCharts],['独立公式四柱对照',s.formulaFourPillarChecks],['日界与交节规则检查',s.boundaryContractChecks],['官方资料日柱示例',s.officialDayExamples]];
  host.innerHTML=`<div class="section-title"><h2>排盘对照</h2><span class="tag ${d.status==='passed'?'gray':'amber'}">${d.status==='passed'?'已执行对照':d.status==='failed'?'存在不一致':'对照暂不可用'}</span></div><p class="accuracy-headline">${esc(s.headline||'用户问真截图与独立历法公式分别对照。')}</p><p class="muted">${esc(s.interpretation||'不同来源和核验范围分别统计，不能合并当作官方完整排盘样本。')}</p><div class="accuracy-counts">${categories.map(([label,value])=>`<article><span>${esc(label)}</span><strong>${value?.passed??0} / ${value?.total??0}</strong><small>一致 / 已检查${value?.failed?' · 不一致 '+value.failed:''}</small></article>`).join('')}</div><p class="muted">官方完整四柱样本 ${s.officialFullFourPillarSamples??0} 例；秒级节气交接独立核验 ${s.secondPrecisionSolarTermsVerified??0} 项。既有 ${d.oldValidation?.executedChecks??107} 项程序检查${d.oldValidation?.unchanged?'保持原样':''}，与本次外部对照分开计数。</p><details><summary>查看样例、排盘设置与证据范围</summary>${table(['样例 / 时刻','参考四柱','本地四柱','结果'],(d.examples||[]).map(x=>[x.label+' / '+cnTime(x.at),columns.map(k=>x.expected?.[k]||'—').join(' '),columns.map(k=>x.actual?.[k]||'—').join(' '),x.passed?'一致':'待核查']))}<p class="muted">北京时间 UTC+8 · 00:00 换日 · 晚子时以次日日干起时干 · 不使用真太阳时。四柱一致不表示 BTC／ETH 原始出生时地已经确定。</p><ul>${(d.limitations||[]).map(x=>`<li>${esc(x)}</li>`).join('')}</ul>${(d.sources||[]).map(x=>`<article class="source"><strong>${esc(x.title)}</strong>${/^https?:\/\//.test(x.url||'')?` · <a href="${esc(x.url)}" target="_blank" rel="noopener noreferrer">来源 ↗</a>`:''}<p class="muted">${esc(x.note||'')}</p></article>`).join('')}<p class="small"><a href="/api/calendar-accuracy" target="_blank" rel="noopener">查看完整对照报告 JSON</a> · v${esc(d.validationVersion)}</p></details>`;
 }catch(error){host.innerHTML=`<h2>排盘对照</h2><p class="callout">对照报告暂不可用：${esc(error.message)}</p>`;}
}
