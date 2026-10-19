export function formatResearchPercent(value: unknown): string {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 1
    ? `${(value * 100).toFixed(1)}%`
    : '未知'
}

/** Transport completion is not proof that the underlying research completed. */
export function canSimulateResearch(value: unknown): boolean {
  if (typeof value !== 'object' || value === null) return false
  const report = value as Record<string, unknown>
  const decision = typeof report.decision === 'object' && report.decision !== null
    ? report.decision as Record<string, unknown> : undefined
  const statuses = [report.analysis_status, decision?.analysis_status].filter(status => status !== undefined)
  return statuses.length > 0 && statuses.every(status => status === 'completed')
}
