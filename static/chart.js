function drawChart(canvas, points, field, color, unit='W') {
  const ctx=canvas.getContext('2d'), w=canvas.width=canvas.clientWidth*devicePixelRatio, h=canvas.height=canvas.clientHeight*devicePixelRatio;
  ctx.scale(devicePixelRatio,devicePixelRatio); const W=canvas.clientWidth,H=canvas.clientHeight,p=32,bottom=28;
  const values=points.map(x=>x[field]).filter(x=>Number.isFinite(x)); if(!values.length){ctx.fillStyle='#91a0ae';ctx.font='14px system-ui';ctx.fillText('No measurements for this period.',p,H/2);return}
  const chartHeight=H-p-bottom, min=unit==='V'?Math.min(...values)*.995:Math.min(0,...values),max=Math.max(...values)*1.005||1;
  ctx.strokeStyle='#283442';ctx.lineWidth=1;for(let i=0;i<4;i++){let y=p+i*chartHeight/3;ctx.beginPath();ctx.moveTo(p,y);ctx.lineTo(W-p,y);ctx.stroke();ctx.fillStyle='#91a0ae';ctx.font='11px system-ui';ctx.fillText((max-(max-min)*i/3).toFixed(unit==='V'?1:0)+' '+unit,2,y+4)}
  const labelDate=(raw)=>{const date=new Date(raw), span=new Date(points.at(-1).observed_at)-new Date(points[0].observed_at);return span<=172800000?date.toLocaleTimeString([], {hour:'numeric',minute:'2-digit'}):span<=3456000000?date.toLocaleDateString([], {month:'short',day:'numeric'}):date.toLocaleDateString([], {month:'short',year:'2-digit'})};
  ctx.textAlign='center';for(let i=0;i<5;i++){const index=Math.round(i*(points.length-1)/4),X=p+i*(W-2*p)/4;ctx.strokeStyle='#283442';ctx.beginPath();ctx.moveTo(X,H-bottom);ctx.lineTo(X,H-bottom+4);ctx.stroke();ctx.fillStyle='#91a0ae';ctx.font='11px system-ui';ctx.fillText(labelDate(points[index].observed_at),X,H-6)}ctx.textAlign='start';
  ctx.beginPath();let started=false;points.forEach((x,i)=>{if(!Number.isFinite(x[field]))return;let X=p+i*(W-2*p)/Math.max(1,points.length-1),Y=H-bottom-(x[field]-min)/(max-min)*chartHeight;if(started)ctx.lineTo(X,Y);else{ctx.moveTo(X,Y);started=true}});ctx.strokeStyle=color;ctx.lineWidth=2;ctx.stroke();
}
function drawSupplyVoltageChart(canvas, points) {
  const ctx=canvas.getContext('2d'),w=canvas.width=canvas.clientWidth*devicePixelRatio,h=canvas.height=canvas.clientHeight*devicePixelRatio;
  ctx.scale(devicePixelRatio,devicePixelRatio);const W=canvas.clientWidth,H=canvas.clientHeight,p=32,bottom=28;
  const usable=points.filter(point=>Number.isFinite(point.voltage)||point.inferred_outage),values=usable.flatMap(point=>point.inferred_outage?[0]:[point.voltage]).filter(Number.isFinite);
  if(!values.length){ctx.fillStyle='#91a0ae';ctx.font='14px system-ui';ctx.fillText('No supply measurements for this period.',p,H/2);return}
  const times=usable.map(point=>new Date(point.observed_at).getTime()),start=Math.min(...times),end=Math.max(...times),duration=Math.max(1,end-start),chartHeight=H-p-bottom;
  const hasOutage=usable.some(point=>point.inferred_outage),min=hasOutage?0:Math.min(...values)*.995,max=Math.max(...values)*1.005||1;
  const xFor=time=>p+(time-start)/duration*(W-2*p),yFor=value=>H-bottom-(value-min)/(max-min)*chartHeight;
  const labelDate=time=>{const date=new Date(time);return duration<=172800000?date.toLocaleTimeString([],{hour:'numeric',minute:'2-digit'}):duration<=3456000000?date.toLocaleDateString([],{month:'short',day:'numeric'}):date.toLocaleDateString([],{month:'short',year:'2-digit'})};
  ctx.strokeStyle='#283442';ctx.lineWidth=1;for(let i=0;i<4;i++){const y=p+i*chartHeight/3;ctx.beginPath();ctx.moveTo(p,y);ctx.lineTo(W-p,y);ctx.stroke();ctx.fillStyle='#91a0ae';ctx.font='11px system-ui';ctx.fillText((max-(max-min)*i/3).toFixed(1)+' V',2,y+4)}
  ctx.textAlign='center';for(let i=0;i<5;i++){const X=p+i*(W-2*p)/4,time=start+i*duration/4;ctx.strokeStyle='#283442';ctx.beginPath();ctx.moveTo(X,H-bottom);ctx.lineTo(X,H-bottom+4);ctx.stroke();ctx.fillStyle='#91a0ae';ctx.font='11px system-ui';ctx.fillText(labelDate(time),X,H-6)}ctx.textAlign='start';
  const sorted=[...points].sort((a,b)=>new Date(a.observed_at)-new Date(b.observed_at)),gaps=[];for(let i=1;i<sorted.length;i++){const gap=new Date(sorted[i].observed_at)-new Date(sorted[i-1].observed_at);if(gap>0)gaps.push(gap)}const typical=gaps.sort((a,b)=>a-b)[Math.floor(gaps.length/2)]||60000,breakAfter=Math.max(90000,typical*2.5);
  ctx.beginPath();let previousTime=null;for(const point of sorted){if(!Number.isFinite(point.voltage))continue;const time=new Date(point.observed_at).getTime(),X=xFor(time),Y=yFor(point.voltage);if(previousTime===null||time-previousTime>breakAfter)ctx.moveTo(X,Y);else ctx.lineTo(X,Y);previousTime=time}ctx.strokeStyle='#91a7ff';ctx.lineWidth=2;ctx.stroke();
  ctx.fillStyle='#ff7b7b';for(const point of sorted){if(!point.inferred_outage)continue;const X=xFor(new Date(point.observed_at).getTime()),Y=yFor(0);ctx.beginPath();ctx.arc(X,Y,4,0,Math.PI*2);ctx.fill()}
}
function drawComparisonChart(canvas, datasets) {
  const points=datasets.flatMap(dataset=>dataset.points), values=points.map(point=>point.watts).filter(Number.isFinite);
  const ctx=canvas.getContext('2d'), w=canvas.width=canvas.clientWidth*devicePixelRatio, h=canvas.height=canvas.clientHeight*devicePixelRatio;
  ctx.scale(devicePixelRatio,devicePixelRatio);const W=canvas.clientWidth,H=canvas.clientHeight,p=32,bottom=28;
  if(!values.length){ctx.fillStyle='#91a0ae';ctx.font='14px system-ui';ctx.fillText('No device measurements for this period.',p,H/2);return}
  const times=points.map(point=>new Date(point.observed_at).getTime()),start=Math.min(...times),end=Math.max(...times),duration=Math.max(1,end-start),chartHeight=H-p-bottom,min=0,max=Math.max(...values)*1.05||1;
  const labelDate=(time)=>{const date=new Date(time);return duration<=172800000?date.toLocaleTimeString([],{hour:'numeric',minute:'2-digit'}):duration<=3456000000?date.toLocaleDateString([],{month:'short',day:'numeric'}):date.toLocaleDateString([],{month:'short',year:'2-digit'})};
  ctx.strokeStyle='#283442';ctx.lineWidth=1;for(let i=0;i<4;i++){const y=p+i*chartHeight/3;ctx.beginPath();ctx.moveTo(p,y);ctx.lineTo(W-p,y);ctx.stroke();ctx.fillStyle='#91a0ae';ctx.font='11px system-ui';ctx.fillText((max-(max-min)*i/3).toFixed(0)+' W',2,y+4)}
  ctx.textAlign='center';for(let i=0;i<5;i++){const X=p+i*(W-2*p)/4,time=start+i*duration/4;ctx.strokeStyle='#283442';ctx.beginPath();ctx.moveTo(X,H-bottom);ctx.lineTo(X,H-bottom+4);ctx.stroke();ctx.fillStyle='#91a0ae';ctx.font='11px system-ui';ctx.fillText(labelDate(time),X,H-6)}ctx.textAlign='start';
  datasets.forEach(dataset=>{ctx.beginPath();let started=false;dataset.points.forEach(point=>{if(!Number.isFinite(point.watts))return;const X=p+(new Date(point.observed_at).getTime()-start)/duration*(W-2*p),Y=H-bottom-(point.watts-min)/(max-min)*chartHeight;if(started)ctx.lineTo(X,Y);else{ctx.moveTo(X,Y);started=true}});ctx.strokeStyle=dataset.color;ctx.lineWidth=2;ctx.stroke()});
}
if(typeof readings!=='undefined' && document.getElementById('aggregate')){drawChart(document.getElementById('aggregate'),readings,'watts','#64d8cb','W');drawSupplyVoltageChart(document.getElementById('supply-voltage'),supplyReadings);let newest=readings.at(-1);document.getElementById('total').textContent=newest?`${newest.watts.toFixed(1)} W`:'—';document.getElementById('voltage').textContent=newest?.voltage?`${newest.voltage.toFixed(1)} V`:'—';document.getElementById('reported').textContent=newest?`${newest.reporters} reporting plug${newest.reporters===1?'':'s'}`:'Waiting for reports'}
if(typeof comparisonSeries!=='undefined' && document.getElementById('device-comparison')){drawComparisonChart(document.getElementById('device-comparison'),comparisonSeries)}
function chartFrame(canvas, points, values, unit, forceZero=false) {
  const ctx=canvas.getContext('2d'), w=canvas.width=canvas.clientWidth*devicePixelRatio, h=canvas.height=canvas.clientHeight*devicePixelRatio;
  ctx.scale(devicePixelRatio,devicePixelRatio);const W=canvas.clientWidth,H=canvas.clientHeight,p=32,bottom=28;
  if(!values.length){ctx.fillStyle='#91a0ae';ctx.font='14px system-ui';ctx.fillText('No measurements for this period.',p,H/2);return null}
  const chartHeight=H-p-bottom;let min=forceZero?0:Math.min(...values),max=Math.max(...values)*1.05||1;if(max===min){min-=1;max+=1}
  ctx.strokeStyle='#283442';ctx.lineWidth=1;for(let i=0;i<4;i++){const y=p+i*chartHeight/3;ctx.beginPath();ctx.moveTo(p,y);ctx.lineTo(W-p,y);ctx.stroke();ctx.fillStyle='#91a0ae';ctx.font='11px system-ui';ctx.fillText((max-(max-min)*i/3).toFixed(unit==='°F'?1:0)+' '+unit,2,y+4)}
  const labelDate=(raw)=>{const date=new Date(raw),span=new Date(points.at(-1).observed_at)-new Date(points[0].observed_at);return span<=172800000?date.toLocaleTimeString([],{hour:'numeric',minute:'2-digit'}):span<=3456000000?date.toLocaleDateString([],{month:'short',day:'numeric'}):date.toLocaleDateString([],{month:'short',year:'2-digit'})};
  ctx.textAlign='center';for(let i=0;i<5;i++){const index=Math.round(i*(points.length-1)/4),X=p+i*(W-2*p)/4;ctx.strokeStyle='#283442';ctx.beginPath();ctx.moveTo(X,H-bottom);ctx.lineTo(X,H-bottom+4);ctx.stroke();ctx.fillStyle='#91a0ae';ctx.font='11px system-ui';ctx.fillText(labelDate(points[index].observed_at),X,H-6)}ctx.textAlign='start';
  return {ctx,W,H,p,bottom,chartHeight,min,max};
}
function drawMultiChart(canvas, points, series, unit, forceZero=false) {
  const values=series.flatMap(([field])=>points.map(point=>point[field]).filter(Number.isFinite)),frame=chartFrame(canvas,points,values,unit,forceZero);if(!frame)return;
  const {ctx,W,H,p,bottom,chartHeight,min,max}=frame;
  series.forEach(([field,color,label],datasetIndex)=>{ctx.beginPath();let started=false;points.forEach((point,index)=>{if(!Number.isFinite(point[field]))return;const X=p+index*(W-2*p)/Math.max(1,points.length-1),Y=H-bottom-(point[field]-min)/(max-min)*chartHeight;if(started)ctx.lineTo(X,Y);else{ctx.moveTo(X,Y);started=true}});ctx.strokeStyle=color;ctx.lineWidth=2;ctx.stroke();ctx.fillStyle=color;ctx.font='12px system-ui';ctx.fillText(label,p+datasetIndex*92,p+15)});
}
function drawStateTimeline(canvas, points) {
  const active=points.filter(point=>point.operation);const ctx=canvas.getContext('2d'),w=canvas.width=canvas.clientWidth*devicePixelRatio,h=canvas.height=canvas.clientHeight*devicePixelRatio;ctx.scale(devicePixelRatio,devicePixelRatio);const W=canvas.clientWidth,H=canvas.clientHeight,p=32,bottom=28;
  if(!active.length){ctx.fillStyle='#91a0ae';ctx.font='14px system-ui';ctx.fillText('Operating states are available for the most recent seven days.',p,H/2);return}
  const colors={cooling:'#64d8cb',heating:'#ffb86b',idle:'#5f7182'},start=new Date(points[0].observed_at).getTime(),end=new Date(points.at(-1).observed_at).getTime(),duration=Math.max(1,end-start);
  points.forEach((point,index)=>{if(!point.operation)return;const next=points[index+1],from=p+(new Date(point.observed_at).getTime()-start)/duration*(W-2*p),to=next?p+(new Date(next.observed_at).getTime()-start)/duration*(W-2*p):W-p;ctx.fillStyle=colors[point.operation.toLowerCase()]||'#bb9cff';ctx.fillRect(from,H/2-18,Math.max(2,to-from),36)});
  ctx.fillStyle='#91a0ae';ctx.font='12px system-ui';ctx.fillText('Cooling',p,H/2-28);ctx.fillText('Heating',p+76,H/2-28);ctx.fillText('Other',p+148,H/2-28);ctx.fillStyle='#64d8cb';ctx.fillRect(p,H/2-43,10,4);ctx.fillStyle='#ffb86b';ctx.fillRect(p+76,H/2-43,10,4);ctx.fillStyle='#bb9cff';ctx.fillRect(p+148,H/2-43,10,4);
}
