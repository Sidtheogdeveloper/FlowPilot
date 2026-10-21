"""Re-evaluate saved trained weights against the untouched held-out prompts."""
import argparse,json
from pathlib import Path
import torch
from transformers import AutoModelForCausalLM,AutoTokenizer
from training.lora import load_adapter
from training.train_adapter import evaluate,read_rows,fingerprint,ROOT

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--model-cache',required=True);parser.add_argument('--adapter',type=Path,required=True);parser.add_argument('--threads',type=int,default=8);args=parser.parse_args()
    torch.set_num_threads(args.threads)
    metadata=json.loads((args.adapter/'adapter_config.json').read_text())
    if metadata['adapter_sha256']!=fingerprint(args.adapter/'adapter.pt'):raise ValueError('Adapter checksum mismatch')
    if metadata['heldout_sha256']!=fingerprint(ROOT/'heldout.jsonl'):raise ValueError('Held-out dataset changed')
    if metadata['base_snapshot']!=Path(args.model_cache).name:raise ValueError('Base revision changed')
    tokenizer=AutoTokenizer.from_pretrained(args.model_cache,local_files_only=True)
    model=AutoModelForCausalLM.from_pretrained(args.model_cache,local_files_only=True,dtype=torch.float32,attn_implementation='sdpa').to('cpu')
    load_adapter(model,args.adapter)
    summary=evaluate(model,tokenizer,read_rows(ROOT/'heldout.jsonl'),args.adapter/'reload-after.json','saved-adapter-reloaded')
    (args.adapter/'reload-evaluation.json').write_text(json.dumps({'after':summary,'adapter_sha256':metadata['adapter_sha256'],'heldout_sha256':metadata['heldout_sha256']},indent=2))
    print(json.dumps(summary),flush=True)
if __name__=='__main__':main()
