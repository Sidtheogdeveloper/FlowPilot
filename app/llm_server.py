"""Local, token-free OpenAI-style adapter for an Apache-2.0 Hugging Face model."""
import os
from pathlib import Path
import threading
import time

ROOT = Path(os.getenv('MODEL_CACHE_ROOT', str(Path(__file__).resolve().parent.parent / 'data'))) 
os.environ.setdefault('HF_HOME', str(ROOT / 'hf_cache'))
os.environ.setdefault('HF_HUB_DISABLE_XET', '1')
os.environ.setdefault('HF_HUB_DOWNLOAD_TIMEOUT', '120')
os.environ.setdefault('HF_HUB_ETAG_TIMEOUT', '60')

import torch
from transformers import AutoTokenizer, AutoModelForCausalLM
from fastapi import FastAPI
from pydantic import BaseModel
import uvicorn

MODEL = os.getenv('HF_MODEL', 'Qwen/Qwen2.5-0.5B-Instruct')
MODEL_REVISION=os.getenv('HF_MODEL_REVISION','7ae557604adf67be50417f59c2c2f167def9a775' if MODEL=='Qwen/Qwen2.5-0.5B-Instruct' else None)
torch.set_num_threads(int(os.getenv('LLM_THREADS','4')))
print('Loading free local Hugging Face model:', MODEL, flush=True)
tokenizer = AutoTokenizer.from_pretrained(MODEL,revision=MODEL_REVISION)
model = AutoModelForCausalLM.from_pretrained(MODEL,revision=MODEL_REVISION,dtype=torch.float32).to('cpu').eval()
adapter_metadata=None
training_record={'fine_tuned':False,'method':None,'steps':0,'adapter_sha256':None,'trainable_parameters':0}
adapter_path=os.getenv('HF_ADAPTER_PATH')
if adapter_path:
    import json
    from training.lora import load_adapter
    if MODEL!='Qwen/Qwen2.5-0.5B-Instruct':raise ValueError('This trained adapter requires the Qwen 0.5B base model')
    evaluation=json.loads((Path(adapter_path)/'evaluation.json').read_text(encoding='utf-8'))
    import hashlib
    adapter_sha=hashlib.sha256((Path(adapter_path)/'adapter.pt').read_bytes()).hexdigest()
    if evaluation.get('adapter_sha256')!=adapter_sha:raise ValueError('Evaluation is not bound to these adapter weights')
    before=evaluation.get('before');after=evaluation.get('after')
    if not before or not after or after['count']!=before['count'] or after['valid']<before['valid'] or after['exact']<before['exact']:
        raise ValueError('Adapter evaluation is missing or regresses the held-out pilot')
    if evaluation.get('activation_recommended') is not None and not evaluation['activation_recommended']:
        raise ValueError('General-table adapter has not met its semantic correctness activation gate')
    adapter_metadata=load_adapter(model,adapter_path)
    if adapter_metadata.get('base_snapshot')!=MODEL_REVISION:raise ValueError('Adapter base model revision mismatch')
    if adapter_metadata.get('adapter_sha256')!=adapter_sha:raise ValueError('Adapter metadata checksum mismatch')
    training_record={'fine_tuned':True,'method':adapter_metadata['method'],'steps':adapter_metadata['steps'],'adapter_sha256':adapter_sha,'trainable_parameters':adapter_metadata['trainable_parameters']}
    training_record['task']=adapter_metadata.get('task','retail')
    model.eval()
    print('Loaded task-specific trained LoRA adapter; pilot comparison:',before['exact'],'->',after['exact'],flush=True)
print('Local model ready on CPU', flush=True)
quantization=None
if os.getenv('LLM_INT8','0')=='1':
    if adapter_path:raise ValueError('Use the unquantized runtime for trained adapters')
    targets={name for name,module in model.named_modules() if isinstance(module,torch.nn.Linear) and name!='lm_head'}
    model=torch.ao.quantization.quantize_dynamic(model,qconfig_spec=targets,dtype=torch.qint8,inplace=True)
    quantization='CPU dynamic int8; tied output and embedding float32'
    print('CPU int8 projections ready',flush=True)
app = FastAPI(title='Local free Hugging Face model')
lock = threading.Lock()

class Completion(BaseModel):
    messages: list[dict[str, str]]
    max_tokens: int = 2048
    temperature: float = 0
    model: str = MODEL

@app.get('/health')
def health():
    return {'model': MODEL,'revision':MODEL_REVISION,'quantization':quantization, 'device': 'cpu', 'ready': True, 'hosted_api_used': False,'adapter_loaded':adapter_metadata is not None,'training':training_record}

@app.post('/v1/chat/completions')
def completion(request: Completion):
    started = time.monotonic()
    with lock, torch.inference_mode():
        prompt = tokenizer.apply_chat_template(request.messages, tokenize=False, add_generation_prompt=True,enable_thinking=False)
        encoded = tokenizer(prompt, return_tensors='pt')
        output = model.generate(**encoded, max_new_tokens=min(request.max_tokens, 2048), do_sample=False, pad_token_id=tokenizer.eos_token_id)
        generated = output[0, encoded['input_ids'].shape[1]:]
        content = tokenizer.decode(generated, skip_special_tokens=True)
    return {'model': MODEL, 'training':training_record,'choices': [{'message': {'role': 'assistant', 'content': content}}], 'usage': {'prompt_tokens': encoded['input_ids'].shape[1], 'completion_tokens': len(generated)}, 'elapsed_seconds': time.monotonic()-started}

if __name__ == '__main__':
    uvicorn.run(app, host=os.getenv('LLM_HOST', '127.0.0.1'), port=int(os.getenv('LLM_PORT','8001')))


