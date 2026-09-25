import assert from 'node:assert/strict';
import test from 'node:test';
import { Miniflare, Headers as MFHeaders } from 'miniflare';
import { readRecalls, writeRecalls } from '../src/recalls.ts';

const base = {schema_version:1,audience:'public',commit:'b'.repeat(40),sequence:1,entries:[]};
async function bucketFixture(t) {
  const runtime = new Miniflare({telemetry:{enabled:false},workers:[{config:{type:'worker',name:'recalls',compatibilityDate:'2026-09-03',manifest:{mainModule:'fixture.mjs',modules:{'fixture.mjs':{type:'esm',contents:'export default {fetch(){return new Response("ok")}}'}}},env:{SKILLS_BUCKET:{type:'r2',name:'SKILLS_BUCKET'}},exports:{}}}]});
  t.after(()=>runtime.dispose());
  const raw=await runtime.getR2Bucket('SKILLS_BUCKET');
  return {get:(...a)=>raw.get(...a),put:(key,value,options)=>raw.put(key,value,options?.onlyIf instanceof Headers ? {...options,onlyIf:new MFHeaders(options.onlyIf)}:options)};
}

test('recall CAS retains a monotone set and only one concurrent writer wins',async t=>{
 const bucket=await bucketFixture(t);
 await assert.rejects(readRecalls(bucket,'public'), e=>e.status===503);
 const initial=await writeRecalls(bucket,base,null);
 assert.deepEqual((await readRecalls(bucket,'public')).entries,[]);
 const entry={note_id:'service/example',revision:'sha256:'+'c'.repeat(64)};
 const a=writeRecalls(bucket,{...base,sequence:2,entries:[entry]},initial.etag);
 const b=writeRecalls(bucket,{...base,sequence:2,entries:[{...entry,revision:'sha256:'+'d'.repeat(64)}]},initial.etag);
 const outcomes=await Promise.allSettled([a,b]);
 assert.equal(outcomes.filter(x=>x.status==='fulfilled').length,1);
 const current=await readRecalls(bucket,'public');
 assert.equal(current.entries.length,1);
 const retry=await writeRecalls(bucket,{...base,commit:current.commit,sequence:current.sequence,entries:current.entries},current.etag);
 assert.equal(retry.etag,current.etag,'identical projection replay does not increment sequence');
 await assert.rejects(writeRecalls(bucket,{...base,sequence:3,entries:[]},current.etag), /monotonically/);
 await assert.rejects(writeRecalls(bucket,{...base,audience:'company',sequence:3,entries:current.entries},current.etag), /changed|projection/);
 assert.equal((await readRecalls(bucket,'public')).entries.length,1);
});

test('malformed and cross-audience projections fail closed',async t=>{
 const bucket=await bucketFixture(t);
 await bucket.put('recalls/current.json',JSON.stringify({...base,entries:[{note_id:'service/a',revision:'bad'}]}));
 await assert.rejects(readRecalls(bucket,'public'),e=>e.status===503);
 await bucket.put('recalls/current.json',JSON.stringify(base));
 await assert.rejects(readRecalls(bucket,'company'),e=>e.status===503);
});
