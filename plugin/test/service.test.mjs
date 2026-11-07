import test from "node:test";
import assert from "node:assert/strict";
import { normalizeServiceUrl, parseResponse } from "../src/lib/service.js";

test("accepts and normalizes an HTTPS origin", () => {
  assert.equal(normalizeServiceUrl("https://signup.example.net/"), "https://signup.example.net");
});

test("rejects plain HTTP", () => {
  assert.throws(() => normalizeServiceUrl("http://signup.example.net"), /HTTPS/);
});

test("rejects credentials, paths, query strings and fragments", () => {
  assert.throws(() => normalizeServiceUrl("https://user:pass@signup.example.net"), /origin/);
  assert.throws(() => normalizeServiceUrl("https://signup.example.net/api"), /origin/);
  assert.throws(() => normalizeServiceUrl("https://signup.example.net/?token=x"), /origin/);
  assert.throws(() => normalizeServiceUrl("https://signup.example.net/#section"), /origin/);
});

test("parses successful JSON responses", () => {
  assert.deepEqual(parseResponse({ ok: true, body: "{\"requests\":[]}" }), { requests: [] });
});

test("surfaces companion API errors", () => {
  assert.throws(() => parseResponse({ ok: false, status: 401, body: "{\"detail\":\"Unauthorized\"}" }), /Unauthorized/);
});
