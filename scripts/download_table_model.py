"""Download the pinned public model; never download weights during ordinary uploads."""
from pathlib import Path
import hashlib
import urllib.request

ROOT = Path(__file__).resolve().parents[1] / '.models'
NAME = 'Qwen3-4B-Instruct-2507-Q4_K_M.gguf'
REVISION = '4edb920b6f14e3b9284d4502a6485103d72cde05'
EXPECTED = '8cdb57cbb880d313736a9bc4e3d3d2485f145b5e19cf33783746e753e82641fc'
URL = f'https://huggingface.co/lmstudio-community/Qwen3-4B-Instruct-2507-GGUF/resolve/{REVISION}/{NAME}'

def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()

def main():
    ROOT.mkdir(exist_ok=True)
    target = ROOT / NAME
    if not target.exists() or digest(target) != EXPECTED:
        temporary = ROOT / (NAME + '.part')
        print('Downloading the pinned quantized model; this may take several minutes.')
        with urllib.request.urlopen(URL, timeout=120) as response, temporary.open('wb') as output:
            for chunk in iter(lambda: response.read(1024 * 1024), b''):
                output.write(chunk)
        if digest(temporary) != EXPECTED:
            raise RuntimeError('Model checksum mismatch; the partial download was not activated.')
        temporary.replace(target)
    (ROOT / 'Modelfile').write_text(f'FROM /models/{NAME}\nPARAMETER num_ctx 8192\n', encoding='utf-8')
    print('Model checksum verified and .models/Modelfile created.')

if __name__ == '__main__':
    main()
