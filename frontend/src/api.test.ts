import {afterEach, describe, expect, it, vi} from 'vitest';
import {api, artifactUrl, errorMessage, progress} from './api';
afterEach(()=>vi.unstubAllGlobals());
describe('user-facing workflow state',()=>{
  it('counts accepted/completed work, without counting running work',()=>expect(progress([{status:'completed'},{status:'running'},{status:'accepted'}])).toBe(67));
  it('handles empty workflow',()=>expect(progress([])).toBe(0));
  it('preserves validator locations',()=>expect(errorMessage({detail:[{loc:['body','request'],msg:'Required'}]})).toBe('body.request: Required'));
  it('escapes artifact IDs',()=>expect(artifactUrl('one/two')).toBe('/api/artifacts/one%2Ftwo'));
  it('surfaces backend rejection rather than returning an apparent success',async()=>{
    vi.stubGlobal('fetch',vi.fn().mockResolvedValue(new Response(JSON.stringify({detail:'Cycle detected'}),{status:422})));
    await expect(api('/plans')).rejects.toThrow('Cycle detected (HTTP 422)');
  });
  it('lets browser set multipart boundary',async()=>{
    const mocked=vi.fn().mockResolvedValue(new Response('{"id":"d"}',{status:200}));vi.stubGlobal('fetch',mocked);
    await api('/datasets',{method:'POST',body:new FormData()});
    expect((mocked.mock.calls[0][1].headers as Headers).has('Content-Type')).toBe(false);
  });
});
