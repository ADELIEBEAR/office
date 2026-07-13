const $ = (id) => document.getElementById(id);
let currentJob = null;
let pollTimer = null;
let elapsedTimer = null;
let jobStartedAt = null;
let lastEventId = 0;
let lastData = { raw_data: "", script: "", thumbnail_copy: "", thumbnail_concepts: [], thumbnail_images: [], infographic_concepts: [], infographic_slides: [], voice_items: [], full_package: {} };
const deptOrder = ["planning","research","writing","review","design","video","shipping"];
const activeLines = {
  research: ["lineResearchWrite"], writing: ["linePlanWrite","lineResearchWrite"], review: ["lineWriteReview"], design: ["lineReviewDesign"], video: ["lineReviewDesign"], shipping: ["lineDesignShip","lineReviewShip"]
};
const deptDefaultTalk = {
  planning: "오늘 영상 방향 잡는 중",
  research: "공시랑 수급 확인할게요",
  writing: "말로 읽히게 다듬는 중",
  review: "숫자랑 장중 표현 볼게요",
  design: "클릭될 그림으로 바꾸는 중",
  video: "일레븐랩스 음성 준비",
  shipping: "한 폴더로 포장합니다"
};
const deptSceneProps = {
  planning: ['화이트보드','아이디어 보드'],
  research: ['데이터 서버','뉴스 모니터'],
  writing: ['원고 데스크','녹음 부스'],
  review: ['검수 모니터','팩트 보드'],
  design: ['드로잉 태블릿','컬러 보드'],
  video: ['편집 콘솔','오디오 랙'],
  shipping: ['출고 박스','파일 서버']
};
function buildTycoonOffice(){
  let totalCrew=0;
  let spriteCursor=0;
  document.querySelectorAll('.office-room').forEach(room=>{
    if(room.querySelector('.tycoon-scene')) return;
    const dept=room.dataset.dept||'planning';
    const names=[...room.querySelectorAll('.crew-list em')].map(el=>el.textContent.trim()).filter(Boolean);
    const crew=Math.max(2,Number(room.dataset.crew||names.length||2));
    totalCrew+=crew;
    while(names.length<crew) names.push(`팀원 ${names.length+1}`);
    const props=deptSceneProps[dept]||['업무 데스크','자료 보드'];
    const agents=names.slice(0,4).map((name,index)=>{
      const sprite=(spriteCursor++%9)+1;
      return `<span class="pixel-agent agent-${index+1} sprite-${sprite}" title="${escapeHtml(name)}"><i class="pixel-head"></i><i class="pixel-body"></i><i class="pixel-legs"></i><b>${escapeHtml(name)}</b></span>`;
    }).join('');
    const scene=document.createElement('div');
    scene.className='tycoon-scene';
    scene.setAttribute('aria-label',`${room.querySelector('h3')?.textContent||'부서'} 사무실`);
    scene.innerHTML=`
      <span class="room-window"><i></i><i></i></span>
      <span class="pixel-board"><b>${escapeHtml(props[1])}</b></span>
      <span class="pixel-desk desk-one"><i class="pixel-monitor"></i><i class="desk-chair"></i></span>
      <span class="pixel-desk desk-two"><i class="pixel-monitor"></i><i class="desk-chair"></i></span>
      <span class="pixel-prop"><b>${escapeHtml(props[0])}</b></span>
      <span class="pixel-plant"><i></i></span>
      <span class="pixel-cabinet"><i></i><i></i></span>
      <div class="pixel-agents">${agents}</div>
      <span class="task-packet"></span>`;
    const head=room.querySelector('.room-head');
    head?.insertAdjacentElement('afterend',scene);
  });
  if($('totalCrewCount')) $('totalCrewCount').textContent=String(totalCrew);
}
function updateOfficeClock(){ if($('officeClock')) $('officeClock').textContent=new Date().toLocaleTimeString('ko-KR',{hour:'2-digit',minute:'2-digit',hour12:false}); }
function log(msg){ const el=$("log"); const t=new Date().toLocaleTimeString(); el.innerHTML = `<div>[${t}] ${escapeHtml(msg)}</div>` + el.innerHTML; }
function escapeHtml(s){return String(s||"").replace(/[&<>"]/g,m=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;"}[m]));}
function shortTask(msg, fallback){
  const text=String(msg||fallback||'대기중').replace(/^[0-9]+\/[0-9]+\s*/,'');
  return text.length>22 ? text.slice(0,22)+'…' : text;
}
function normalizeActiveDepartments(active, message=''){
  const list = Array.isArray(active) ? active.filter(Boolean) : [active].filter(Boolean);
  const text = String(message || '');
  if(/병렬|동시|음성|일레븐|영상팀/.test(text) && !list.includes('video')) list.push('video');
  if(/병렬|동시|썸네일|인포|디자인실/.test(text) && !list.includes('design')) list.push('design');
  return list.length ? list : [];
}
function setDept(active, done=[], message=''){
  const activeList = normalizeActiveDepartments(active, message);
  document.querySelectorAll('.office-room').forEach(room=>{
    const id=room.dataset.dept;
    const isActive = activeList.includes(id);
    room.classList.toggle('active', isActive);
    room.classList.toggle('done', done.includes(id) && !isActive);
    room.querySelector('span').textContent = isActive ? '작업중' : (done.includes(id) ? '완료' : '대기');
    const bubble=room.querySelector('.bubble'); if(bubble) bubble.textContent = isActive ? shortTask(message, deptDefaultTalk[id]) : deptDefaultTalk[id];
    const chip=room.querySelector('.task-chip'); if(chip) chip.textContent = isActive ? '팀 작업중' : (done.includes(id) ? '완료됨' : '대기중');
  });
  document.querySelectorAll('.flow-lines path').forEach(p=>p.classList.remove('active'));
  activeList.forEach(dept => (activeLines[dept]||[]).forEach(id=>{ const el=$(id); if(el) el.classList.add('active'); }));
  const activeCrew=activeList.reduce((sum,id)=>{
    const room=document.querySelector(`.office-room[data-dept="${id}"]`);
    return sum + Number(room?.dataset.crew||0);
  },0);
  if($('activeTeamCount')) $('activeTeamCount').textContent=`${activeList.length}개 팀`;
  if($('activeCrewCount')) $('activeCrewCount').textContent=`${activeCrew}명`;
  if($('workingCrewCount')) $('workingCrewCount').textContent=String(activeCrew);
  if($('stageName')) $('stageName').textContent=activeList.length ? activeList.map(id=>document.querySelector(`.office-room[data-dept="${id}"] h3`)?.textContent||id).join(' + ') : (done.length ? '출고 완료' : '의뢰 대기');
}
function doneBefore(active){ const i=deptOrder.indexOf(active); return i>0 ? deptOrder.slice(0,i) : []; }
function payload(engine){ return { stock_name: $('stockName').value.trim(), stock_code: $('stockCode').value.trim(), format_name: $('formatName').value, custom_topic: $('customTopic').value.trim(), output_dir: $('outputDir').value, engine: engine || 'chain', raw_data: lastData.raw_data, script: lastData.script, thumbnail_copy: lastData.thumbnail_copy, concepts: selectedConcepts(), infographic_concepts: selectedInfoConcepts(), infographic_color_theme: $('infoTheme')?.value || 'dark_lineart_city', infographic_layout_concept: $('infoLayout')?.value || 'photo_fullbleed', infographic_photo_accent: $('infoPhotoAccent')?.checked ?? true, infographic_custom_color: $('infoCustomColor')?.value || '', image_parallel_workers: Number($('infoWorkers')?.value || 2) }; }
async function api(path, body){ const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})}); const j=await r.json(); if(!j.ok) throw new Error(j.error||'요청 실패'); return j; }
async function startJob(path, body){
  if(currentJob) return;
  const res=await api(path, body);
  currentJob=res.job_id;
  jobStartedAt=Date.now();
  lastEventId=0;
  document.body.classList.add('is-running');
  $('globalStatus').textContent='작업 시작';
  $('jobTitle').textContent='작업 #' + currentJob;
  $('progressBar').style.width='3%';
  log('제작 의뢰가 접수됐습니다.');
  if(body?.custom_topic) log('핵심 주제 고정: '+shortTopic(body.custom_topic,54));
  setControlsRunning(true);
  clearInterval(elapsedTimer);
  elapsedTimer=setInterval(updateElapsed,1000);
  updateElapsed();
  poll();
}
async function poll(){
  if(!currentJob) return;
  clearTimeout(pollTimer);
  try{
    const r=await fetch('/api/job/'+currentJob); const j=await r.json(); if(!j.ok) throw new Error(j.error||'작업 조회 실패');
    const job=j.job; const pct=job.progress||0; $('jobProgress').textContent=pct+'%'; $('progressBar').style.width=pct+'%'; $('globalStatus').textContent=job.message||job.status; $('jobTitle').textContent=job.title+' · '+statusLabel(job.status); setDept(job.active_departments||job.department||'planning', doneBefore(job.department), job.message||'');
    (job.events||[]).filter(event=>Number(event.id)>lastEventId).forEach(event=>{ log(event.message); lastEventId=Math.max(lastEventId,Number(event.id)||0); });
    if(job.status==='done') { handleResult(job); cleanupButtons(); setDept(null, deptOrder, '출고 완료'); $('globalStatus').textContent='출고 완료'; log('작업 완료'); return; }
    if(job.status==='error') { cleanupButtons(); setDept(null, []); log('오류: '+job.error); showToast(job.error||'작업 중 오류가 발생했습니다.','error'); return; }
    pollTimer=setTimeout(poll,1200);
  } catch(e){ cleanupButtons(); log('조회 오류: '+e.message); showToast(e.message,'error'); }
}
function setControlsRunning(running){ document.querySelectorAll('button').forEach(b=>{ b.classList.toggle('busy',running); b.disabled=running; }); }
function cleanupButtons(){ setControlsRunning(false); document.body.classList.remove('is-running'); currentJob=null; clearInterval(elapsedTimer); elapsedTimer=null; }
function updateElapsed(){ if(!jobStartedAt) return; const sec=Math.max(0,Math.floor((Date.now()-jobStartedAt)/1000)); const min=String(Math.floor(sec/60)).padStart(2,'0'); const rem=String(sec%60).padStart(2,'0'); if($('elapsedTime')) $('elapsedTime').textContent=`${min}:${rem}`; }
function statusLabel(status){ return ({queued:'대기',running:'진행중',done:'완료',error:'오류'})[status]||status; }
function showToast(message,type='info'){ const old=document.querySelector('.app-toast'); if(old) old.remove(); const toast=document.createElement('div'); toast.className=`app-toast ${type}`; toast.textContent=message; document.body.appendChild(toast); requestAnimationFrame(()=>toast.classList.add('show')); setTimeout(()=>{ toast.classList.remove('show'); setTimeout(()=>toast.remove(),250); },4200); }
function shortTopic(text,limit=38){ const clean=String(text||'').replace(/\s+/g,' ').trim(); return clean.length>limit ? clean.slice(0,limit)+'…' : clean; }
function updateTopicBrief(){
  const topic=$('customTopic')?.value||'';
  const clean=topic.trim();
  if($('topicCount')) $('topicCount').textContent=String(topic.length);
  if($('commandTopic')) $('commandTopic').textContent=clean ? shortTopic(clean) : 'AI 자동 선정';
  if($('autoPilotBtn')) $('autoPilotBtn').textContent=clean ? '✨ 이 주제로 자동 출고' : '✨ AI 자동 주제 출고';
  const foot=document.querySelector('.topic-foot');
  if(foot) foot.classList.toggle('ready',Boolean(clean));
  if($('topicState')) $('topicState').textContent=clean ? '이 주제가 초안부터 최종 검수까지 고정됩니다.' : '비워두면 AI가 주제를 자동 선정합니다.';
}
function handleResult(job){
  const r=job.result||{};
  if(r.raw_data){ lastData.raw_data=r.raw_data; $('rawOut').value=r.raw_data; }
  if(r.script){ lastData.script=r.script; $('scriptOut').value=r.script; }
  if(r.stats?.topic_requested){
    log(r.stats.topic_covered ? '핵심 주제 반영 검사 완료' : '핵심 주제 반영이 약해 결과를 직접 확인해 주세요.');
  }
  if(r.thumbnail_copy){ lastData.thumbnail_copy=r.thumbnail_copy; $('thumbOut').value=r.thumbnail_copy; }
  if(Array.isArray(r.concepts)){ lastData.thumbnail_concepts=r.concepts; renderConcepts(r.concepts); }
  if(Array.isArray(r.items)){ lastData.thumbnail_images=r.items; renderGallery(r.items); $('thumbOut').value = JSON.stringify(r,null,2); }
  if(Array.isArray(r.infographic_concepts)){ lastData.infographic_concepts=r.infographic_concepts; renderInfoConcepts(r.infographic_concepts); $('thumbOut').value = JSON.stringify(r.infographic_concepts,null,2); }
  if(Array.isArray(r.infographic_items)){ lastData.infographic_slides=r.infographic_items; renderInfoGallery(r.infographic_items); $('thumbOut').value = JSON.stringify(r,null,2); }
  if(Array.isArray(r.voice_items)){ lastData.voice_items=r.voice_items; }
  if(r.package){ lastData.full_package=r.package; }
  if(Array.isArray(r.results)){ $('thumbOut').value = JSON.stringify(r,null,2); }
  if(r.summary){ renderPackageSummary(r.summary); }
  if(r.path){ log('저장 파일: '+r.path); }
}

