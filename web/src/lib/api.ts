// Client of the sky service API v1 (docs/plan/05-architecture.md, 5.8).

export interface Health {
  ok: boolean;
  service: string;
  version: string;
  started_at: string;
  uptime_s: number;
  product_versions: Record<string, string>;
}

export async function getHealth(signal?: AbortSignal): Promise<Health> {
  const r = await fetch('/api/v1/health', { signal, cache: 'no-store' });
  if (!r.ok) throw new Error(`service answered ${r.status}`);
  return (await r.json()) as Health;
}
