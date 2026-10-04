const form=document.querySelector('#agent-form');
const question=document.querySelector('#agent-question');
const chat=document.querySelector('#agent-chat');
const status=document.querySelector('#agent-status');
const history=[];

function addMessage(role, text, sources=[]){
  const message=document.createElement('article');
  message.className=`agent-message ${role}`;
  const heading=document.createElement('strong');
  heading.textContent=role==='user'?'You':'Home Energy Analyst';
  const body=document.createElement('p');
  body.textContent=text;
  message.append(heading,body);
  if(sources.length){
    const meta=document.createElement('small');
    meta.textContent=`Data used: ${sources.map(s=>s.period?`${s.tool} (${s.period})`:s.tool).join(', ')}`;
    message.append(meta);
  }
  chat.append(message);
  chat.scrollTop=chat.scrollHeight;
}

async function ask(value){
  const button=form.querySelector('button');
  addMessage('user',value);
  history.push({role:'user',content:value});
  question.value=''; button.disabled=true;
  try{
    const queueResponse=await fetch('/agent/status');
    const queueState=queueResponse.ok ? await queueResponse.json() : null;
    if(queueState?.active){
      const ahead=(queueState.waiting||0)+1;
      status.textContent=`Another analysis is in progress. Yours is queued behind ${ahead} ${ahead===1?'request':'requests'}.`;
    }else{
      status.textContent='Analyzing local data…';
    }
    const body=new FormData(); body.set('question',value); body.set('history',JSON.stringify(history.slice(-8)));
    const response=await fetch('/agent/ask',{method:'POST',body});
    const result=await response.json();
    if(!response.ok) throw new Error(result.detail||'Analysis failed.');
    addMessage('assistant',result.answer,result.sources||[]);
    history.push({role:'assistant',content:result.answer});
    status.textContent=result.queued?`Queued analysis answered by ${result.model} locally.`:`Answered by ${result.model} locally.`;
  }catch(error){
    addMessage('assistant',error.message||'Analysis is unavailable.');
    status.textContent='No data was changed.';
  }finally{button.disabled=false; question.focus();}
}
form.addEventListener('submit',event=>{event.preventDefault();const value=question.value.trim();if(value)ask(value);});
document.querySelectorAll('[data-question]').forEach(button=>button.addEventListener('click',()=>ask(button.dataset.question)));