function renderPackageSummary(summary){
  const root=$('packageGrid');
  if(!root) return;
  const rawChars=Number(summary.raw_chars||0).toLocaleString();
  const scriptChars=Number(summary.script_chars||0).toLocaleString();
  const thumbCount=Number(summary.thumbnail_image_count ?? summary.thumbnail_concept_count ?? 0);
  const voiceCount=Number(summary.voice_count ?? summary.infographic_concept_count ?? 0);
  const thumbLabel = summary.thumbnail_image_count !== undefined ? '썸네일 이미지' : 'CTR 컨셉 후보';
  const voiceLabel = summary.voice_count !== undefined ? 'mp3 음성 파일' : '인포 장면 후보';
  $('packageStatus').textContent = `${escapeHtml(summary.stock_name||'종목')} 준비 완료`;
  root.innerHTML = `
    <div class="package-card"><b>자료</b><strong>${rawChars}</strong><small>수집 데이터 글자</small></div>
    <div class="package-card"><b>대본</b><strong>${scriptChars}</strong><small>완성 대본 글자</small></div>
    <div class="package-card"><b>썸네일</b><strong>${thumbCount}개</strong><small>${thumbLabel}</small></div>
    <div class="package-card"><b>음성/인포</b><strong>${voiceCount}개</strong><small>${voiceLabel}</small></div>
  `;
  $('packageFolder').textContent = summary.package_dir ? `출고 폴더: ${summary.package_dir}` : '';
  if(Array.isArray(summary.next_steps)){
    summary.next_steps.forEach(step=>log('다음 단계: '+step));
  }
}

