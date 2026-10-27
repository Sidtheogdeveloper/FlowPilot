from pydantic import BaseModel, Field, ConfigDict
from typing import Literal
class Resources(BaseModel):
    model_config=ConfigDict(extra='forbid')
    cpu:int=Field(default=1,ge=1,le=64)
    memory_mb:int=Field(default=128,ge=1,le=262144)
class Task(BaseModel):
    model_config=ConfigDict(extra='forbid')
    id:str=Field(pattern=r'^[a-z][a-z0-9_]{0,39}$')
    handler:str
    version:Literal['1']='1'
    parameters:dict=Field(default_factory=dict)
    inputs:dict[str,str]
    depends_on:list[str]=Field(default_factory=list)
    resources:Resources=Field(default_factory=Resources)
class Plan(BaseModel):
    model_config=ConfigDict(extra='forbid')
    tasks:list[Task]=Field(min_length=1,max_length=256)
class PlanRequest(BaseModel):
    dataset_id:str|None=None
    dataset_ids:list[str]|None=Field(default=None,min_length=1,max_length=8)
    generic:bool=False
    request:str=Field(min_length=3,max_length=2000)
    planner:Literal['template','huggingface']='template'
    policy:Literal['greedy','heft','bounded']='greedy'
class FollowupRequest(BaseModel):
    reuse_outputs:bool=True
    dataset_ids:list[str]|None=Field(default=None,min_length=1,max_length=8)
    model_config=ConfigDict(extra='forbid')
    request:str=Field(min_length=3,max_length=2000)
    planner:Literal['template','huggingface']|None=None
    policy:Literal['greedy','heft','bounded']|None=None
