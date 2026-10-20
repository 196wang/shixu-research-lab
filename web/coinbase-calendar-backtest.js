'use strict';
/* Daily and calendar-month summaries from the sealed Coinbase retrospective. */
(() => {
 const E=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const names={'DAY-STEM-METALWATER-UP':'金水日涨','DAY-STEM-FIRE-DOWN':'火日跌','DAY-BRANCH-YIN-DOWN':'寅日跌'};
 const actual={up:'上涨',down:'下跌',flat:'平盘'};
 const status={boundary_excluded:'交节剔除',missing:'缺行情',calendar_unknown:'排盘未知'};
 const outcome={correct:'符合',incorrect:'不符',flat:'平盘'};
 const dayList=dates=>dates.length?dates.map(date=>Number(date.slice(-2))+'日').join('、'):'—';
 const fmtReturn=value=>Number.isFinite(value)?(value>0?'+':'')+value.toFixed(2)+'%':'—';
 class CoinbaseCalendarBacktest {
  constructor(host){this.host=host;this.symbol='BTCUSD';this.year=2026;this.month=null;this.visible=false;this.loaded=new Map();this.ticket=0;
   host.innerHTML=`<section class="cb-calendar"><div class="section-title"><h3>历史日线与自然月统计</h3><span class="tag gray">Coinbase 固定事后复盘</span></div><p class="muted">按北京时间自然月汇总每日开盘至收盘的上涨、下跌次数，并列出具体几号。以下是历史描述，不是提前登记的预测；TradingView 的月K线可在“K 线与时间”切换查看。</p><div class="tv-chart-toolbar"><label>年份<select id="cb-calendar-year"><option value="2023">2023</option><option value="2024">2024</option><option value="2025">2025</option><option value="2026" selected>2026</option></select></label><label>查看每日<select id="cb-calendar-month"></select></label></div><p id="cb-calendar-status" class="muted" role="status">正在读取历史明细…</p><div id="cb-calendar-body"></div></section>`;
   host.querySelector('#cb-calendar-year').addEventListener('change',event=>{this.year=Number(event.target.value);this.month=null;this.load();});
   host.querySelector('#cb-calendar-month').addEventListener('change',event=>{this.month=event.target.value;this.render();});
  }
  setVisible(visible){this.visible=visible;if(visible)this.load();}
  setSymbol(symbol){if(symbol===this.symbol)return;this.symbol=symbol;if(this.visible)this.load();}
  async load(){if(!this.visible)return;const key=this.symbol+'-'+this.year,ticket=++this.ticket;
   if(this.loaded.has(key)){this.render();return;}
   this.host.querySelector('#cb-calendar-status').textContent='正在读取已校验的 '+this.symbol+' '+this.year+' 年逐日记录…';
   try{const response=await fetch('/api/coinbase-study-calendar?'+new URLSearchParams({symbol:this.symbol,year:String(this.year)}),{cache:'no-store'});
    const data=await response.json();if(!response.ok)throw Error(data.error||'读取失败');if(ticket!==this.ticket)return;
    if(data.studyId!=='CB-USD-HX-001'||!Array.isArray(data.months)||!Array.isArray(data.daily))throw Error('历史明细结构不符');
    this.loaded.set(key,data);this.render();
   }catch(error){if(ticket===this.ticket){this.host.querySelector('#cb-calendar-status').textContent='历史明细读取失败：'+error.message;this.host.querySelector('#cb-calendar-body').innerHTML='';}}
  }
  render(){const data=this.loaded.get(this.symbol+'-'+this.year);if(!data)return;
   if(!data.months.some(item=>item.month===this.month))this.month=data.months.at(-1)?.month||null;
   const monthSelect=this.host.querySelector('#cb-calendar-month');monthSelect.innerHTML=data.months.map(item=>`<option value="${E(item.month)}" ${item.month===this.month?'selected':''}>${E(item.month)}</option>`).join('');
   const selected=data.months.find(item=>item.month===this.month);
   this.host.querySelector('#cb-calendar-status').textContent=`${this.symbol} · 固定区间 ${data.range.start} 至 ${data.range.end} · ${this.year===2026?'2026 年只含截至区间末日的已知历史。':'全年逐日记录。'}交节剔除和缺行情另列。`;
   const monthRows=data.months.map(item=>`<tr class="${item.month===this.month?'cb-calendar-selected':''}"><th scope="row"><button type="button" class="quiet cb-calendar-pick" data-month="${E(item.month)}">${E(item.month)} ↗</button></th><td class="cb-calendar-up">${E(item.up)} 日</td><td class="cb-calendar-down">${E(item.down)} 日</td><td>${E(item.flat)} 日</td><td>${E(item.excluded)} / ${E(item.missing)}</td><td>${E(dayList(item.upDates))}</td><td>${E(dayList(item.downDates))}</td></tr>`).join('');
   const daily=data.daily.filter(row=>row.date.startsWith(this.month));
   const dailyRows=daily.map(row=>`<tr><th scope="row">${E(row.date)}</th><td>${E(row.dayPillar||'—')} / ${E(row.monthPillar||'—')}</td><td>${row.status==='eligible'?`<span class="cb-calendar-${E(row.actual)}">${E(actual[row.actual]||'未知')}</span>`:E(status[row.status]||row.status)}</td><td>${fmtReturn(row.returnPct)}</td><td>${row.open!=null?E(row.open)+' → '+E(row.close):'—'}</td><td>${row.ruleIds.length?row.ruleIds.map(id=>`${E(names[id]||id)}：${E(outcome[row.ruleOutcomes[id]]||'—')}`).join('；'):'—'}</td></tr>`).join('');
   const ruleSummary=selected?Object.entries(selected.ruleCounts).map(([id,counts])=>`${E(names[id]||id)} ${E(counts.correct)}/${E(counts.triggered)}`).join(' · '):'';
   this.host.querySelector('#cb-calendar-body').innerHTML=`<h4>逐月涨跌天数和日期</h4><div class="table-wrap cb-calendar-scroll"><table class="data-table cb-calendar-month-table"><thead><tr><th>自然月</th><th>上涨</th><th>下跌</th><th>平盘</th><th>交节 / 缺口</th><th>上涨日期</th><th>下跌日期</th></tr></thead><tbody>${monthRows}</tbody></table></div><h4>${E(this.month)} 每日结果</h4><p class="muted">${selected?`可评 ${E(selected.eligible)} 日：涨 ${E(selected.up)}、跌 ${E(selected.down)}、平 ${E(selected.flat)}；交节剔除 ${E(selected.excluded)}、缺行情 ${E(selected.missing)}。规则符合次数：${ruleSummary}。同一天多条规则分别计数，反向冲突不合并成一个预测。`:''}</p><div class="table-wrap cb-calendar-scroll"><table class="data-table cb-calendar-day-table"><thead><tr><th>北京日期</th><th>日 / 月柱</th><th>实际</th><th>当日涨跌</th><th>开 → 收 USD</th><th>触发规则及结果</th></tr></thead><tbody>${dailyRows}</tbody></table></div><p class="muted">仅“可评”日计入涨跌次数和规则结果。交节日即使有行情，也因固定研究协议排除在这里的规则统计之外；缺口不能算跌。月份是公历自然月分组，八字月柱仍按节气切换。</p>`;
   this.host.querySelectorAll('.cb-calendar-pick').forEach(button=>button.addEventListener('click',()=>{this.month=button.dataset.month;this.render();}));
  }
 }
 window.createCoinbaseCalendarBacktest=host=>new CoinbaseCalendarBacktest(host);
})();
