import { assertCommit, assertDigest, readCurrent, readVerifiedObject, releaseKey, ReleaseError, sha256 } from "./r2-objects.ts";
import type { FileDigest, ReleaseIdentity } from "./r2-objects.ts";
import { readRecalls } from "./recalls.ts";

export type NoteRef = { note_id: string; revision: string };
export type NoteEntry = NoteRef & { kind: "procedure" | "lesson"; service_id: string; grade: "trial" | "stable"; path: string; uri: string; file_digest: string; size: number };
export type NoteIndex = { schema_version: 1; audience: "public" | "company"; commit: string; notes: NoteEntry[] };
export type SkillResource = FileDigest & { uri: string };
export type SkillEntry = { uri: string; frontmatter: Record<string, unknown> & { name: string; description: string }; resources: SkillResource[]; grade?: "trial" | "stable"; note_refs?: NoteRef[]; origin?: "code" };
export type ReleaseFile = FileDigest & { path: string; uri?: string };
export type ReleaseInventory = {
  schema_version: 1 | 2;
  audience?: "public" | "company";
  note_index?: FileDigest & { path: string };
  commit: string;
  release: string;
  skills: SkillEntry[];
  files: ReleaseFile[];
  aliases: Record<string, string>;
};
export type Snapshot = { identity: ReleaseIdentity; inventory: ReleaseInventory; files: Map<string, ReleaseFile>; noteIndex?: NoteIndex; visibleNotes: NoteEntry[]; includeTrial?: boolean; recalled?: Set<string>; recalledUris?: Set<string> };

export function canonicalUri(uri: unknown): string {
  if (typeof uri !== "string" || /%2e|%2f|%5c|%00/i.test(uri) || uri.includes("\\")) throw new ReleaseError("Invalid skill URI", 400);
  let url: URL;
  try { url = new URL(uri); } catch { throw new ReleaseError("Invalid skill URI", 400); }
  if (url.protocol !== "skill:" || url.host !== "gisul" || url.username || url.password || url.search || url.hash) throw new ReleaseError("Invalid skill URI", 400);
  let segments: string[];
  try { segments = url.pathname.slice(1).split("/").map(decodeURIComponent); } catch { throw new ReleaseError("Invalid skill URI", 400); }
  if (segments.length < 2 || segments.some(part => !part || part === "." || part === ".." || /[\\/\x00-\x1f\x7f]/.test(part)) || `skill://gisul/${segments.map(encodeURIComponent).join("/")}` !== uri) throw new ReleaseError("Noncanonical skill URI", 400);
  return uri;
}

export async function manifestDigest(entry: SkillEntry): Promise<string> {
  return sha256(JSON.stringify(entry.resources.map(({ uri, digest, size }) => ({ uri, digest, size })).sort((a, b) => a.uri < b.uri ? -1 : a.uri > b.uri ? 1 : 0)));
}

