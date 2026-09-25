import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import { Miniflare, Headers as MFHeaders } from 'miniflare';
import { publisherFetch } from '../src/release-publisher.ts';
import { readCurrent, sha256 } from '../src/r2-objects.ts';

const root=new URL('./fixtures/release-v2/',import.meta.url);
const text=await readFile(new URL('inventory.json',root),'utf8');
const inventory=JSON.parse(text);
const identity={commit:inventory.commit,release:inventory.release,inventory_digest:await sha256(text)};
async function setup(t){
 const runtime=new Miniflare({telemetry:{enabled:false},workers:[{config:{type:'worker',name:'publisher',compatibilityDate:'2026-09-03',manifest:{mainModule:'fixture.mjs',modules:{'fixture.mjs':{type:'esm',contents:'export default {fetch(){return new Response("ok")}}'}}},env:{SKILLS_BUCKET:{type:'r2',name:'SKILLS_BUCKET'}},exports:{}}}]});
 t.after(()=>runtime.dispose());
 const raw=await runtime.getR2Bucket('SKILLS_BUCKET');
 const bucket={get:(...a)=>raw.get(...a),head:(...a)=>raw.head(...a),list:(...a)=>raw.list(...a),put:(k,v,o)=>raw.put(k,v,o?.onlyIf instanceof Headers ? {...o,onlyIf:new MFHeaders(o.onlyIf)}:o)};
 const context={bucket,audience:'public',operatorHash:'a'.repeat(64)};
 const call=(path,method='GET',body)=>publisherFetch(new Request(`https://hub.example${path}`,{method,body}),context);
 return {bucket,call,context};
}

test('authenticated publisher accepts golden shared release only after exact upload and verify, retains existing admin routes',async t=>{
 const {bucket,call}=await setup(t);
 let rootPath=`/admin/releases/${inventory.commit}`;
 assert.equal((await call(`${rootPath}/inventory.json`,'PUT',text)).status,201);
 for(const file of inventory.files){let data=await readFile(new URL('objects/'+file.path,root));assert.equal((await call(`${rootPath}/${file.path}`,'PUT',data)).status,201);}
 assert.equal((await call(`${rootPath}/stable/example/notes/anchor.md`,'PUT','other')).status,409);
 const body=JSON.stringify({...identity,expected_etag:null,sequence:1});
 const verification=await call('/admin/verify','POST',body);assert.equal(verification.status,200,await verification.text());
 assert.equal((await call('/admin/promote','POST',body)).status,200);
 assert.equal((await call('/admin/current')).status,200);
 assert.equal((await call(rootPath)).status,200);
 assert.equal((await readCurrent(bucket)).value.commit,inventory.commit);
});

test('shared publisher rejects personal v1 inventory and mismatched audience before R2 write',async t=>{
 const {bucket,call}=await setup(t);
 const v1=structuredClone(inventory);v1.schema_version=1;delete v1.audience;delete v1.note_index;
 for(const skill of v1.skills){delete skill.grade;delete skill.note_refs;}
 const path=`/admin/releases/${inventory.commit}/inventory.json`;
 assert.notEqual((await call(path,'PUT',JSON.stringify(v1))).status,201);
 assert.equal(await bucket.head(`releases/${inventory.commit}/inventory.json`),null);
 const wrong=structuredClone(inventory);wrong.audience='company';
 assert.notEqual((await call(path,'PUT',JSON.stringify(wrong))).status,201);
 assert.equal(await bucket.head(`releases/${inventory.commit}/inventory.json`),null);
});