function renderConcepts(concepts){
  const root=$('conceptStrip');
  if(!root) return;
  if(!concepts.length){ root.innerHTML='<div class="empty-card">컨셉 후보가 없습니다.</div>'; return; }
  root.innerHTML=concepts.map((c,idx)=>`
    <div class="concept-card ${c.selected?'selected':''}" data-idx="${idx}">
      <span class="score">${c.ctr_score||80}</span>
      <b>${escapeHtml(c.badge||'긴급속보')}</b>
      <strong>${escapeHtml(c.main||'메인문구')}</strong>
      <em>${escapeHtml(c.sub||'서브문구')}</em>
      <small>${escapeHtml(c.style||'프리미엄')} · 후보 ${idx+1}</small>
    </div>
  `).join('');
  root.querySelectorAll('.concept-card').forEach(card=>{
    card.onclick=()=>{
      const idx=Number(card.dataset.idx);
      lastData.thumbnail_concepts[idx].selected=!lastData.thumbnail_concepts[idx].selected;
      renderConcepts(lastData.thumbnail_concepts);
    };
  });
}

function selectedConcepts(){
  return (lastData.thumbnail_concepts||[]).filter(c=>c.selected);
}

function renderGallery(items){
  const root=$('thumbGallery');
  if(!root) return;
  if(!items.length){ root.innerHTML='<div class="empty-card">이미지 결과가 없습니다.</div>'; return; }
  root.innerHTML=items.map((item,idx)=>{
    const url=item.url||'';
    return `<div class="thumb-card">
      ${url?`<img src="${escapeHtml(url)}" alt="thumbnail ${idx+1}" />`:'<div class="empty-card">이미지 없음</div>'}
      <a href="${escapeHtml(url)}" target="_blank">시안 ${idx+1} 열기 · ${escapeHtml(item.style||'')}</a>
    </div>`;
  }).join('');
}

