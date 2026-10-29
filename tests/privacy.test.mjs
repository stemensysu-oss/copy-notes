import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { rejectionReason } from '../privacy.mjs';
const cases = JSON.parse(readFileSync(new URL('./privacy-cases.json', import.meta.url), 'utf8'));
for (const test of cases) {
  assert.equal(rejectionReason(test.text, {source: 'reader.exe', requireSource: true, ...test.context}) === null,
    test.allowed, test.name);
}
assert.equal(rejectionReason('普通文本。'.repeat(20000)), 'oversized');
console.log(`Privacy: ${cases.length + 1} cases passed`);
