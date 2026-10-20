'use strict';
/* TradingView's free iframe widget is display-only; it does not supply bars to the research ledger. */
(() => {
 const SYMBOLS={BTC:'COINBASE:BTCUSD',ETH:'COINBASE:ETHUSD'};
 const PERIODS=[['240','4 小时'],['D','日线'],['W','周线'],['M','月线']];
 class TradingViewChart {
  constructor(options){
   this.options=options;this.root=document.getElementById('tradingview-chart');this.visible=false;this.loaded=false;
   this.asset='BTC';this.interval='D';this.height=640;
   this.root.innerHTML=`<section class="tv-chart-card" aria-label="TradingView 行情图"><div class="section-title"><h2>TradingView K 线</h2><span class="tag gray">Coinbase · USD 现货</span></div><div class="tv-chart-toolbar"><label>资产<select id="tv-asset"><option value="BTC">BTC / USD</option><option value="ETH">ETH / USD</option></select></label><div class="tv-chart-periods" role="group" aria-label="TradingView K 线周期">${PERIODS.map(([value,label])=>`<button type="button" class="quiet" data-tv-period="${value}" aria-pressed="${value==='D'}">${label}</button>`).join('')}</div><label>图表高度<select id="tv-height"><option value="480">480 px</option><option value="640" selected>640 px</option><option value="800">800 px</option><option value="960">960 px</option></select></label><a id="tv-external" class="quiet tv-external" target="_blank" rel="noopener noreferrer" href="https://www.tradingview.com/symbols/BTCUSD/?exchange=COINBASE">在 TradingView 打开 ↗</a></div><p class="muted tv-chart-note">图中行情由 TradingView 嵌入组件提供，市场选用 Coinbase 的 USD 现货。可在图中缩放和调整纵轴；本站不会请求币安行情。下方研究时间带仍是旧样本，不能与图内报价混作同一组回测数据。</p><p id="tv-selection-note" class="muted" role="status"></p><div id="tv-widget-shell" class="tv-widget-shell"><div id="tv-widget" class="tv-widget" aria-label="TradingView K 线嵌入图"></div></div><p id="tv-status" class="muted" role="status">打开“K 线与时间”后加载 TradingView 图表。若图表被网络阻止，可使用右上角链接直接打开。</p></section>`;
   this.root.querySelector('#tv-asset').addEventListener('change',e=>{this.asset=e.target.value==='ETH'?'ETH':'BTC';this.mount();});
   this.root.querySelector('#tv-height').addEventListener('change',e=>{this.height=Number(e.target.value)||640;this.root.querySelector('#tv-widget-shell').style.height=this.height+'px';});
   this.root.querySelectorAll('[data-tv-period]').forEach(button=>button.addEventListener('click',()=>{this.interval=button.dataset.tvPeriod;this.updatePeriods();this.mount();}));
   this.root.querySelector('#tv-widget-shell').style.height=this.height+'px';
  }
  profileAsset(){return this.options.getProfile().startsWith('ETH-')?'ETH':'BTC';}
  updatePeriods(){this.root.querySelectorAll('[data-tv-period]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.tvPeriod===this.interval)));}
  syncProfile(){const asset=this.profileAsset();if(asset!==this.asset){this.asset=asset;this.root.querySelector('#tv-asset').value=asset;if(this.visible)this.mount();}}
  showAsset(asset,record){this.asset=asset==='ETH'?'ETH':'BTC';this.root.querySelector('#tv-asset').value=this.asset;this.root.querySelector('#tv-selection-note').textContent=record?`已选择预测账本 ${record.symbol} · ${record.targetDate||'目标日期未设置'}。免费嵌入图无法自动定位或标记该日期，请在图内手动查看；评分以本站保存的 Coinbase 完整日线为准。`:'';if(this.visible)this.mount();}
  setVisible(visible){this.visible=visible;if(!visible)return;this.syncProfile();if(!this.loaded)this.mount();}
  mount(){
   if(!this.visible)return;
   const symbol=SYMBOLS[this.asset],host=this.root.querySelector('#tv-widget');host.replaceChildren();
   this.root.querySelector('#tv-external').href=`https://www.tradingview.com/symbols/${this.asset}USD/?exchange=COINBASE`;
   this.root.querySelector('#tv-status').textContent=`正在加载 ${this.asset}/USD · ${PERIODS.find(p=>p[0]===this.interval)?.[1]} 的 TradingView 图表…`;
   const container=document.createElement('div');container.className='tradingview-widget-container';
   const widget=document.createElement('div');widget.className='tradingview-widget-container__widget';container.appendChild(widget);
   const credit=document.createElement('div');credit.className='tradingview-widget-copyright';
   const link=document.createElement('a');link.href=this.root.querySelector('#tv-external').href;link.target='_blank';link.rel='noopener noreferrer';link.textContent=`${this.asset}/USD chart by TradingView`;credit.appendChild(link);container.appendChild(credit);
   host.appendChild(container);
   const script=document.createElement('script');script.src='https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js';script.async=true;
   script.textContent=JSON.stringify({autosize:true,symbol,interval:this.interval,timezone:'Asia/Shanghai',theme:'light',style:'1',locale:'zh_CN',allow_symbol_change:false,hide_top_toolbar:false,hide_side_toolbar:false,withdateranges:true,calendar:false,details:false,hotlist:false,save_image:true,backgroundColor:'#ffffff',gridColor:'rgba(36,66,76,0.10)',support_host:'https://www.tradingview.com'});
   script.onerror=()=>{this.root.querySelector('#tv-status').textContent='TradingView 组件未能加载；请检查网络，或点“在 TradingView 打开”。';};
   script.onload=()=>{this.root.querySelector('#tv-status').textContent=`${this.asset}/USD 图表已请求；若仍空白，请检查浏览器是否拦截 TradingView。`;};
   container.appendChild(script);this.loaded=true;
  }
 }
 window.createTradingViewChart=options=>new TradingViewChart(options);
})();
