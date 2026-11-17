"""Whole-library stocktake: freeze a single-instant snapshot, then post the physical counts atomically.

Posting rules:
- all samples existing at freeze time are captured with quantity and quantity_version;
- registrations after the freeze are not part of the batch, daily edits keep working;
- any sample whose quantity was edited after the freeze (even edited back) rejects the
  whole batch through the version comparison, and the original stock stays untouched;
- a sample with a queued/running trial also rejects the whole batch;
- the batch posts in one database transaction, so an interrupted attempt leaves no partial
  stock changes and no audit rows and can be retried; a retry after a successful commit
  never adjusts twice and never writes a second audit row;
- a successful post invalidates transfer requests that now exceed the stock, and those
  requests can no longer launch trials.
"""
from datetime import datetime, timezone
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from fastapi import HTTPException
from src.config.database import Session
from src.models.entities import Sample,Transfer,Trial,Audit,InventoryCount,InventoryCountLine
from src.utils.serialization import record

IN_FLIGHT=('queued','running')


def _lines(db,count_id):
    return list(db.scalars(select(InventoryCountLine).where(InventoryCountLine.count_id==count_id).order_by(InventoryCountLine.sample_id)))


def _summary(lines):
    return {
        'total':len(lines),
        'pending':sum(1 for x in lines if x.result=='pending'),
        'matched':sum(1 for x in lines if x.result=='matched'),
        'adjusted':sum(1 for x in lines if x.result=='adjusted'),
        'conflict':sum(1 for x in lines if x.result=='conflict'),
    }


def detail(db,ident):
    obj=db.get(InventoryCount,ident)
    if not obj: raise HTTPException(404,'盘点批次不存在')
    lines=_lines(db,obj.id)
    ids=[x.sample_id for x in lines]
    live={s.id:s for s in db.scalars(select(Sample).where(Sample.id.in_(ids)))} if ids else {}
    payload=record(obj)
    payload['summary']=_summary(lines)
    payload['lines']=[]
    for line in lines:
        item=record(line)
        current=live.get(line.sample_id)
        item['current_quantity']=current.quantity if current else None
        item['changed_since_freeze']=bool(current and (current.quantity_version or 0)!=line.frozen_version)
        payload['lines'].append(item)
    return payload


def listing(db):
    counts=list(db.scalars(select(InventoryCount).order_by(InventoryCount.id.desc())))
    result=[]
    for count in counts:
        item=record(count)
        item['summary']=_summary(_lines(db,count.id))
        result.append(item)
    return result


def freeze(db,data,user):
    """Snapshot every sample that exists at this instant. Later registrations are excluded."""
    actor=user.email
    with Session() as snapshot:
        snapshot.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ'))
        samples=list(snapshot.scalars(select(Sample).order_by(Sample.id)))
        if not samples: raise HTTPException(422,'样本台账为空，无法发起整库盘点')
        count=InventoryCount(batch_code=data.batch_code,note=data.note,created_by=actor,status='pending')
        snapshot.add(count);snapshot.flush()
        for sample in samples:
            snapshot.add(InventoryCountLine(
                count_id=count.id,sample_id=sample.id,sample_code=sample.code,sample_title=sample.title,
                frozen_quantity=sample.quantity,frozen_version=sample.quantity_version or 0,result='pending'))
        snapshot.add(Audit(actor=actor,action=f'发起整库盘点 {data.batch_code}（冻结 {len(samples)} 个样本）'))
        try: snapshot.commit()
        except IntegrityError:
            snapshot.rollback();raise HTTPException(409,'盘点批次编号已存在，请更换后重试')
        ident=count.id
    return detail(db,ident)


def enter_lines(db,ident,data,user):
    count=db.execute(select(InventoryCount).where(InventoryCount.id==ident).with_for_update()).scalar_one_or_none()
    if not count: raise HTTPException(404,'盘点批次不存在')
    if count.status=='posted': raise HTTPException(409,'该批次已落账，不能修改实盘量')
    if count.status=='conflict': raise HTTPException(409,'该批次已因冲突被整批拒绝，原库存未变，请重新发起盘点')
    requested={item.line_id:item for item in data.lines}
    if not requested: raise HTTPException(422,'没有需要录入的盘点明细')
    lines=list(db.scalars(select(InventoryCountLine).where(InventoryCountLine.count_id==count.id,InventoryCountLine.id.in_(requested))))
    if len(lines)!=len(requested): raise HTTPException(404,'盘点明细不属于该批次')
    for line in lines:
        item=requested[line.id]
        line.physical_quantity=item.physical_quantity
        line.note=item.note
    db.add(Audit(actor=user.email,action=f'录入盘点实盘量 {count.batch_code}（{len(lines)} 条）'))
    db.commit()
    return detail(db,ident)


