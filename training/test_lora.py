"""Verify real adapter updates preserve frozen weights and reload faithfully."""
import json
import torch
from torch import nn
import pytest
from training.lora import attach_lora,adapter_state,load_adapter

class TinyProjection(nn.Module):
    def __init__(self):
        super().__init__();self.q_proj=nn.Linear(5,4);self.v_proj=nn.Linear(5,4)
    def forward(self,x):return self.q_proj(x)+self.v_proj(x)

def test_gradient_updates_adapter_only_and_reload_matches(tmp_path):
    torch.manual_seed(7);base=TinyProjection();clone=TinyProjection();clone.load_state_dict(base.state_dict());base_weights={name:value.clone() for name,value in base.state_dict().items()};attach_lora(base,rank=2,alpha=4)
    params=[p for p in base.parameters() if p.requires_grad];optimizer=torch.optim.SGD(params,lr=.1);inputs=torch.randn(3,5)
    base(inputs).square().mean().backward();optimizer.step()
    assert any(torch.count_nonzero(value) for name,value in adapter_state(base).items() if name.endswith('lora_B'))
    for name,value in base_weights.items():
        parent,param=name.rsplit('.',1);assert torch.equal(value,base.state_dict()[parent+'.base.'+param])
    torch.save(adapter_state(base),tmp_path/'adapter.pt');(tmp_path/'adapter_config.json').write_text(json.dumps({'base_model':'Qwen/Qwen2.5-0.5B-Instruct','rank':2,'alpha':4}))
    load_adapter(clone,tmp_path);assert torch.allclose(base(inputs),clone(inputs),atol=1e-7)

def test_rejects_incorrect_base_or_parameter_keys(tmp_path):
    (tmp_path/'adapter_config.json').write_text(json.dumps({'base_model':'different','rank':2,'alpha':4}))
    with pytest.raises(ValueError,match='base model'):load_adapter(TinyProjection(),tmp_path)
    (tmp_path/'adapter_config.json').write_text(json.dumps({'base_model':'Qwen/Qwen2.5-0.5B-Instruct','rank':2,'alpha':4}));torch.save({},tmp_path/'adapter.pt')
    with pytest.raises(ValueError,match='parameter keys'):load_adapter(TinyProjection(),tmp_path)
