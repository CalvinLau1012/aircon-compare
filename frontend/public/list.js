function matches(m){
  const q=document.getElementById('q').value.trim().toLowerCase();
  const hp=document.getElementById('fHp').value;
  const ty=document.getElementById('fType').value;
  const br=document.getElementById('fBrand').value;
  const en=document.getElementById('fEnergy').value;
  const mo=document.getElementById('fMount').value;
  const st=document.getElementById('fStatus').value;
  const pr=document.getElementById('fPrice').value;
  if(hp && m.hp!==hp) return false;
  if(ty && m.type!==ty) return false;
  if(br && m.brand!==br) return false;
  if(en && m.energy!==en) return false;
  if(mo && m.mount!==mo) return false;
  if(st && m.status!==st) return false;
  if(pr){
    // 未知價唔屬於任何價位，唔可以當成「5以上」（priceMin 對無價會回 999999）
    const m1=String(m.price||'').match(/\$([\d,]+)/);
    if(!m1) return false;
    const p=parseInt(m1[1].replace(/,/g,''));
    if(pr==='2以下' && p>=2000) return false;
    if(pr==='2-3' && (p<2000||p>=3000)) return false;
    if(pr==='3-4' && (p<3000||p>=4000)) return false;
    if(pr==='4-5' && (p<4000||p>=5000)) return false;
    if(pr==='5以上' && p<5000) return false;
  }
  if(q && !(m.brand.toLowerCase().includes(q)||m.model.toLowerCase().includes(q))) return false;
  if(document.getElementById('fSelOnly').checked && !selected.has(m.brand+'|'+m.model)) return false;
  return true;
}

function priceMin(m){
  const ms=String(m.price).match(/\$([\d,]+)/g)||['$999999'];
  return parseInt(ms[0].replace(/[$,]/g,''));
}
function energyVal(m){return parseInt(m.energy)||9;}

function sortModels(arr){
  const v=document.getElementById('sortBy').value;
  const a=[...arr];
  if(v==='price') a.sort((x,y)=>priceMin(x)-priceMin(y));
  else if(v==='energy') a.sort((x,y)=>energyVal(x)-energyVal(y));
  else if(v==='kwh') a.sort((x,y)=>(parseInt(x.kwh)||9999)-(parseInt(y.kwh)||9999));
  else if(v==='cspf') a.sort((x,y)=>(parseFloat(y.cspf)||0)-(parseFloat(x.cspf)||0));
  // 已選型號永遠排前
  if(selected.size){
    a.sort((x,y)=>((selected.has(y.brand+'|'+y.model)?1:0)-(selected.has(x.brand+'|'+x.model)?1:0)));
  }
  return a;
}

function renderList(){
  const list=document.getElementById('modelList');
  const arr=sortModels(ALL.filter(matches));
  const vis=arr.slice(0, shown);
  list.innerHTML=vis.map((m)=>{
    const id=m.brand+'|'+m.model;
    const on=selected.has(id);
    const priceHtml = m.price
      ? `<a class="plink" href="${gsearch(m)}" target="_blank" rel="noopener" onclick="event.stopPropagation()">${esc(m.price)} 🔍</a>`
      : `<a class="plink" href="${gsearch(m)}" target="_blank" rel="noopener" onclick="event.stopPropagation()">🔍 搜價</a>`;
    const wifiBadge = m.type==='變頻'?'🔷':'🔶';
    const energyTxt = m.energy ? esc(m.energy) : '—';
    const statusTxt = m.status && m.status!=='有價' ? ` · <b>${esc(m.status)}</b>` : '';
    const mountTxt = m.mount ? `${esc(m.mount)} ` : '';
    const modeTxt = m.mode ? `${esc(m.mode)} ` : '';
    const remoteTxt = m.remote==='✅' ? '· 有遙控 ' : (m.remote==='➖' ? '· 無遙控 ' : '');
    const extra = (m.gas ? ` · ${esc(m.gas)}` : '') + (m.cspf ? ` · CSPF ${esc(m.cspf)}` : '');
    return `<label class="mitem ${on?'checked':''}">
      <input type="checkbox" ${on?'checked':''} onchange="toggle('${esc(id)}',this)">
      <span class="info">
        <span class="name">${esc(m.brand)} ${esc(m.model)}</span><br>
        <span class="tag">${mountTxt}${modeTxt}${esc(m.hp||'?匹')} · ${esc(m.btu)} BTU · ${esc(m.type)} · ${energyTxt} ${remoteTxt}${statusTxt}${extra}· ${priceHtml}</span>
      </span>
      <span class="badge">${wifiBadge} ${energyTxt}</span>
    </label>`;
  }).join('')||'<div class="phint">冇符合嘅型號</div>';
  const btn=document.getElementById('btnMore');
  if(btn){
    if(arr.length>shown){ btn.style.display='block'; btn.textContent=`顯示更多（仲有 ${arr.length-shown} 個）`; }
    else btn.style.display='none';
  }
}

function showMore(){ shown += 100; renderList(); }

function toggle(id,el){
  if(el.checked) selected.add(id); else selected.delete(id);
  document.getElementById('selCount').textContent=`已選 ${selected.size} 個（最少 2 個）`;
  const lab=el.closest('.mitem');
  if(lab) lab.classList.toggle('checked', el.checked);
  // 唔自動開面板，等用戶撳「開始比較」
  document.getElementById('btnCompare').disabled = selected.size < 2;
  // 若面板開住，即時更新內容
  const panel=document.getElementById('panel');
  if(panel.classList.contains('open')) buildPanel();
  if(selected.size<2) setPanelOpen(false);
  // 「只顯示已選」之下，反選要即刻由列表移除，先唔會顯示唔符合過濾條件嘅型號
  if(document.getElementById('fSelOnly').checked) renderList();
}

function updateUI(){
  document.getElementById('selCount').textContent=`已選 ${selected.size} 個（最少 2 個）`;
  document.getElementById('btnCompare').disabled = selected.size < 2;
  const panel=document.getElementById('panel');
  if(panel.classList.contains('open')) buildPanel();
  if(selected.size<2) setPanelOpen(false, true);
  renderList();
}

