/**
 * Exception → reason-key lookup with SDK-version-presence guards.
 *
 * Spec §4.3. Classes absent in older openai SDK versions fall through to
 * ``uncategorized``. Mirror of Python ``_taxonomy.py``.
 */

// taxonomy.ts will be implemented in Task 3.4
export function mapExceptionToReason(_err: unknown): string {
  return "uncategorized";
}