def _reject(db,count,lines,line_reasons,summary,actor):
    """Persist a whole-batch rejection once, so retries do not duplicate audit rows."""
    first=count.status!='conflict'
    count.status='conflict';count.post_attempts=(count.post_attempts or 0)+1
    count.failed_reason=summary[:400]
    for line in lines:
        line.result='conflict'
        line.note=line_reasons.get(line.id,'整批拒绝：同批次存在冲突')
    if first: db.add(Audit(actor=actor,action=f'盘点整批拒绝 {count.batch_code}：{summary[:120]}'))
    db.commit()
    raise HTTPException(409,'整批拒绝，原库存未变：'+summary[:300])


def post(db,ident,user):
    actor=user.email
    count=db.execute(select(InventoryCount).where(InventoryCount.id==ident).with_for_update()).scalar_one_or_none()
    if not count: raise HTTPException(404,'盘点批次不存在')
    if count.status=='posted':
        # Idempotent retry after a successful commit: never adjust or audit twice.
        db.rollback()
        return detail(db,ident)
    if count.status=='conflict':
        raise HTTPException(409,'该批次已因冲突被整批拒绝，原库存未变，请重新发起盘点')
    lines=_lines(db,count.id)
    if not lines: raise HTTPException(422,'盘点批次没有明细')
    missing=[line.sample_code for line in lines if line.physical_quantity is None]
    if missing:
        raise HTTPException(422,'仍有样本未录入实盘量：'+'、'.join(missing[:8]))

    sample_ids=[line.sample_id for line in lines]
    # Ordered row locks make posting mutually exclusive with sample edits, transfer creation
    # and trial launches; whoever commits first wins, the later transaction re-reads.
    samples={sample.id:sample for sample in db.scalars(
        select(Sample).where(Sample.id.in_(sample_ids)).order_by(Sample.id).with_for_update())}
    started=list(db.execute(
        select(Trial.name,Transfer.sample_id).join(Transfer,Trial.transfer_id==Transfer.id)
        .where(Transfer.sample_id.in_(sample_ids),Trial.status.in_(IN_FLIGHT)).order_by(Trial.id)))

    line_reasons={}
    summaries=[]
    for line in lines:
        sample=samples.get(line.sample_id)
        if not sample:
            line_reasons[line.id]='样本记录已不存在'
        elif (sample.quantity_version or 0)!=line.frozen_version:
            line_reasons[line.id]=f'冻结后可用量已变更（冻结 {line.frozen_quantity} → 当前 {sample.quantity}）'
    for line in lines:
        if line.id in line_reasons: summaries.append(f'{line.sample_code} {line_reasons[line.id]}')
    for trial_name,sample_id in started:
        line=next(x for x in lines if x.sample_id==sample_id)
        line_reasons[line.id]=f'已发起演练「{trial_name}」，需等待演练结束后重新盘点'
        summaries.append(f'{line.sample_code} {line_reasons[line.id]}')
    if summaries:
        _reject(db,count,lines,line_reasons,'；'.join(summaries),actor)

    final_quantities={line.sample_id:line.physical_quantity for line in lines}
    transfers=list(db.scalars(
        select(Transfer).where(Transfer.sample_id.in_(sample_ids),Transfer.invalidated==False).order_by(Transfer.id).with_for_update()))
    invalidated=0
    for transfer in transfers:
        final=final_quantities[transfer.sample_id]
        if transfer.quantity>final:
            transfer.invalidated=True
            transfer.invalid_reason=f'盘点 {count.batch_code} 落账后可用量 {final} 份，低于移交量 {transfer.quantity} 份'
            transfer.invalidated_at=datetime.now(timezone.utc)
            transfer.invalidated_by_count_id=count.id
            invalidated+=1

    adjusted=0
    for line in lines:
        sample=samples[line.sample_id]
        if line.physical_quantity!=line.frozen_quantity:
            sample.quantity=line.physical_quantity
            sample.quantity_version=(sample.quantity_version or 0)+1
            line.result='adjusted';adjusted+=1
        else:
            line.result='matched'
    count.status='posted';count.posted_at=datetime.now(timezone.utc);count.posted_by=actor
    count.post_attempts=(count.post_attempts or 0)+1;count.failed_reason=''
    db.add(Audit(actor=actor,action=f'盘点落账 {count.batch_code}（{adjusted} 条调整，{len(lines)-adjusted} 条一致，{invalidated} 条移交申请失效）'))
    db.commit()
    return detail(db,ident)
