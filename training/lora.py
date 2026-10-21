"""Minimal LoRA for Qwen attention projections; no PEFT dependency required."""
import math
import torch
from torch import nn

class LoRALinear(nn.Module):
    def __init__(self, base, rank=8, alpha=16):
        super().__init__()
        self.base=base
        self.scale=alpha/rank
        self.lora_A=nn.Parameter(torch.empty(rank,base.in_features,dtype=base.weight.dtype,device=base.weight.device))
        self.lora_B=nn.Parameter(torch.zeros(base.out_features,rank,dtype=base.weight.dtype,device=base.weight.device))
        nn.init.kaiming_uniform_(self.lora_A,a=math.sqrt(5))
    def forward(self,x):
        return self.base(x)+(x @ self.lora_A.T @ self.lora_B.T)*self.scale

def attach_lora(model,rank=8,alpha=16):
    model.requires_grad_(False)
    targets=[]
    for name,module in list(model.named_modules()):
        if isinstance(module,nn.Linear) and name.rsplit('.',1)[-1] in {'q_proj','v_proj'}:
            parent_name,separator,child=name.rpartition('.')
            parent=model.get_submodule(parent_name) if separator else model
            setattr(parent,child,LoRALinear(module,rank,alpha))
            targets.append(name)
    if not targets:raise ValueError('Model has no supported attention projections')
    return targets

def adapter_state(model):
    return {name:value.detach().cpu() for name,value in model.state_dict().items() if name.endswith(('lora_A','lora_B'))}

def load_adapter(model,path):
    from pathlib import Path
    import json
    path=Path(path)
    metadata=json.loads((path/'adapter_config.json').read_text())
    if metadata['base_model']!='Qwen/Qwen2.5-0.5B-Instruct':raise ValueError('Adapter base model mismatch')
    attach_lora(model,metadata['rank'],metadata['alpha'])
    state=torch.load(path/'adapter.pt',map_location='cpu',weights_only=True)
    expected=adapter_state(model)
    if set(state)!=set(expected):raise ValueError('Adapter parameter keys do not match model')
    for name,value in state.items():
        if value.shape!=expected[name].shape or not torch.isfinite(value).all():raise ValueError('Invalid adapter parameter: '+name)
    model.load_state_dict(state,strict=False)
    return metadata
