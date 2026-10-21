export const API_BASE = '/api';
export function errorMessage(value: unknown): string {
  if (typeof value === 'string') return value;
  if (Array.isArray(value)) return value.map(x => errorMessage(x)).join('; ');
  if (value && typeof value === 'object') {
    const record = value as Record<string, unknown>;
    if (record.msg) return `${Array.isArray(record.loc) ? record.loc.join('.') + ': ' : ''}${record.msg}`;
    if (record.detail) return errorMessage(record.detail);
    if (record.message) return errorMessage(record.message);
    return JSON.stringify(value);
  }
  return 'Request failed';
}
export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body && !(options.body instanceof FormData)) headers.set('Content-Type', 'application/json');
  const response = await fetch(`${API_BASE}${path}`, {...options, headers});
  const data: unknown = await response.json().catch(() => null);
  if (!response.ok) throw new Error(errorMessage(data) + ` (HTTP ${response.status})`);
  return data as T;
}
export const post = <T>(path: string, value?: unknown) => api<T>(path, {method: 'POST', ...(value !== undefined ? {body:JSON.stringify(value)} : {})});
export function progress(tasks: {status: string}[]): number {
  return tasks.length ? Math.round(100 * tasks.filter(t => ['completed','succeeded','accepted'].includes(t.status)).length / tasks.length) : 0;
}
export function artifactUrl(id: string): string {return `${API_BASE}/artifacts/${encodeURIComponent(id)}`;}
