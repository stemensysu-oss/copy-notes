// Shared conservative policy for macOS. Rejected contents never leave memory.
import { readFileSync } from 'node:fs';
export const rules = JSON.parse(readFileSync(new URL('./privacy-rules.json', import.meta.url), 'utf8'));
const patterns = rules.patterns.map(p => new RegExp(p, 'i'));
export function rejectionReason(text, context = {}) {
  text = text.normalize('NFKC').replace(/[\u200b-\u200f\u202a-\u202e\u2060\ufeff]/g, '');
  if (context.concealed) return 'protected-format';
  const source = (context.source ?? '').toLowerCase();
  if (context.requireSource && !source) return 'unknown-source';
  if (rules.blocked_sources.some(s => source.includes(s))) return 'blocked-source';
  if (rules.blocked_titles.some(s => (context.title ?? '').toLowerCase().includes(s))) return 'sensitive-window';
  if (!text.trim()) return 'empty';
  if (Buffer.byteLength(text, 'utf8') > rules.max_bytes) return 'oversized';
  if (patterns.some(p => p.test(text))) return 'credential-pattern';
  // Reject any account-like line, even when pasted alongside an ordinary paragraph.
  const lines = text.split(/\r?\n/).map(s => s.trim()).filter(Boolean);
  if (lines.some(s => /^[\w.@+\-/\\=!:;#$%^&*()]+$/.test(s) || /^[\u3400-\u9fff]{1,8}$/.test(s))) return 'identifier-like';
  if (text.trim().length < rules.min_characters) return 'short-text';
  if (!/[。！？.!?]/.test(text)) return 'not-prose';
  if (!/[\u3400-\u9fff]/.test(text) && text.trim().split(/\s+/).length < 5) return 'not-prose';
  return null;
}
