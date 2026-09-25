import { assertCommit, ReleaseError } from './r2-objects.ts';

export type RecallEntry = { note_id: string; revision: string };
export type RecallSnapshot = { schema_version: 1; audience: string; commit: string; sequence: number; entries: RecallEntry[]; etag?: string };
const key = 'recalls/current.json';
const digest = /^sha256:[a-f0-9]{64}$/;
const noteId = /^(service|failure)\/[a-z0-9][a-z0-9._-]*$/;

function validate(value: RecallSnapshot, audience: string): void {
  if (!value || value.schema_version !== 1 || value.audience !== audience || !['public','company'].includes(audience) || !Number.isSafeInteger(value.sequence) || value.sequence < 1 || !Array.isArray(value.entries)) throw new ReleaseError('Invalid recall projection', 503);
  assertCommit(value.commit);
  const seen = new Set<string>();
  for (const entry of value.entries) {
    if (!entry || typeof entry.note_id !== 'string' || !noteId.test(entry.note_id) || typeof entry.revision !== 'string' || !digest.test(entry.revision)) throw new ReleaseError('Invalid recall projection', 503);
    const ref = `${entry.note_id}\0${entry.revision}`;
    if (seen.has(ref)) throw new ReleaseError('Duplicate recall entry', 503);
    seen.add(ref);
  }
}

export async function readRecalls(bucket: R2Bucket, audience: string): Promise<RecallSnapshot> {
  const object = await bucket.get(key);
  if (!object || object.size > 1024 * 1024) throw new ReleaseError('Recall projection unavailable', 503);
  let value: RecallSnapshot;
  try { value = await object.json<RecallSnapshot>(); validate(value, audience); }
  catch { throw new ReleaseError('Invalid recall projection', 503); }
  return { ...value, etag: object.etag };
}

export async function writeRecalls(bucket: R2Bucket, snapshot: RecallSnapshot, expectedEtag: string | null): Promise<RecallSnapshot> {
  validate(snapshot, snapshot?.audience);
  const previous = expectedEtag === null ? null : await readRecalls(bucket, snapshot.audience);
  if ((previous?.etag ?? null) !== expectedEtag) throw new ReleaseError('Recall projection changed');
  const old = new Set(previous?.entries.map(e => `${e.note_id}\0${e.revision}`) ?? []);
  const next = new Set(snapshot.entries.map(e => `${e.note_id}\0${e.revision}`));
  if (previous && snapshot.sequence === previous.sequence && snapshot.commit === previous.commit && old.size === next.size && [...old].every(ref => next.has(ref))) return previous;
  if ([...old].some(ref => !next.has(ref)) || (previous && snapshot.sequence <= previous.sequence)) throw new ReleaseError('Recall projection must advance monotonically');
  const { etag: _unused, ...body } = snapshot;
  const object = await bucket.put(key, JSON.stringify(body), { onlyIf: expectedEtag === null ? new Headers({ 'If-None-Match':'*' }) : { etagMatches:expectedEtag }, httpMetadata: { contentType:'application/json', cacheControl:'private, no-store' } });
  if (!object) throw new ReleaseError('Recall projection changed');
  return { ...body, etag:object.etag };
}
