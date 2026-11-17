from sqlalchemy import select
from fastapi import HTTPException
from src.models.entities import Sample
from .common import save
from src.utils.serialization import record
def listing(db): return [record(x) for x in db.scalars(select(Sample).order_by(Sample.id))]
def create(db,data,user): return save(db,Sample(**data.model_dump(),quantity_version=0),user,'创建样本 '+data.code)
def update(db,ident,data,user):
    obj=db.get(Sample,ident,with_for_update=True)
    if not obj: raise HTTPException(404,'样本不存在')
    values=data.model_dump()
    if values['quantity']!=obj.quantity:
        # A version bump makes "changed away and back" visible to frozen stocktake batches.
        obj.quantity_version=(obj.quantity_version or 0)+1
        action='更新样本 '+data.code+f'（可用量 {obj.quantity} → {values["quantity"]}）'
    else:
        action='更新样本 '+data.code
    for k,v in values.items(): setattr(obj,k,v)
    return save(db,obj,user,action)
