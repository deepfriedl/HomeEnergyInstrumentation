function drawChart(canvas, points, field, color) {
  const ctx=canvas.getContext('2d'), w=canvas.width=canvas.clientWidth*devicePixelRatio, h=canvas.height=canvas.clientHeight*devicePixelRatio;
  ctx.scale(devicePixelRatio,devicePixelRatio); const W=canvas.clientWidth,H=canvas.clientHeight,p=32;
  const values=points.map(x=>x[field]).filter(x=>Number.isFinite(x)); if(!values.length){ctx.fillStyle='#91a0ae';ctx.font='14px system-ui';ctx.fillText('No measurements for this period.',p,H/2);return}
  const min=Math.min(0,...values),max=Math.max(...values)*1.08||1; ctx.strokeStyle='#283442';ctx.lineWidth=1;for(let i=0;i<4;i++){let y=p+i*(H-2*p)/3;ctx.beginPath();ctx.moveTo(p,y);ctx.lineTo(W-p,y);ctx.stroke();ctx.fillStyle='#91a0ae';ctx.font='11px system-ui';ctx.fillText(Math.round(max-(max-min)*i/3)+' W',2,y+4)}
  ctx.beginPath();points.forEach((x,i)=>{if(!Number.isFinite(x[field]))return;let X=p+i*(W-2*p)/Math.max(1,points.length-1),Y=H-p-(x[field]-min)/(max-min)*(H-2*p);i?ctx.lineTo(X,Y):ctx.moveTo(X,Y)});ctx.strokeStyle=color;ctx.lineWidth=2;ctx.stroke();
}
if(typeof readings!=='undefined' && document.getElementById('aggregate')){drawChart(document.getElementById('aggregate'),readings,'watts','#64d8cb');let newest=readings.at(-1);document.getElementById('total').textContent=newest?`${newest.watts.toFixed(1)} W`:'—';document.getElementById('voltage').textContent=newest?.voltage?`${newest.voltage.toFixed(1)} V`:'—';document.getElementById('reported').textContent=newest?`${newest.reporters} reporting plug${newest.reporters===1?'':'s'}`:'Waiting for reports'}