function renderInfoConcepts(concepts){
  const root=$('infoConceptStrip');
  if(!root) return;
  if(!concepts.length){ root.innerHTML='<div class="empty-card">인포그래픽 후보가 없습니다.</div>'; return; }
  root.innerHTML=concepts.map((c,idx)=>`
    <div class="concept-card ${c.selected?'selected':''}" data-idx="${idx}">
      <span class="score">S${c.scene_no||idx+1}</span>
      <b>${escapeHtml(c.style||'프리미엄 다크')}</b>
      <strong>${escapeHtml(c.title||'슬라이드 제목')}</strong>
      <em>${escapeHtml(c.main||'핵심 문장')}</em>
      <span class="layout">${escapeHtml(c.layout||'카드형 레이아웃')}</span>
      <small>대본 장면 ${idx+1}</small>
    </div>
  `).join('');
  root.querySelectorAll('.concept-card').forEach(card=>{
    card.onclick=()=>{
      const idx=Number(card.dataset.idx);
      lastData.infographic_concepts[idx].selected=!lastData.infographic_concepts[idx].selected;
      renderInfoConcepts(lastData.infographic_concepts);
    };
  });
}

function selectedInfoConcepts(){
  return (lastData.infographic_concepts||[]).filter(c=>c.selected);
}

