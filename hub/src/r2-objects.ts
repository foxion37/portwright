export type FileDigest = { digest: string; size: number };
export type InventoryEntry = FileDigest & { key: string };

export class ReleaseError extends Error {
  readonly status: number;
  constructor(message: string, status = 409) { super(message); this.status = status; }
}

export function assertCommit(commit: unknown): asserts commit is string {
  if (typeof commit !== "string" || !/^[a-f0-9]{40}$/.test(commit)) throw new ReleaseError("A full Git commit is required", 400);
}

export function assertDigest(file: FileDigest): void {
  if (!/^sha256:[a-f0-9]{64}$/.test(file.digest) || !Number.isSafeInteger(file.size) || file.size < 0 || file.size > 16 * 1024 * 1024) {
    throw new ReleaseError("Invalid file digest or size", 400);
  }
}

export function releaseKey(commit: string, relative: string): string {
  assertCommit(commit);
  if (!relative || relative.includes("\\") || relative.includes("%") || /[\x00-\x1f\x7f]/.test(relative) || relative.split("/").some(part => !part || part === "." || part === "..")) {
    throw new ReleaseError("Invalid release object path", 400);
  }
  return `releases/${commit}/${relative}`;
}

export async function sha256(bytes: ArrayBuffer | Uint8Array<ArrayBuffer> | string): Promise<string> {
  const input = typeof bytes === "string" ? new TextEncoder().encode(bytes) : bytes;
  return `sha256:${Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", input)), b => b.toString(16).padStart(2, "0")).join("")}`;
}

export async function readVerifiedObject(bucket: R2Bucket, key: string, expected: FileDigest): Promise<ArrayBuffer> {
  assertDigest(expected);
  const object = await bucket.get(key);
  if (!object || object.size !== expected.size) throw new ReleaseError(`Missing or wrong-sized release object: ${key}`);
  const bytes = await object.arrayBuffer();
  if (await sha256(bytes) !== expected.digest) throw new ReleaseError(`Release object failed digest verification: ${key}`);
  return bytes;
}

export async function putImmutableObject(bucket: R2Bucket, commit: string, relative: string, bytes: ArrayBuffer): Promise<{ created: boolean; key: string } & FileDigest> {
  const key = releaseKey(commit, relative);
  const digest = await sha256(bytes);
  const file = { digest, size: bytes.byteLength };
  assertDigest(file);
  const written = await bucket.put(key, bytes, {
    onlyIf: new Headers({ "If-None-Match": "*" }),
    sha256: digest.slice(7),
    customMetadata: { sha256: digest },
    httpMetadata: { cacheControl: "private, no-store" },
  });
  if (!written) await readVerifiedObject(bucket, key, file);
  return { created: written !== null, key, ...file };
}

export async function verifyInventory(bucket: R2Bucket, commit: string, inventory: InventoryEntry[], excluded: string[] = [], inspect?: (file: InventoryEntry, bytes: ArrayBuffer) => void): Promise<void> {
  assertCommit(commit);
  const prefix = `releases/${commit}/`;
  const expected = new Set<string>();
  for (const file of inventory) {
    assertDigest(file);
    if (!file.key.startsWith(prefix) || releaseKey(commit, file.key.slice(prefix.length)) !== file.key || expected.has(file.key)) {
      throw new ReleaseError("Inventory has duplicate or out-of-release objects", 400);
    }
    expected.add(file.key);
  }
  const excludedKeys = new Set(excluded.map(relative => releaseKey(commit, relative)));
  if ([...excludedKeys].some(key => expected.has(key))) throw new ReleaseError("Excluded object also appears in inventory", 400);
  let cursor: string | undefined;
  const actual = new Set<string>();
  do {
    const page = await bucket.list({ prefix, cursor });
    for (const object of page.objects) if (!excludedKeys.has(object.key)) actual.add(object.key);
    cursor = page.truncated ? page.cursor : undefined;
    if (page.truncated && !cursor) throw new ReleaseError("R2 returned an incomplete inventory page");
  } while (cursor);
  if (actual.size !== expected.size || [...actual].some(key => !expected.has(key))) throw new ReleaseError("R2 objects differ from release inventory");
  // Read back the uploaded bytes, not uploader-provided object metadata.
  for (const file of inventory) {
    const bytes = await readVerifiedObject(bucket, file.key, file);
    inspect?.(file, bytes);
  }
}

export type ReleaseIdentity = { commit: string; release: string; inventory_digest: string };
export type CurrentRelease = ReleaseIdentity & {
  revision: number;
  sequence: number;
  high_water: { commit: string; sequence: number };
  previous: ReleaseIdentity | null;
  operation: "promote" | "rollback";
  activated_at: string;
};

function assertIdentity(value: ReleaseIdentity): void {
  assertCommit(value.commit);
  if (typeof value.release !== "string" || !value.release || !/^sha256:[a-f0-9]{64}$/.test(value.inventory_digest)) throw new ReleaseError("Invalid release identity", 400);
}

export async function readCurrent(bucket: R2Bucket): Promise<{ value: CurrentRelease; etag: string } | null> {
  const object = await bucket.get("current.json");
  if (!object) return null;
  const value = await object.json<CurrentRelease>();
  assertIdentity(value);
  assertCommit(value.high_water?.commit);
  if (!Number.isSafeInteger(value.revision) || value.revision < 1 || !Number.isSafeInteger(value.high_water.sequence) || value.high_water.sequence < 1 || !Number.isSafeInteger(value.sequence) || value.sequence < value.high_water.sequence) throw new ReleaseError("Invalid current release pointer");
  return { value, etag: object.etag };
}

// Call only after full release verification. The expected ETag makes the single
// pointer write conditional, including when no pointer has been published yet.
export async function switchCurrent(bucket: R2Bucket, candidate: ReleaseIdentity, expectedEtag: string | null, sequence: number, operation: "promote" | "rollback" = "promote"): Promise<CurrentRelease> {
  assertIdentity(candidate);
  if (operation !== "promote" && operation !== "rollback") throw new ReleaseError("Unknown pointer operation", 400);
  if (!Number.isSafeInteger(sequence) || sequence < 1) throw new ReleaseError("A positive deployment sequence is required", 400);
  const current = await readCurrent(bucket);
  if (current && current.value.sequence === sequence && current.value.operation === operation && current.value.commit === candidate.commit && current.value.release === candidate.release && current.value.inventory_digest === candidate.inventory_digest) return current.value;
  if ((current?.etag ?? null) !== expectedEtag) throw new ReleaseError("Current release changed; reload before publishing");
  if (operation === "rollback" && !current) throw new ReleaseError("There is no release to roll back");
  if (current && sequence <= current.value.sequence) throw new ReleaseError("Deployment sequence is older than the last pointer operation");
  const value: CurrentRelease = {
    ...candidate,
    revision: (current?.value.revision ?? 0) + 1,
    sequence,
    high_water: operation === "rollback" ? current!.value.high_water : { commit: candidate.commit, sequence },
    previous: current ? { commit: current.value.commit, release: current.value.release, inventory_digest: current.value.inventory_digest } : null,
    operation,
    activated_at: new Date().toISOString(),
  };
  const result = await bucket.put("current.json", JSON.stringify(value), {
    onlyIf: expectedEtag === null ? new Headers({ "If-None-Match": "*" }) : { etagMatches: expectedEtag },
    httpMetadata: { contentType: "application/json", cacheControl: "private, no-store" },
  });
  if (!result) throw new ReleaseError("Another deployment switched the current release");
  return value;
}
