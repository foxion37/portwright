import assert from "node:assert/strict";
import test from "node:test";
import { corsHeaders } from "../src/http.ts";

const origin = "https://client.example";

const cases = [
  { name: "adds Origin when Vary is absent", vary: null, expected: "Origin" },
  { name: "adds Origin when Vary is empty", vary: "", expected: "Origin" },
  { name: "preserves an existing field", vary: "Accept-Encoding", expected: "Accept-Encoding, Origin" },
  { name: "preserves multiple fields and their formatting", vary: "Accept-Encoding,  Accept-Language", expected: "Accept-Encoding,  Accept-Language, Origin" },
  { name: "does not duplicate Origin", vary: "Origin", expected: "Origin" },
  { name: "recognizes lowercase origin", vary: "origin", expected: "origin" },
  { name: "recognizes mixed-case Origin among fields", vary: "Accept-Encoding,\t oRiGiN , Accept-Language", expected: "Accept-Encoding,\t oRiGiN , Accept-Language" },
  { name: "matches whole tokens", vary: "X-Origin", expected: "X-Origin, Origin" },
  { name: "preserves wildcard Vary", vary: "*", expected: "*" },
  { name: "preserves wildcard among fields", vary: "Accept-Encoding, *", expected: "Accept-Encoding, *" },
];

for (const { name, vary, expected } of cases) {
  for (const withOrigin of [true, false]) {
    test(`${name} (${withOrigin ? "with" : "without"} request Origin)`, () => {
      const upstreamHeaders = new Headers({ "x-upstream": "preserved" });
      if (vary !== null) upstreamHeaders.set("vary", vary);
      const requestHeaders = new Headers({ authorization: "Bearer test-token" });
      if (withOrigin) requestHeaders.set("origin", origin);

      const request = new Request("https://worker.example/mcp", {
        method: "POST",
        headers: requestHeaders,
      });
      const result = new Headers(upstreamHeaders);
      new Headers(corsHeaders(request, upstreamHeaders.get("vary"))).forEach((value, key) => result.set(key, value));
      assert.equal(result.get("vary"), withOrigin ? expected : vary);
      assert.equal(result.get("access-control-allow-origin"), withOrigin ? origin : null);
      assert.equal(result.get("x-upstream"), "preserved");
      assert.equal(upstreamHeaders.get("vary"), vary);
    });
  }
}
