import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import { Miniflare, Headers as MFHeaders } from 'miniflare';
import { parseInventory, visibleSnapshot, readNoteResource, readResource, readDirectory, resolveAlias } from '../src/release-reader.ts';
import { releaseKey, sha256, putImmutableObject } from '../src/r2-objects.ts';

const root = new URL('./fixtures/release-v2/', import.meta.url);
const inventoryText = await readFile(new URL('inventory.json', root), 'utf8');
const inventory = JSON.parse(inventoryText);
const identity = { commit: inventory.commit, release: inventory.release, inventory_digest: await sha256(inventoryText) };
const recalls = JSON.parse(await readFile(new URL('recalls.json', root), 'utf8'));

async function fixture(t) {
  const runtime = new Miniflare({ telemetry: { enabled: false }, workers: [{ config: {
    type: 'worker', name: 'reader-test', compatibilityDate: '2026-09-03',
    manifest: { mainModule: 'fixture.mjs', modules: { 'fixture.mjs': { type: 'esm', contents: 'export default { fetch() { return new Response("ok") } }' } } },
    env: { SKILLS_BUCKET: { type: 'r2', name: 'SKILLS_BUCKET' } }, exports: {},
  } }] });
  t.after(() => runtime.dispose());
  const raw = await runtime.getR2Bucket('SKILLS_BUCKET');
  const bucket = { get: (...a) => raw.get(...a), list: (...a) => raw.list(...a), head: (...a) => raw.head(...a), put: (k,v,o) => raw.put(k,v,o?.onlyIf instanceof Headers ? {...o,onlyIf:new MFHeaders(o.onlyIf)} : o) };
  for (const file of inventory.files) await putImmutableObject(bucket, inventory.commit, file.path, Uint8Array.from(await readFile(new URL('objects/' + file.path, root))).buffer);
  await putImmutableObject(bucket, inventory.commit, 'inventory.json', new TextEncoder().encode(inventoryText).buffer);
  await putImmutableObject(bucket, inventory.commit, 'complete.json', new TextEncoder().encode(JSON.stringify(identity)).buffer);
  await bucket.put('current.json', JSON.stringify({ ...identity, revision: 1, sequence: 1, high_water: { commit: identity.commit, sequence: 1 }, previous: null, operation: 'promote', activated_at: '2026-09-25T00:00:00Z' }));
  await bucket.put('recalls/current.json', JSON.stringify(recalls));
  return bucket;
}

test('v2 fixture validates exact grade/note/resource memberships; rejects cross-package references', async () => {
  const index = JSON.parse(await readFile(new URL('objects/note-index.json',root),'utf8'));
  assert.equal(parseInventory(inventoryText, identity, 'public',index).inventory.skills.length, 2);
  const changed = structuredClone(inventory);
  changed.skills[1].note_refs[0] = changed.skills[0].note_refs[0];
  assert.throws(() => parseInventory(JSON.stringify(changed), identity,'public',index), /note|membership|reference/i);
  const moved = structuredClone(inventory);
  moved.files.find(f => f.path.includes('candidate.md')).path = 'stable/example/notes/candidate.md';
  assert.throws(() => parseInventory(JSON.stringify(moved), identity,'public',index), /path|grade|membership/i);
  const noIndex = structuredClone(inventory);
  delete noIndex.note_index;
  assert.throws(() => parseInventory(JSON.stringify(noIndex), identity), /note|index/i);
});

test('stable note remains valid while a trial revision of the same note is pending', async () => {
  const index = JSON.parse(await readFile(new URL('objects/note-index.json',root),'utf8'));
  const update = structuredClone(inventory);
  update.skills[1].note_refs[0].note_id = 'service/anchor';
  index.notes[2].note_id = 'service/anchor';
  const snapshot = parseInventory(JSON.stringify(update), identity,'public',index);
  assert.deepEqual(snapshot.visibleNotes.filter(n=>n.note_id==='service/anchor').map(n=>n.grade),['stable','trial']);
});