export function parseInventory(text: string, identity: ReleaseIdentity, audience?: "personal" | "public" | "company", noteIndex?: NoteIndex): Snapshot {
  const inventory = JSON.parse(text) as ReleaseInventory;
  if (!inventory || ![1, 2].includes(inventory.schema_version) || inventory.commit !== identity.commit || typeof inventory.release !== "string" || !inventory.release || inventory.release !== identity.release || !Array.isArray(inventory.skills) || !Array.isArray(inventory.files) || !inventory.aliases || typeof inventory.aliases !== "object" || Array.isArray(inventory.aliases)) throw new ReleaseError("Release inventory identity or structure is invalid");
  if (audience && (audience === "personal" ? inventory.schema_version !== 1 : inventory.schema_version !== 2 || inventory.audience !== audience)) throw new ReleaseError("Inventory audience or schema mismatch");
  if (inventory.schema_version === 2) {
    if (!["public", "company"].includes(inventory.audience ?? "") || !inventory.note_index || inventory.note_index.path !== "note-index.json") throw new ReleaseError("Invalid shared note index");
    assertDigest(inventory.note_index);
  }
  const paths = new Set<string>();
  const files = new Map<string, ReleaseFile>();
  for (const file of inventory.files) {
    assertDigest(file);
    releaseKey(identity.commit, file.path);
    if (paths.has(file.path) || ["inventory.json", "complete.json"].includes(file.path)) throw new ReleaseError("Duplicate or reserved inventory path");
    if (inventory.schema_version === 2 && (file.path.startsWith("personal/") || file.path.startsWith("_private/") || (file.path !== "release.json" && file.path !== "note-index.json" && !/^(stable|trial|code)\//.test(file.path)))) throw new ReleaseError("Invalid shared release path");
    paths.add(file.path);
    if (file.uri !== undefined) {
      canonicalUri(file.uri);
      if (files.has(file.uri)) throw new ReleaseError("Duplicate resource URI in inventory");
      files.set(file.uri, file);
    }
  }
  if (inventory.schema_version === 2) {
    const listed = inventory.files.find(f => f.path === "note-index.json");
    if (!listed || listed.uri !== undefined || listed.digest !== inventory.note_index!.digest || listed.size !== inventory.note_index!.size || !inventory.files.some(f => f.path === "release.json" && f.uri === undefined)) throw new ReleaseError("Shared note index or release digest differs from inventory");
  }
  const skills = new Set<string>();
  const referenced = new Set<string>();
  for (const entry of inventory.skills) {
    canonicalUri(entry.uri);
    if (!entry.uri.endsWith("/SKILL.md") || skills.has(entry.uri) || typeof entry.frontmatter?.name !== "string" || entry.frontmatter.name !== decodeURIComponent(entry.uri.split("/").at(-2)!) || typeof entry.frontmatter.description !== "string" || !entry.frontmatter.description || !Array.isArray(entry.resources) || entry.resources.length > 512) throw new ReleaseError("Invalid skill manifest");
    if (inventory.schema_version === 2 && (entry.grade !== "trial" && entry.grade !== "stable" || !Array.isArray(entry.note_refs) || (entry.origin === "code" ? entry.note_refs.length !== 0 || !entry.uri.startsWith("skill://gisul/code/") : entry.origin !== undefined || !entry.note_refs.length || !entry.uri.startsWith(`skill://gisul/commons/${entry.grade}/`)))) throw new ReleaseError("Invalid shared skill grade or note references");
    skills.add(entry.uri);
    const root = entry.uri.slice(0, -8);
    const resources = new Set<string>();
    let size = 0;
    for (const resource of entry.resources) {
      canonicalUri(resource.uri);
      assertDigest(resource);
      const file = files.get(resource.uri);
      if (!resource.uri.startsWith(root) || resources.has(resource.uri) || !file || file.digest !== resource.digest || file.size !== resource.size) throw new ReleaseError("Skill manifest differs from release inventory");
      if (inventory.schema_version === 2 && (file.path !== resource.uri.slice("skill://gisul/commons/".length) && (entry.origin !== "code" || !file.path.startsWith("code/")))) throw new ReleaseError("Shared resource path differs from its URI");
      resources.add(resource.uri);
      referenced.add(resource.uri);
      size += resource.size;
    }
    if (!resources.has(entry.uri) || size > 16 * 1024 * 1024) throw new ReleaseError("Incomplete or oversized skill manifest");
  }
  if (referenced.size !== files.size) throw new ReleaseError("Inventory exposes a resource outside all skill manifests");
  for (const [from, to] of Object.entries(inventory.aliases)) {
    canonicalUri(from); canonicalUri(to);
    if (!from.endsWith("/SKILL.md") || !to.endsWith("/SKILL.md") || skills.has(from)) throw new ReleaseError("Invalid or shadowing skill alias");
    resolveAlias(inventory, from);
  }
  const snapshot: Snapshot = { identity, inventory, files, visibleNotes: [] };
  if (noteIndex) validateNoteIndex(snapshot, noteIndex);
  return snapshot;
}

export function validateNoteIndex(snapshot: Snapshot, index: NoteIndex): void {
  const { inventory, files } = snapshot;
  if (inventory.schema_version !== 2 || index?.schema_version !== 1 || index.audience !== inventory.audience || index.commit !== inventory.commit || !Array.isArray(index.notes)) throw new ReleaseError("Invalid note index");
  const refs = new Set<string>();
  for (const skill of inventory.skills) {
    if (skill.origin === "code") continue;
    for (const ref of skill.note_refs ?? []) {
      if (typeof ref.note_id !== "string" || !/^sha256:[a-f0-9]{64}$/.test(ref.revision)) throw new ReleaseError("Invalid note reference");
      const key = `${ref.note_id}\0${ref.revision}`;
      if (refs.has(key)) throw new ReleaseError("Duplicate note reference");
      refs.add(key);
    }
  }
  const found = new Set<string>();
  for (const note of index.notes) {
    if (!note || typeof note.note_id !== "string" || !/^(service|failure)\/[a-z0-9][a-z0-9._-]*$/.test(note.note_id) || note.kind !== (note.note_id.startsWith("service/") ? "procedure" : "lesson") || typeof note.service_id !== "string" || !/^[a-z0-9][a-z0-9._-]*$/.test(note.service_id) || !/^sha256:[a-f0-9]{64}$/.test(note.revision) || !["stable", "trial"].includes(note.grade)) throw new ReleaseError("Invalid note index entry");
    const key = `${note.note_id}\0${note.revision}`;
    if (found.has(key) || !refs.has(key)) throw new ReleaseError("Note index reference membership differs");
    found.add(key);
    const file = files.get(note.uri);
    const skill = inventory.skills.find(s => s.grade === note.grade && s.note_refs?.some(r => r.note_id === note.note_id && r.revision === note.revision));
    if (!file || !skill || !skill.resources.some(r => r.uri === note.uri) || file.path !== note.path || file.digest !== note.file_digest || file.size !== note.size || !note.path.startsWith(`${note.grade}/${note.service_id}/notes/`) || !note.uri.startsWith(skill.uri.slice(0, -8) + "notes/")) throw new ReleaseError("Note index path or grade membership differs");
  }
  if (found.size !== refs.size) throw new ReleaseError("Note index reference set differs");
  snapshot.noteIndex = index;
  snapshot.visibleNotes = index.notes;
}

export function resolveAlias(inventory: ReleaseInventory, uri: string): string {
  canonicalUri(uri);
  let target = uri;
  const visited = new Set<string>();
  while (Object.hasOwn(inventory.aliases, target)) {
    if (visited.has(target) || visited.size >= 8) throw new ReleaseError("Cyclic or excessive skill aliases");
    visited.add(target);
    target = inventory.aliases[target];
  }
  if (!inventory.skills.some(entry => entry.uri === target)) throw new ReleaseError("Unknown skill URI", 404);
  return target;
}

export async function readSnapshot(bucket: R2Bucket, pin?: unknown): Promise<Snapshot> {
  let identity: ReleaseIdentity;
  if (pin === undefined) {
    const current = await readCurrent(bucket);
    if (!current) throw new ReleaseError("No release is currently published", 503);
    identity = current.value;
  } else {
    assertCommit(pin);
    const complete = await bucket.get(releaseKey(pin, "complete.json"));
    if (!complete) throw new ReleaseError("Unknown or incomplete pinned release", 404);
    identity = await complete.json<ReleaseIdentity>();
    if (identity?.commit !== pin) throw new ReleaseError("Pinned release identity differs from its key");
  }
  return readInventory(bucket, identity);
}

export async function readInventory(bucket: R2Bucket, identity: ReleaseIdentity, audience?: "personal" | "public" | "company"): Promise<Snapshot> {
  assertCommit(identity.commit);
  if (!/^sha256:[a-f0-9]{64}$/.test(identity.inventory_digest)) throw new ReleaseError("Invalid inventory digest");
  const object = await bucket.get(releaseKey(identity.commit, "inventory.json"));
  if (!object || object.size > 8 * 1024 * 1024) throw new ReleaseError("Missing or oversized release inventory");
  const bytes = await object.arrayBuffer();
  if (await sha256(bytes) !== identity.inventory_digest) throw new ReleaseError("Release inventory failed digest verification");
  const snapshot = parseInventory(new TextDecoder("utf-8", { fatal: true }).decode(bytes), identity, audience);
  if (snapshot.inventory.schema_version === 2) {
    const index = snapshot.inventory.note_index!;
    const text = new TextDecoder("utf-8", { fatal: true }).decode(await readVerifiedObject(bucket, releaseKey(identity.commit, index.path), index));
    validateNoteIndex(snapshot, JSON.parse(text) as NoteIndex);
  }
  return snapshot;
}

export async function visibleSnapshot(bucket: R2Bucket, options: { audience: "personal" | "public" | "company"; pin?: string; includeTrial: boolean }): Promise<Snapshot> {
  const original = await readSnapshot(bucket, options.pin);
  if (options.audience === "personal") {
    if (original.inventory.schema_version !== 1) throw new ReleaseError("Personal inventory audience mismatch", 404);
    return original;
  }
  if (original.inventory.schema_version !== 2 || original.inventory.audience !== options.audience || !original.noteIndex) throw new ReleaseError("Shared inventory audience mismatch", 404);
  const projection = await readRecalls(bucket, options.audience);
  const recalled = new Set(projection.entries.map(e => `${e.note_id}\0${e.revision}`));
  const visibleNotes = original.noteIndex.notes.filter(n => (options.includeTrial || n.grade === "stable") && !recalled.has(`${n.note_id}\0${n.revision}`));
  const revokedSkills = original.inventory.skills.filter(s => (options.includeTrial || s.grade === "stable") && s.note_refs?.some(r => recalled.has(`${r.note_id}\0${r.revision}`)));
  const recalledUris = new Set(revokedSkills.flatMap(s => s.resources.map(r => r.uri)));
  const skills = original.inventory.skills.filter(s => (options.includeTrial || s.grade === "stable") && !revokedSkills.includes(s));
  const allowed = new Set(skills.flatMap(s => s.resources.map(r => r.uri)));
  const files = new Map([...original.files].filter(([uri]) => allowed.has(uri)));
  const aliases = Object.fromEntries(Object.entries(original.inventory.aliases).filter(([from]) => skills.some(s => s.uri === resolveAlias(original.inventory, from))));
  return { ...original, inventory: { ...original.inventory, skills, aliases, files: original.inventory.files.filter(f => !f.uri || allowed.has(f.uri)) }, files, visibleNotes, includeTrial: options.includeTrial, recalled, recalledUris };
}

export async function readNoteResource(bucket: R2Bucket, snapshot: Snapshot, noteId: string, revision?: string): Promise<{ note_id: string; revision: string; grade: "trial" | "stable"; file_digest: string; text: string }> {
  let note: NoteEntry | undefined;
  for (const candidate of snapshot.visibleNotes) if (candidate.note_id === noteId && (revision === undefined || candidate.revision === revision) && (!note || candidate.grade === "trial")) note = candidate;
  note ??= snapshot.noteIndex?.notes.find(n => n.note_id === noteId && (revision === undefined || n.revision === revision) && (snapshot.includeTrial || n.grade === "stable"));
  if (!note) throw new ReleaseError("Unknown note", 404);
  if (snapshot.recalled?.has(`${note.note_id}\0${note.revision}`)) throw new ReleaseError("REVISION_RECALLED", 410);
  if (!snapshot.visibleNotes.includes(note)) throw new ReleaseError("Unknown note", 404);
  const text = new TextDecoder("utf-8", { fatal: true }).decode(await readVerifiedObject(bucket, releaseKey(snapshot.identity.commit, note.path), { digest: note.file_digest, size: note.size }));
  return { note_id: note.note_id, revision: note.revision, grade: note.grade, file_digest: note.file_digest, text };
}

const MIME: Record<string, string> = { md: "text/markdown", markdown: "text/markdown", txt: "text/plain", json: "application/json", yaml: "text/yaml", yml: "text/yaml", sh: "text/x-shellscript", bash: "text/x-shellscript", py: "text/x-python", js: "text/javascript", mjs: "text/javascript", ts: "text/plain", css: "text/css", html: "text/html", svg: "image/svg+xml", png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg", gif: "image/gif", webp: "image/webp", pdf: "application/pdf" };
export function mimeType(uri: string): string {
  const extension = uri.split(".").at(-1)!.toLowerCase();
  return Object.hasOwn(MIME, extension) ? MIME[extension] : "application/octet-stream";
}

export async function readResource(bucket: R2Bucket, snapshot: Snapshot, uri: string): Promise<Record<string, unknown>> {
  canonicalUri(uri);
  const file = snapshot.files.get(uri);
  if (snapshot.recalledUris?.has(uri)) throw new ReleaseError("REVISION_RECALLED", 410);
  if (!file) throw new ReleaseError("Unknown resource URI in this release", 404);
  const bytes = await readVerifiedObject(bucket, releaseKey(snapshot.identity.commit, file.path), file);
  const mime = mimeType(uri);
  if (mime.startsWith("text/") || ["application/json", "image/svg+xml"].includes(mime)) return { uri, mimeType: mime, text: new TextDecoder("utf-8", { fatal: true }).decode(bytes) };
  let binary = "";
  const view = new Uint8Array(bytes);
  for (let offset = 0; offset < view.length; offset += 8192) binary += String.fromCharCode(...view.subarray(offset, offset + 8192));
  return { uri, mimeType: mime, blob: btoa(binary) };
}

export function readDirectory(snapshot: Snapshot, uri: string): Array<{ uri: string; name: string; mimeType: string }> {
  canonicalUri(uri);
  if (snapshot.files.has(uri)) throw new ReleaseError("Resource is a file, not a directory", 400);
  const children = new Map<string, { uri: string; name: string; mimeType: string }>();
  for (const file of snapshot.files.keys()) if (file.startsWith(`${uri}/`)) {
    const suffix = file.slice(uri.length + 1);
    const part = suffix.split("/")[0];
    const child = `${uri}/${part}`;
    children.set(child, { uri: child, name: decodeURIComponent(part), mimeType: suffix.includes("/") ? "inode/directory" : mimeType(child) });
  }
  if (!children.size) throw new ReleaseError("Unknown directory URI in this release", 404);
  return [...children.values()].sort((a, b) => a.uri < b.uri ? -1 : a.uri > b.uri ? 1 : 0);
}
