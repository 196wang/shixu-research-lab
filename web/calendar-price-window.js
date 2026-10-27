'use strict';
/* Calendar-linked Coinbase cache view. Never invent prices for empty days. */
(() => {
 const E=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const LABEL={term:'节气',astro:'星象',gann:'江恩',prediction:'已登记'},COLOR={term:'#b78620',astro:'#7962a7',gann:'#287a60',prediction:'#2869aa'};
 const local=v=>String(v||'').slice(0,19).replace('T',' ');
 const format=v=>Number.isFinite(Number(v))?Number(v).toLocaleString('en-US',{maximumFractionDigits:2}):'—';
 async function get(url){const r=await fetch(url,{cache:'no-store'}),data=await r.json();if(!r.ok)throw Error(data.error||'读取失败');return data;}
 class CalendarPriceWindow {
  constructor(){this.host=document.getElementById('calendar-price-window');this.ticket=0;this.host.innerHTML='<section class="tv-chart-card calendar-price-card"><div class="section-title"><h2>日历事件 · 前后 3 日 K 线</h2><span class="tag gray">Coinbase 缓存 · 北京时间</span></div><p id="calendar-price-status" class="muted" role="status"></p><div id="calendar-price-body"></div></section>';}
  markers(rows,calendars,astro,ledger,gann){const result=new Map(rows.map(row=>[row.date,[]])),add=(date,kind,label,at)=>{if(result.has(date))result.get(date).push({kind,label,at});};
   for(const month of calendars)for(const day of month?.days||[])for(const term of day.termTransitions||[])add(day.date,'term',term.termName+(term.changesMonth?'交节':'中气'),term.at);
   for(const event of astro?.events||[])add(String(event.atLocal).slice(0,10),'astro',event.label,event.atLocal);
   for(const record of ledger?.records||[])add(record.targetDate,'prediction',record.title,record.createdAtUtc);
   for(const item of gann||[])add(item.targetDate,'gann',`江恩 ${item.multiplier} 倍时间候选`,item.targetAtLocal);
   return result;
  }
  async show({date,profile,selectedEvent,gann=[]}){
   const asset=profile?.startsWith('ETH-')?'ETH':profile==='BTC-BZ-V01'?'BTC':null;if(!asset)throw Error('人物盘没有对应的 Coinbase 行情资产');
   const ticket=++this.ticket,symbol=asset+'USD';this.host.hidden=false;this.host.querySelector('#calendar-price-status').textContent=`正在读取 ${symbol} · ${date} 前后 3 日的本机缓存…`;this.host.querySelector('#calendar-price-body').innerHTML='';
   const price=await get('/api/calendar-price-window?'+new URLSearchParams({symbol,date}));if(ticket!==this.ticket)return;
   const months=[...new Set([price.range.start.slice(0,7),price.range.end.slice(0,7)])];
   const optional=await Promise.allSettled([get('/api/astro-events?'+new URLSearchParams({start:price.range.start,end:price.range.end})),get('/api/predictions'),...months.map(month=>get('/api/calendar-browser?'+new URLSearchParams({at:month+'-01T12:00:00+08:00'})))]);if(ticket!==this.ticket)return;
   const astro=optional[0].status==='fulfilled'?optional[0].value:null,ledger=optional[1].status==='fulfilled'?optional[1].value:null,calendars=optional.slice(2).filter(x=>x.status==='fulfilled').map(x=>x.value);
   const warnings=[];if(!astro)warnings.push('星象标记暂不可用');if(!ledger)warnings.push('已登记判断暂不可用');if(calendars.length!==months.length)warnings.push('部分节气标记暂不可用');
   this.host.querySelector('#calendar-price-status').textContent=`${symbol} · ${date} 前后 3 日 · ${price.rows.filter(x=>x.status==='complete').length}/7 根完整日线${warnings.length?' · '+warnings.join('、'):''}`;
   this.render(price,this.markers(price.rows,calendars,astro,ledger,gann),selectedEvent);
   this.host.scrollIntoView({block:'start',behavior:'smooth'});
  }
  render(price,markers,selectedEvent){const rows=price.rows,valid=rows.filter(r=>r.status==='complete'&&[r.open,r.high,r.low,r.close].every(x=>Number.isFinite(Number(x)))),low=valid.length?Math.min(...valid.map(x=>Number(x.low))):0,high=valid.length?Math.max(...valid.map(x=>Number(x.high))):1,pad=Math.max((high-low)*.08,high*.003,1),min=Math.max(0,low-pad),max=high+pad,scale=n=>185-(Number(n)-min)/(max-min)*158;
   const svg=['<svg viewBox="0 0 760 268" role="img" aria-label="Coinbase 缓存的七日 K 线与时间事件标记">','<rect x="0" y="0" width="760" height="268" fill="#fff"/>'];
   for(let i=0;i<3;i++){const y=27+i*79,value=max-(max-min)*i/2;svg.push(`<line x1="55" x2="732" y1="${y}" y2="${y}" stroke="#e5ebe6"/><text x="3" y="${y+4}" font-size="11" fill="#65746e">${E(Math.round(value).toLocaleString('en-US'))}</text>`);}
   rows.forEach((row,i)=>{const x=55+i*96.5,center=x+48.25,items=markers.get(row.date)||[],mark=row.date===price.centerDate;svg.push(`<rect x="${x}" y="22" width="96.5" height="169" fill="${mark?'#f6f2e8':i%2?'#fafbf9':'#fff'}"/>`);
    if(row.status==='complete'){const up=Number(row.close)>=Number(row.open),color=up?'#23816a':'#ca4e46',bodyTop=scale(Math.max(Number(row.open),Number(row.close))),bodyHeight=Math.max(2,Math.abs(scale(row.open)-scale(row.close)));svg.push(`<line x1="${center}" x2="${center}" y1="${scale(row.high)}" y2="${scale(row.low)}" stroke="${color}" stroke-width="2"/><rect x="${center-10}" y="${bodyTop}" width="20" height="${bodyHeight}" fill="${color}"/><title>${E(row.date)} 开 ${E(format(row.open))} 高 ${E(format(row.high))} 低 ${E(format(row.low))} 收 ${E(format(row.close))}</title>`);}else svg.push(`<text x="${center}" y="106" text-anchor="middle" font-size="10" fill="#7d8983">${row.status==='future'?'未来 · 无行情':'缓存缺口'}</text>`);
    svg.push(`<text x="${center}" y="207" text-anchor="middle" font-size="11" fill="#40574e">${E(row.date.slice(5))}</text>`);const kinds=[...new Set(items.map(x=>x.kind))];kinds.forEach((kind,n)=>{const bx=center-(kinds.length-1)*12+n*24;svg.push(`<circle cx="${bx}" cy="225" r="9" fill="${COLOR[kind]}"/><text x="${bx}" y="229" text-anchor="middle" font-size="10" fill="#fff">${({term:'节',astro:'星',gann:'江',prediction:'预'})[kind]}</text>`);});
   });svg.push('</svg>');
   const event=selectedEvent?`选择的事件：${E(selectedEvent.label)} · ${E(local(selectedEvent.at))}（北京）`:`选择日期：${E(price.centerDate)}`;
   const body=this.host.querySelector('#calendar-price-body');body.innerHTML=`<p class="calendar-price-selected"><strong>${event}</strong></p><div class="calendar-price-legend">${Object.keys(LABEL).map(kind=>`<span><i style="background:${COLOR[kind]}"></i>${LABEL[kind]}</span>`).join('')}</div><div class="calendar-price-svg">${svg.join('')}</div><div class="table-wrap"><table class="data-table calendar-price-table"><thead><tr><th>北京日期</th><th>收开变动</th><th>时间事件</th><th>行情状态</th></tr></thead><tbody>${rows.map(row=>{const items=markers.get(row.date)||[],change=row.status==='complete'?(Number(row.close)/Number(row.open)-1)*100:null,pct=change===null?null:change.toFixed(3);return `<tr${row.date===price.centerDate?' class="calendar-price-center"':''}><th>${E(row.date)}</th><td>${pct===null?'—':`${change>0?'+':''}${pct}%`}</td><td>${items.map(item=>`<span class="calendar-price-event is-${E(item.kind)}" title="${E(local(item.at))}">${E(LABEL[item.kind])} · ${E(item.label)}</span>`).join('')||'—'}</td><td>${row.status==='complete'?'完整日线':row.status==='future'?'未来／未收盘':'缓存缺口'}</td></tr>`;}).join('')}</tbody></table></div><p class="muted calendar-price-note">图表只读本机 Coinbase Exchange 的 BTC/USD 或 ETH/USD 完整日线，缺口与未来日留空。彩色圆点仅标时间来源；江恩点来自刚才日历已计算的候选，跨月可能未载入。TradingView 免费嵌入图不能接收本站标记或自动定位到此日。</p>`;
  }
 }
 window.createCalendarPriceWindow=()=>new CalendarPriceWindow();
})();
