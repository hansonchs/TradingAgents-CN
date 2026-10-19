/** Detail response shared by report views; historical availability can be unknown. */
export interface AnalysisReport {
  id: string
  analysis_id: string
  stock_symbol: string
  stock_name?: string
  model_info?: string
  analysis_date: string
  status: string
  created_at: string
  updated_at?: string
  analysts: string[]
  research_depth?: number | string
  summary?: string
  reports: Record<string, string>
  recommendation?: string
  confidence_score?: number | null
  risk_level?: string
  key_points?: string[]
  execution_time?: number
  tokens_used?: number
  analysis_status?: 'completed' | 'unavailable' | 'unknown'
}
