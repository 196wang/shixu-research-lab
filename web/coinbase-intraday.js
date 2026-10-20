'use strict';
/* Read-only hourly path and day-boundary comparison; not a new forecast. */
(() => {
 const E=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const pct=v=>Number.isFinite(v)?(v>=0?'+':'')+v.toFixed(2)+'%':'—';
 class Intraday {
  constructor(host){this.host=host;this.symbol='BTCUSD';this.loaded=false;
   host.innerHTML='<details class="cb-intraday"><summary>查看日内波动与 23:00 换日对照</summary><p class="muted">按小时线比较 00:00 和 23:00 两种北京时间日界。此处是历史描述，不修改已登记的方向规则或评分。</p><div class="tv-chart-toolbar"><label>日期<input type="date" class="cb-intraday-date" value="2026-10-16" min="2022-01-02"></label><button type="button" class="secondary cb-intraday-run">查看该日</button></div><p class="cb-intraday-status muted" role="status">展开后读取本机小时线。</p><div class="cb-intraday-body"></div></details>';
   host.querySelector('details').addEventListener('toggle',()=>{if(host.querySelector('details').open&&!this.loaded)this.load();});
   host.querySelector('button').addEventListener('click',()=>this.load());
  }
  setSymbol(symbol){if(this.symbol===symbol)return;this.symbol=symbol;this.loaded=false;if(this.host.querySelector('details').open)this.load();}
  async load(){const date=this.host.querySelector('input').value,status=this.host.querySelector('.cb-intraday-status');if(!date)return;
   status.textContent='正在读取本机小时线…';
   try{const response=await fetch('/api/coinbase-intraday?'+new URLSearchParams({symbol:this.symbol,date}),{cache:'no-store'}),data=await response.json();
    if(!response.ok)throw Error(data.error||'日内数据读取失败');this.loaded=true;status.textContent=`${this.symbol} · ${date} · 已读取 ${data.hours.filter(h=>h.open!==null).length} / 25 个相关小时；来源为 Coinbase 本机缓存。`;
    const card=(item,title)=>`<div class="cb-intraday-card"><h4>${E(title)}</h4><p>${E(item.startAt.slice(0,16).replace('T',' '))} → ${E(item.endAt.slice(0,16).replace('T',' '))}</p>${item.complete?`<p>开 ${E(item.open)} · 高 ${E(item.high)} · 低 ${E(item.low)} · 收 ${E(item.close)}</p><p>收盘变化 <strong>${pct(item.closeReturnPct)}</strong> · 日内高低幅 <strong>${pct(item.rangePct)}</strong></p><p>从开盘向上 ${pct(item.upExcursionPct)} · 向下 ${pct(item.downExcursionPct)}</p><p>最高小时 ${E(item.highHours.join('、'))}；最低小时 ${E(item.lowHours.join('、'))}</p>`:`<p>只有 ${E(item.completeHours)} / 24 小时，缺口不计算完整日内指标。</p>`}</div>`;
    const rows=data.hours.map(hour=>`<tr><th scope="row">${E(hour.localHour)}</th><td>${hour.inZiSession?'●':'—'}</td><td>${hour.inMidnightSession?'●':'—'}</td><td>${E(hour.open??'缺')}</td><td>${E(hour.high??'—')}</td><td>${E(hour.low??'—')}</td><td>${E(hour.close??'—')}</td></tr>`).join('');
    this.host.querySelector('.cb-intraday-body').innerHTML=`<p class="muted">${E(data.date)} 的日柱：${E(data.dayPillar)}。前一晚 23:30 按现行口径为 ${E(data.lateZiCurrentPillar)}，按 23:00 换日假设为 ${E(data.lateZiAlternativePillar)}；下方行情区间也相应平移一小时。</p><div class="cb-intraday-cards">${card(data.midnight,'00:00 日界 · 现行口径')}${card(data.ziHour,'23:00 日界 · 并列假设')}</div><p class="muted">日K涨跌只看开收盘；下表可以看到小时级的高低波动。小时 OHLC 无法判定同一小时内先到高点还是低点。</p><div class="table-wrap cb-intraday-scroll"><table class="data-table cb-intraday-table"><thead><tr><th>北京时间</th><th>23点日界</th><th>0点日界</th><th>开</th><th>高</th><th>低</th><th>收</th></tr></thead><tbody>${rows}</tbody></table></div>`;
   }catch(error){status.textContent='暂不能展示：'+error.message;this.host.querySelector('.cb-intraday-body').innerHTML='';}}
 }
 window.createCoinbaseIntraday=host=>new Intraday(host);
})();
