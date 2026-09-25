import assert from "node:assert/strict";
import test from "node:test";
import { Headers as MiniflareHeaders, Miniflare } from "miniflare";
import { putImmutableObject, readCurrent, readVerifiedObject, releaseKey, sha256, switchCurrent, verifyInventory } from "../src/r2-objects.ts";

const bytes = text => new TextEncoder().encode(text).buffer;
const commit = "a".repeat(40);
const identity = letter => ({ commit: letter.repeat(40), release: `fixture.${letter}`, inventory_digest: `sha256:${letter.repeat(64)}` });

async function bucketFixture(t) {
  const runtime = new Miniflare({ telemetry: { enabled: false }, workers: [{ config: {
    type: "worker", name: "r2-test", compatibilityDate: "2026-09-03",
    manifest: { mainModule: "fixture.mjs", modules: { "fixture.mjs": { type: "esm", contents: "export default { fetch() { return new Response('fixture'); } }" } } },
    env: { SKILLS_BUCKET: { type: "r2", name: "SKILLS_BUCKET" } }, exports: {},
  } }] });
  t.after(() => runtime.dispose());
  const bucket = await runtime.getR2Bucket("SKILLS_BUCKET");
  // Miniflare's Node RPC bridge serializes its own Headers class. The R2
  // implementation still evaluates these conditions in the real local runtime.
  return {
    get: (...args) => bucket.get(...args),
    list: (...args) => bucket.list(...args),
    delete: (...args) => bucket.delete(...args),
    put: (key, value, options) => bucket.put(key, value, options?.onlyIf instanceof Headers ? { ...options, onlyIf: new MiniflareHeaders(options.onlyIf) } : options),
  };
}

test("immutable R2 uploads allow exact retries and refuse replacement bytes", async t => {
  const bucket = await bucketFixture(t);
  const first = await putImmutableObject(bucket, commit, "skills/flow/SKILL.md", bytes("first"));
  assert.equal(first.created, true);
  const retry = await putImmutableObject(bucket, commit, "skills/flow/SKILL.md", bytes("first"));
  assert.equal(retry.created, false);
  await assert.rejects(putImmutableObject(bucket, commit, "skills/flow/SKILL.md", bytes("other")), /digest verification/);
  assert.equal(new TextDecoder().decode(await readVerifiedObject(bucket, first.key, first)), "first");
  assert.equal(await readCurrent(bucket), null);
});

test("release paths reject traversal, encoded paths and cross-release keys", async t => {
  const bucket = await bucketFixture(t);
  for (const path of ["", "../current.json", "/current.json", "x/../y", "x//y", "x\\y", "x/%2e%2e/y", "x\0y"]) assert.throws(() => releaseKey(commit, path), /Invalid release object path/);
  assert.throws(() => releaseKey("a".repeat(7), "x"), /full Git commit/);
  await assert.rejects(verifyInventory(bucket, commit, [{ key: `releases/${"b".repeat(40)}/x`, digest: await sha256("x"), size: 1 }]), /out-of-release/);
});

test("inventory checks stored bytes and complete object membership before activation", async t => {
  const bucket = await bucketFixture(t);
  const file = await putImmutableObject(bucket, commit, "skills/flow/SKILL.md", bytes("valid"));
  await verifyInventory(bucket, commit, [file]);
  await bucket.put(file.key, bytes("other"), { customMetadata: { sha256: file.digest } });
  await assert.rejects(verifyInventory(bucket, commit, [file]), /digest verification/, "matching metadata cannot hide changed bytes");
  await bucket.put(file.key, bytes("valid"));
  await bucket.put(releaseKey(commit, "extra.txt"), "unlisted");
  await assert.rejects(verifyInventory(bucket, commit, [file]), /differ from release inventory/);
  await bucket.delete(releaseKey(commit, "extra.txt"));
  await bucket.delete(file.key);
  await assert.rejects(verifyInventory(bucket, commit, [file]), /differ from release inventory/);
  assert.equal(await readCurrent(bucket), null);
});

test("only one concurrent pointer switch wins, including initial publication", async t => {
  const bucket = await bucketFixture(t);
  const first = await Promise.allSettled([switchCurrent(bucket, identity("a"), null, 1), switchCurrent(bucket, identity("b"), null, 2)]);
  assert.equal(first.filter(r => r.status === "fulfilled").length, 1);
  const current = await readCurrent(bucket);
  const next = await Promise.allSettled([switchCurrent(bucket, identity("c"), current.etag, 3), switchCurrent(bucket, identity("d"), current.etag, 4)]);
  assert.equal(next.filter(r => r.status === "fulfilled").length, 1);
  assert.equal((await readCurrent(bucket)).value.revision, 2);
});

test("late old deployments cannot reverse a promotion or a deliberate rollback", async t => {
  const bucket = await bucketFixture(t);
  await switchCurrent(bucket, identity("a"), null, 1);
  await switchCurrent(bucket, identity("b"), (await readCurrent(bucket)).etag, 4);
  const current = await readCurrent(bucket);
  await assert.rejects(switchCurrent(bucket, identity("a"), current.etag, 3), /older than/);
  assert.deepEqual(await readCurrent(bucket), current);
  const rolledBack = await switchCurrent(bucket, identity("a"), current.etag, 6, "rollback");
  assert.equal(rolledBack.commit, identity("a").commit);
  assert.equal(rolledBack.high_water.sequence, 4);
  assert.equal(rolledBack.high_water.commit, identity("b").commit);
  assert.equal(rolledBack.sequence, 6);
  await assert.rejects(switchCurrent(bucket, identity("c"), (await readCurrent(bucket)).etag, 5), /older than/);
  const repeatedRollback = await switchCurrent(bucket, identity("a"), (await readCurrent(bucket)).etag, 8, "rollback");
  assert.equal(repeatedRollback.sequence, 8, "same-release rollback still fences older operations");
  assert.equal(repeatedRollback.high_water.sequence, 4);
  await assert.rejects(switchCurrent(bucket, identity("c"), (await readCurrent(bucket)).etag, 7), /older than/);
  await assert.rejects(switchCurrent(bucket, identity("c"), current.etag, 5), /Current release changed/);
  assert.equal((await readCurrent(bucket)).value.commit, identity("a").commit);
});

test("upload or verification failures leave the previously published pointer intact", async t => {
  const bucket = await bucketFixture(t);
  await switchCurrent(bucket, identity("a"), null, 1);
  const current = await readCurrent(bucket);
  const file = await putImmutableObject(bucket, "b".repeat(40), "skills/flow/SKILL.md", bytes("new"));
  await assert.rejects(putImmutableObject(bucket, "b".repeat(40), "skills/flow/SKILL.md", bytes("bad")), /digest verification/);
  await assert.rejects(verifyInventory(bucket, "b".repeat(40), [file, { key: releaseKey("b".repeat(40), "missing.md"), size: 0, digest: await sha256("") }]), /differ from release inventory/);
  assert.deepEqual(await readCurrent(bucket), current);
});
