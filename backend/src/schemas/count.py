from pydantic import BaseModel, Field
class CountInput(BaseModel):
    batch_code: str=Field(min_length=2,max_length=80)
    note: str=Field(default='',max_length=300)
class CountLineInput(BaseModel):
    line_id: int=Field(gt=0)
    physical_quantity: int|None=Field(default=None,ge=0,le=100000)
    note: str=Field(default='',max_length=300)
class CountLinesInput(BaseModel):
    lines: list[CountLineInput]
