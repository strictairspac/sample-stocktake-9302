import {table} from '../components/common/table.js';
import {countBadge,lineBadge} from '../components/common/countBadge.js';
import {esc,date} from '../utils/html.js';
import {session} from '../hooks/session.js';

const canWrite=()=>session.user?.role!=='viewer';

function summaryLine(x){const s=x.summary||{};return `${s.total??0} 样本 · 待盘 ${s.pending??0} · 一致 ${s.matched??0} · 调整 ${s.adjusted??0} · 冲突 ${s.conflict??0}`;}

function batches(items){
  return `<div class="section-top"><h2>盘点批次</h2><span>${items.length} 批</span></div>${table(['批次 / 冻结时间','状态','盘点结果','说明'],items.map(x=>[
    `<a href="#counts/${x.id}"><strong>${esc(x.batch_code)}</strong></a><small>${date(x.frozen_at)} · ${esc(x.created_by)}</small>`,
    countBadge(x.status),summaryLine(x),
    x.status==='conflict'?`<span class="error">${esc(x.failed_reason||'整批拒绝')}</span>`:x.status==='posted'?`已落账 ${x.posted_at?date(x.posted_at):''} · ${esc(x.posted_by)}`:'—'
  ]))}`;
}

function detailBlock(count){
  if(!count)return '<div class="empty">选择上方任一批次查看明细；或先冻结一次整库盘点。</div>';
  const editable=count.status==='pending'&&canWrite();
  const rows=count.lines.map(line=>{
    const physical=editable
      ?`<input class="count-input" form="count-lines-form" name="physical_${line.id}" type="number" min="0" max="100000" value="${line.physical_quantity??''}" placeholder="实盘量">`
      :(line.physical_quantity??'—');
    const reason=line.result==='conflict'?`<span class="error">${esc(line.note||'整批拒绝')}</span>`:esc(line.note||'—');
    return [
      `<strong>${esc(line.sample_code)}</strong><small>${esc(line.sample_title)}</small>`,
      `${line.frozen_quantity}`,
      line.changed_since_freeze?`<span class="error">${line.current_quantity}（已变更）</span>`:`${line.current_quantity??'—'}`,
      physical,lineBadge(line.result),reason];
  });
  return `<div class="section-top" style="margin-top:30px"><h2>批次 #${count.id} 明细</h2><span>${countBadge(count.status)} ${summaryLine(count)}</span></div>
    ${count.failed_reason?`<div class="error">整批拒绝原因：${esc(count.failed_reason)}（原库存未变，可重新发起盘点）</div>`:''}
    <form id="count-lines-form" data-count="${count.id}">
    ${table(['样本','冻结量','当前台账','实盘量','明细状态','原因 / 备注'],rows)}
    ${editable?`<div class="count-actions"><button type="submit" class="primary" style="width:auto">保存实盘量</button></div>`:''}
    </form>
    ${count.status==='pending'&&canWrite()?`<div class="count-actions"><button id="post-count" data-id="${count.id}">整批落账 →</button></div>`:''}
    <div class="notice">落账时整批在一个数据库事务内提交：任一冻结后改量的样本（即使改回原值）或已发起演练的样本都会使整批拒绝，原库存不变；落账成功后超量移交申请自动失效。</div>`;
}

export function countsPage(items,count){return `<div class="split"><section>${batches(items)}${detailBlock(count)}</section><section class="form-panel"><span class="eyebrow">FULL STOCKTAKE</span><h2>冻结整库盘点</h2><form id="count-form"><label>批次编号<input name="batch_code" placeholder="COUNT-20261117" required></label><label>盘点说明<input name="note" value="11 月整库盘点"></label><button class="primary" ${canWrite()?'':'disabled'}>冻结此刻全部样本 +</button></form><p class="help">冻结时记录全部样本及数量；此后新登记的样本不纳入本批。日常登记与改量照常进行，但冻结后改过量的样本会使整批落账被拒绝。</p><div class="protocol"><h3>落账口径</h3><ol><li>任一样本冻结后改过量 → 整批拒绝</li><li>任一样本已发起演练 → 整批拒绝</li><li>落账与演练并发 → 先完成者为准</li><li>成功后超量移交申请失效，不能演练</li><li>落账中断可重试，不重复调整或记审计</li></ol></div></section></div>`;}
