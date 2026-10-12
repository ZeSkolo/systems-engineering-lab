'use strict';
const $ = id => document.getElementById(id);
let config, selectedTool, selectedAction, resultData;
const node = (tag, text, cls) => { const e=document.createElement(tag); if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e; };
function setStatus(text, kind=''){ $('connection').textContent=text;$('connection').className='status '+kind; }
function selectTool(tool){
 selectedTool=tool;selectedAction=tool.actions[0];$('tool-title').textContent=tool.title;$('tool-description').textContent=tool.description;
 document.querySelectorAll('.nav-button').forEach(e=>{const active=e.dataset.tool===tool.id;e.classList.toggle('active',active);e.setAttribute('aria-current',active?'page':'false');});
 $('actions').replaceChildren();for(const a of tool.actions){const b=node('button',a.label,'action-tab');b.type='button';b.dataset.action=a.id;b.onclick=()=>selectAction(a);$('actions').append(b);}selectAction(selectedAction);
 $('result').replaceChildren(node('div','Run an action to see real output from this project.','empty-state'));$('result-message').textContent='Ready when you are';$('elapsed').textContent='';resultData=undefined;$('download').disabled=true;
}
function selectAction(a){
 selectedAction=a;document.querySelectorAll('.action-tab').forEach(b=>{b.classList.toggle('active',b.dataset.action===a.id);b.setAttribute('aria-pressed',b.dataset.action===a.id?'true':'false');});
 $('fields').replaceChildren();for(const f of a.fields||[]){const wrap=node('div',undefined,'field');const label=node('label',f.label);label.htmlFor='field-'+f.key;let input;
 if(f.type==='textarea')input=node('textarea');else if(f.type==='select'){input=node('select');for(const opt of f.options){const option=node('option',opt.label||opt);option.value=opt.value||opt;input.append(option);}}else {input=node('input');input.type=f.type||'text';}
 input.id='field-'+f.key;input.name=f.key;input.required=f.required!==false;input.value=f.value==='@today'?new Date().toLocaleDateString('en-CA'):f.value||'';
 if(f.placeholder)input.placeholder=f.placeholder;if(f.min!==undefined)input.min=f.min;if(f.max!==undefined)input.max=f.max;if(f.type==='textarea'){input.rows=8;input.spellcheck=false;}if(f.maxlength)input.maxLength=f.maxlength;
 wrap.append(label,input);if(f.hint)wrap.append(node('p',f.hint,'hint'));$('fields').append(wrap);}
 $('run-label').textContent=a.label;$('form-note').textContent=a.note||'Runs locally. Your original project files stay intact.';
}
function table(rows,columns){
 if(!rows.length)return node('p','No records yet.','empty-state');
 const list=rows.map(r=>Array.isArray(r)?Object.fromEntries((columns||r.map((_,i)=>'Column '+(i+1))).map((c,i)=>[c,r[i]])):r);
 const cols=columns&&columns.length?columns:Object.keys(list[0]);const container=node('div',undefined,'table-wrap'),t=node('table'),head=node('thead'),tr=node('tr');for(const c of cols)tr.append(node('th',c.replaceAll('_',' ')));head.append(tr);t.append(head);const body=node('tbody');
 for(const row of list.slice(0,500)){const r=node('tr');for(const c of cols){const cell=node('td');const val=row[c];if(c==='url'&&typeof val==='string'&&/^https?:\/\//i.test(val)){const link=node('a',val);link.href=val;link.target='_blank';link.rel='noopener noreferrer';cell.append(link);}else cell.textContent=val===null?'null':typeof val==='object'?JSON.stringify(val):String(val??'');r.append(cell);}body.append(r);}t.append(body);container.append(t);return container;
}
function renderResult(r){
 $('result').replaceChildren();$('result-message').textContent=r.message||'Completed';$('elapsed').textContent=r.elapsed_ms!==undefined?r.elapsed_ms+' ms':'';
 if(r.preview){const a=node('a','Open original output ↗','preview-link');a.href=r.preview;a.target='_blank';a.rel='noopener';$('result').append(a);const frame=node('iframe');frame.src=r.preview;frame.title='Original project output';frame.setAttribute('sandbox','allow-scripts allow-forms allow-popups');$('result').append(frame);}
 if(r.output!==undefined)$('result').append(node('pre',typeof r.output==='string'?r.output:JSON.stringify(r.output,null,2),'output-code'));
 if(r.rows)$('result').append(table(r.rows,r.columns));if(r.monthly){$('result').append(node('h3','Monthly totals'));$('result').append(table(r.monthly));}
 if(r.facts?.length){$('result').append(node('h3','Saved memories'));for(const f of r.facts)$('result').append(node('p',f));}
 if(!r.preview&&r.output===undefined&&!r.rows&&!r.facts?.length)$('result').append(node('div','Operation completed successfully.','empty-state'));
}
$('run-form').onsubmit=async e=>{
 e.preventDefault();if(!selectedTool||!selectedAction)return;
 if(selectedAction.destructive&&!confirm('This changes stored data. Continue?'))return;
 const values=Object.fromEntries(new FormData(e.target));const button=$('run');button.disabled=true;$('run-label').textContent='Working…';$('error').hidden=true;setStatus('Running','busy');
 try{const resp=await fetch('/api/run',{method:'POST',headers:{'Content-Type':'application/json','X-Project-Request':'1'},body:JSON.stringify({tool:selectedTool.id,action:selectedAction.id,values})});const data=await resp.json();if(!resp.ok)throw Error(data.error||'Request failed');resultData=data;renderResult(data);$('download').disabled=false;setStatus('Backend connected','ok');}
 catch(err){$('error').textContent=err.message;$('error').hidden=false;setStatus('Action failed','error');}
 finally{button.disabled=false;$('run-label').textContent=selectedAction.label;}
};
$('download').onclick=()=>{if(!resultData)return;const url=URL.createObjectURL(new Blob([JSON.stringify(resultData,null,2)],{type:'application/json'}));const a=node('a');a.href=url;a.download=config.repo+'-output.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
$('theme').onclick=()=>{const current=getComputedStyle(document.body).getPropertyValue('--canvas').trim();document.documentElement.dataset.theme=current==='#191919'?'light':'dark';};
async function start(){
 try{config=window.PROJECT_CONFIG||await (await fetch('/api/config')).json();$('project-title').textContent=config.title;$('repo-name').textContent=config.repo;$('intro').textContent=config.subtitle;$('page-title').textContent=config.title+' · Project UI';$('runtime-note').textContent=config.runtime;
 $('repo-link').href='https://github.com/ZeSkolo/'+config.repo;
 for(const [i,t]of config.tools.entries()){const b=node('button',undefined,'nav-button');b.type='button';b.dataset.tool=t.id;b.append(node('span',String(i+1).padStart(2,'0'),'nav-index'),node('span',t.title));b.onclick=()=>selectTool(t);$('navigation').append(b);}selectTool(config.tools[0]);
 if(window.PROJECT_CONFIG)setStatus('Preview only','busy');else {const health=await(await fetch('/api/health')).json();if(!health.ok)throw Error('Backend unavailable');setStatus('Backend connected','ok');}
 }catch(e){setStatus('Backend unavailable','error');$('error').textContent='Start the server from your repository: python3 backend/server.py';$('error').hidden=false;}
}
start();
