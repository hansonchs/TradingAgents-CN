import DOMPurify from 'dompurify'

/** Research and news text is untrusted even when it has passed Markdown parsing. */
export function sanitizeHtml(value: unknown): string {
  return typeof value === 'string' ? DOMPurify.sanitize(value) : ''
}

/** Only web source links may be opened from provider-controlled news. */
export function safeExternalUrl(value: unknown): string | undefined {
  if (typeof value !== 'string') return undefined
  try {
    const url = new URL(value)
    return ['http:', 'https:'].includes(url.protocol) ? url.href : undefined
  } catch {
    return undefined
  }
}