function renderInfoGallery(items){
  const root=$('infoGallery');
  if(!root) return;
  if(!items.length){ root.innerHTML='<div class="empty-card">인포그래픽 이미지 결과가 없습니다.</div>'; return; }
  root.innerHTML=items.map((item,idx)=>{
    const url=item.url||'';
    return `<div class="thumb-card">
      ${url?`<img src="${escapeHtml(url)}" alt="infographic ${idx+1}" />`:'<div class="empty-card">이미지 없음</div>'}
      <a href="${escapeHtml(url)}" target="_blank">인포 ${idx+1} 열기 · ${escapeHtml(item.style||'')}</a>
    </div>`;
  }).join('');
}
async function loadConfig(){
  const r=await fetch('/api/config'); const j=await r.json(); if(!j.ok) throw new Error(j.error||'설정 로드 실패');
  const preset=$('preset'); preset.innerHTML=''; Object.entries(j.presets).forEach(([name,code])=>{ const o=document.createElement('option'); o.value=name; o.textContent=name; o.dataset.code=code; preset.appendChild(o); });
  const fmt=$('formatName'); fmt.innerHTML=''; j.formats.forEach(f=>{ const o=document.createElement('option'); o.value=f; o.textContent=f; fmt.appendChild(o); });
  const theme=$('infoTheme'); if(theme){ theme.innerHTML=''; (j.infographic_themes||[]).forEach(t=>{ const o=document.createElement('option'); o.value=t.key; o.textContent=t.label; theme.appendChild(o); }); theme.value='dark_lineart_city'; }
  const layout=$('infoLayout'); if(layout){ layout.innerHTML=''; (j.infographic_layouts||[]).forEach(l=>{ const o=document.createElement('option'); o.value=l.key; o.textContent=l.label; layout.appendChild(o); }); layout.value='photo_fullbleed'; }
  $('outputDir').value=j.output_dir||'';
  const initialName=$('stockName').value.trim();
  if([...preset.options].some(option=>option.value===initialName)) preset.value=initialName;
  preset.addEventListener('change',()=>{ const opt=preset.selectedOptions[0]; $('stockName').value=opt.value; $('stockCode').value=opt.dataset.code||''; });
  log('AI 제작사 로드 완료');
}
$('collectBtn').onclick=()=>startJob('/api/collect', payload());
$('autoPilotBtn').onclick=()=>startJob('/api/full-package', payload('chain'));
$('fullPackageBtn').onclick=()=>startJob('/api/full-package', payload('chain'));
$('oneClickBtn').onclick=()=>startJob('/api/one-click', payload('chain'));
$('scriptBtn').onclick=()=>startJob('/api/script', payload('chain'));
$('fastScriptBtn').onclick=()=>startJob('/api/script', payload('fast_openai'));
$('thumbCopyBtn').onclick=()=>startJob('/api/thumbnail-copy', payload());
$('thumbConceptBtn').onclick=()=>startJob('/api/thumbnail-concepts', {...payload(), count:8});
$('thumbImgBtn').onclick=()=>startJob('/api/thumbnail-images', {...payload(), count:Math.max(1, selectedConcepts().length || 3)});
$('infoConceptBtn').onclick=()=>startJob('/api/infographic-concepts', {...payload(), count:6});
$('infoImgBtn').onclick=()=>startJob('/api/infographic-slides', {...payload(), count:Math.max(1, selectedInfoConcepts().length || 4)});
$('openOutputBtn').onclick=()=>api('/api/open-output',{}).then(r=>log('폴더 열기: '+r.path)).catch(e=>showToast(e.message,'error'));
$('selectAllConcepts').onclick=()=>{ lastData.thumbnail_concepts=(lastData.thumbnail_concepts||[]).map(c=>({...c,selected:true})); renderConcepts(lastData.thumbnail_concepts); };
$('clearConcepts').onclick=()=>{ lastData.thumbnail_concepts=(lastData.thumbnail_concepts||[]).map(c=>({...c,selected:false})); renderConcepts(lastData.thumbnail_concepts); };
$('selectAllInfo').onclick=()=>{ lastData.infographic_concepts=(lastData.infographic_concepts||[]).map(c=>({...c,selected:true})); renderInfoConcepts(lastData.infographic_concepts); };
$('clearInfo').onclick=()=>{ lastData.infographic_concepts=(lastData.infographic_concepts||[]).map(c=>({...c,selected:false})); renderInfoConcepts(lastData.infographic_concepts); };
$('customTopic').addEventListener('input',updateTopicBrief);
document.querySelectorAll('.topic-chip').forEach(chip=>{
  chip.addEventListener('click',()=>{
    const text=String(chip.dataset.topic||'').trim();
    const current=$('customTopic').value.trim();
    if(text && !current.includes(text)) $('customTopic').value=current ? `${current}\n${text}` : text;
    updateTopicBrief();
    $('customTopic').focus();
  });
});
loadConfig().catch(e=>showToast(e.message,'error'));
updateTopicBrief();
buildTycoonOffice();
updateOfficeClock();
setInterval(updateOfficeClock,30000);
setDept(null,[]);
