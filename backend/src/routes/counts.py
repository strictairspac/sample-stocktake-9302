from fastapi import APIRouter,Depends
from src.config.database import db_session
from src.utils.security import current,writer
from src.schemas.count import CountInput,CountLinesInput
from src.services import counts as service
router=APIRouter()
@router.get('/counts')
def listing(db=Depends(db_session),user=Depends(current)): return service.listing(db)
@router.get('/counts/{ident}')
def detail(ident:int,db=Depends(db_session),user=Depends(current)): return service.detail(db,ident)
@router.post('/counts',status_code=201)
def freeze(data:CountInput,db=Depends(db_session),user=Depends(writer)): return service.freeze(db,data,user)
@router.put('/counts/{ident}/lines')
def enter_lines(ident:int,data:CountLinesInput,db=Depends(db_session),user=Depends(writer)): return service.enter_lines(db,ident,data,user)
@router.post('/counts/{ident}/post')
def post(ident:int,db=Depends(db_session),user=Depends(writer)): return service.post(db,ident,user)