test('shared v1 and personal v2 are rejected, not silently treated as stable', async t => {
  const bucket = await fixture(t);
  const v1 = structuredClone(inventory);
  v1.schema_version = 1; delete v1.audience; delete v1.note_index;
  for (const skill of v1.skills) { delete skill.grade; delete skill.note_refs; }
  await assert.rejects(visibleSnapshot(bucket, { audience: 'personal', includeTrial: false }), /personal|audience|inventory/i);
  assert.throws(() => parseInventory(JSON.stringify(v1), identity, 'public'), /personal|v1|schema/i);
});

test('v1 personal inventory remains readable without a grade or note index', () => {
  const path='personal/demo/SKILL.md', uri='skill://gisul/personal/demo/SKILL.md';
  const legacy={schema_version:1,commit:inventory.commit,release:'personal.old',skills:[{uri,frontmatter:{name:'demo',description:'Private demo'},resources:[{uri,size:3,digest:'sha256:'+'a'.repeat(64)}]}],files:[{path,uri,size:3,digest:'sha256:'+'a'.repeat(64)}],aliases:{}};
  const snapshot=parseInventory(JSON.stringify(legacy),{...identity,release:legacy.release},'personal');
  assert.equal(snapshot.inventory.skills[0].uri,uri);
  assert.deepEqual(snapshot.visibleNotes,[]);
  assert.throws(()=>parseInventory(JSON.stringify(legacy),{...identity,release:legacy.release},'public'), /audience|schema/i);
});

test('shared inventory refuses orphan note index and a forged personal origin', async () => {
  const changed=structuredClone(inventory);
  changed.note_index.digest='sha256:'+'a'.repeat(64);
  const index=JSON.parse(await readFile(new URL('objects/note-index.json',root),'utf8'));
  assert.throws(()=>parseInventory(JSON.stringify(changed),identity,'public',index),/index|digest/i);
  const personal=structuredClone(inventory);
  personal.skills[1].origin='personal';
  assert.throws(()=>parseInventory(JSON.stringify(personal),identity,'public',index),/origin|shared/i);
});

test('personal v1 release is served from its own current pointer', async t => {
  const bucket=await fixture(t), commit='c'.repeat(40), uri='skill://gisul/personal/demo/SKILL.md';
  const content='---\nname: demo\ndescription: Private demo\n---\n\nPrivate\n';
  const f=await putImmutableObject(bucket,commit,'personal/demo/SKILL.md',new TextEncoder().encode(content).buffer);
  const legacy={schema_version:1,commit,release:'personal.old',skills:[{uri,frontmatter:{name:'demo',description:'Private demo'},resources:[{uri,digest:f.digest,size:f.size}]}],files:[{path:'personal/demo/SKILL.md',uri,digest:f.digest,size:f.size}],aliases:{}};
  const data=JSON.stringify(legacy), id={commit,release:legacy.release,inventory_digest:await sha256(data)};
  await putImmutableObject(bucket,commit,'inventory.json',new TextEncoder().encode(data).buffer);
  await bucket.put('current.json',JSON.stringify({...id,revision:2,sequence:2,high_water:{commit,sequence:2},previous:identity,operation:'promote',activated_at:'2026-09-25T01:00:00Z'}));
  const personal=await visibleSnapshot(bucket,{audience:'personal',includeTrial:false});
  assert.equal(personal.inventory.skills[0].uri,uri);
  assert.match((await readResource(bucket,personal,uri)).text,/Private/);
  await assert.rejects(visibleSnapshot(bucket,{audience:'public',includeTrial:true}),/audience/i);
});

