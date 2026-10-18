// 初始化：填充品牌下拉 + 渲染 + 動態統計
(function(){
  const counts={};
  ALL.forEach(m=>counts[m.brand]=(counts[m.brand]||0)+1);
  const brands=[...new Set(ALL.map(m=>m.brand))].sort();
  const sel=document.getElementById('fBrand');
  brands.forEach(b=>{
    const o=document.createElement('option');
    o.value=b; o.textContent=`${b}（${counts[b]}）`;
    sel.appendChild(o);
  });
  document.getElementById('btnCompare').disabled = true;
  // 動態統計（永遠同數據同步）
  document.getElementById('statTotal').textContent = ALL.length.toLocaleString();
  document.getElementById('statPrice').textContent = ALL.filter(m=>m.price).length.toLocaleString();
  document.getElementById('statSize').textContent = ALL.filter(m=>m.size).length.toLocaleString();
  renderList();
})();

// 返回頂部按鈕顯示
window.addEventListener('scroll',()=>{
  const b=document.getElementById('backTop');
  if(b) b.classList.toggle('show', window.scrollY>300);
});