test('default, trial opt-in, direct URI, aliases and pinned reads share visibility; recalled package stays hidden', async t => {
  const bucket = await fixture(t);
  const stable = await visibleSnapshot(bucket, { audience: 'public', includeTrial: false });
  assert.equal(stable.inventory.skills.length, 0, 'recalled stable note hides its whole package');
  assert.deepEqual(stable.visibleNotes.map(n => n.note_id), ['service/anchor'], 'unrecalled original note survives its package');
  const text = await readNoteResource(bucket, stable, 'service/anchor');
  assert.match(text.text, /anchor content/);
  await assert.rejects(readNoteResource(bucket, stable, 'service/recalled'), e => e.status === 410);
  await assert.rejects(readResource(bucket, stable, inventory.skills[0].uri), e => e.status === 410);
  assert.throws(() => resolveAlias(stable.inventory, 'skill://gisul/alias/trial/SKILL.md'), e => e.status === 404);
  await assert.rejects(readResource(bucket,stable,inventory.skills[1].uri),e=>e.status===404);
  await assert.rejects(readNoteResource(bucket,stable,'service/candidate'),e=>e.status===404);
  const trial = await visibleSnapshot(bucket, { audience: 'public', includeTrial: true, pin: inventory.commit });
  assert.deepEqual(trial.inventory.skills.map(s => s.grade), ['trial']);
  assert.equal(resolveAlias(trial.inventory, 'skill://gisul/alias/trial/SKILL.md'), inventory.skills[1].uri);
  assert.match((await readResource(bucket, trial, inventory.skills[1].uri)).text, /trial example/);
  assert.deepEqual(readDirectory(trial, 'skill://gisul/commons/trial/example').map(x=>x.name), ['SKILL.md','notes']);
  assert.deepEqual(trial.visibleNotes.map(n=>n.note_id), ['service/anchor','service/candidate']);
  const chained=structuredClone(inventory);
  chained.aliases['skill://gisul/alias/chain/SKILL.md']='skill://gisul/alias/trial/SKILL.md';
  const changedText=JSON.stringify(chained), changedIdentity={...identity,inventory_digest:await sha256(changedText)};
  await bucket.put(releaseKey(inventory.commit,'inventory.json'),changedText);
  await bucket.put(releaseKey(inventory.commit,'complete.json'),JSON.stringify(changedIdentity));
  const chainedSnapshot=await visibleSnapshot(bucket,{audience:'public',includeTrial:true,pin:inventory.commit});
  assert.equal(resolveAlias(chainedSnapshot.inventory,'skill://gisul/alias/chain/SKILL.md'),inventory.skills[1].uri);
  await bucket.put(releaseKey(inventory.commit,'inventory.json'),inventoryText);
  await bucket.put(releaseKey(inventory.commit,'complete.json'),JSON.stringify(identity));
  await bucket.put('recalls/current.json', JSON.stringify({...recalls,sequence:2,entries:[...recalls.entries,{note_id:'service/candidate',revision:inventory.skills[1].note_refs[0].revision}]}));
  const pinned = await visibleSnapshot(bucket, { audience: 'public', includeTrial: true, pin: inventory.commit });
  await assert.rejects(readNoteResource(bucket, pinned, 'service/candidate'), e=>e.status===410);
  const defaultPinned=await visibleSnapshot(bucket,{audience:'public',includeTrial:false,pin:inventory.commit});
  await assert.rejects(readResource(bucket,defaultPinned,inventory.skills[1].uri),e=>e.status===404);
  await assert.rejects(readNoteResource(bucket,defaultPinned,'service/candidate'),e=>e.status===404);
});

test('shared reads fail closed on absent or corrupt recall projection, wrong audience and tampered note bytes', async t => {
  const bucket = await fixture(t);
  await assert.rejects(visibleSnapshot(bucket,{audience:'company',includeTrial:true}), /audience/i);
  await bucket.put('recalls/current.json','{broken');
  await assert.rejects(visibleSnapshot(bucket,{audience:'public',includeTrial:true}), /recall|projection/i);
  await bucket.put('recalls/current.json',JSON.stringify(recalls));
  await bucket.put(releaseKey(inventory.commit, inventory.files.find(f=>f.path.includes('anchor.md')).path),'tampered');
  const snapshot = await visibleSnapshot(bucket,{audience:'public',includeTrial:true});
  await assert.rejects(readNoteResource(bucket,snapshot,'service/anchor'), /size|digest/i);
});
