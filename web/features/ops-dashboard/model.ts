/** Ops-dashboard wire model (RIC-720, D3).
 *
 * Parses the read-only aggregation payload from ``/api/ops`` into typed,
 * validated shapes. Numbers are coerced defensively so a misbehaving backend
 * degrades to zero instead of crashing the dashboard.
 */

export interface PlanRow {
  plan_id: string;
  units: number;
  revenue_fen: number;
}

export interface DailyPoint {
  date: string;
  value: number;
}

export interface SalesView {
  units_sold: number;
  revenue_fen: number;
  plan_breakdown: PlanRow[];
  daily_revenue_fen: DailyPoint[];
}

export interface CoachingView {
  sessions: number;
  revenue_fen: number;
  daily_revenue_fen: DailyPoint[];
}

export interface RenewalView {
  total_buyers: number;
  renewal_buyers: number;
  renewal_rate_pct: number;
}

export interface FunnelView {
  visitors: number;
  active: number;
  engaged_sessions: number;
  converted: number;
  activation_rate_pct: number;
  engagement_rate_pct: number;
  conversion_rate_pct: number;
}

export interface MasteryTotals {
  path_count: number;
  kp_count: number;
  mastered_kp_count: number;
  mastery_rate_pct: number;
  avg_mastery_pct: number;
}

export interface MasteryModule {
  module_name: string;
  kp_count: number;
  mastered: number;
  avg_mastery: number;
}

export interface MasteryView {
  paths: Array<{ book_id: string }>;
  modules: MasteryModule[];
  totals: MasteryTotals;
}

export interface LearnerRow {
  user_id: string;
  username: string;
  role: string;
  created_at: string;
  disabled: boolean;
}

export interface DashboardMetric {
  key: string;
  label: string;
  value: string;
  unit: string;
}

export interface OpsDashboard {
  range: string;
  generated_at: string;
  sales: SalesView;
  coaching: CoachingView;
  renewal: RenewalView;
  funnel: FunnelView;
  mastery: MasteryView;
  metrics: DashboardMetric[];
  learners: LearnerRow[];
  sessions: unknown[];
}

function num(token: unknown, fallback = 0): number {
  const n = typeof token === "number" ? token : Number(token);
  return Number.isFinite(n) ? n : fallback;
}

function str(token: unknown, fallback = ""): string {
  return typeof token === "string" ? token : fallback;
}

function bool(token: unknown): boolean {
  return token === true || token === "true" || token === 1 || token === "1";
}

function parsePlanRows(token: unknown): PlanRow[] {
  if (!Array.isArray(token)) return [];
  return token.map((row) => ({
    plan_id: str((row as Record<string, unknown>)?.plan_id),
    units: num((row as Record<string, unknown>)?.units),
    revenue_fen: num((row as Record<string, unknown>)?.revenue_fen),
  }));
}

function parseDaily(token: unknown): DailyPoint[] {
  if (!Array.isArray(token)) return [];
  return token.map((row) => ({
    date: str((row as Record<string, unknown>)?.date),
    value: num((row as Record<string, unknown>)?.value),
  }));
}

function parseFunnel(token: unknown): FunnelView {
  const t = (token ?? {}) as Record<string, unknown>;
  return {
    visitors: num(t.visitors),
    active: num(t.active),
    engaged_sessions: num(t.engaged_sessions),
    converted: num(t.converted),
    activation_rate_pct: num(t.activation_rate_pct),
    engagement_rate_pct: num(t.engagement_rate_pct),
    conversion_rate_pct: num(t.conversion_rate_pct),
  };
}

function parseMastery(token: unknown): MasteryView {
  const t = (token ?? {}) as Record<string, unknown>;
  const totals = (t.totals ?? {}) as Record<string, unknown>;
  const modules = Array.isArray(t.modules)
    ? t.modules.map((m) => {
        const mm = m as Record<string, unknown>;
        return {
          module_name: str(mm.module_name),
          kp_count: num(mm.kp_count),
          mastered: num(mm.mastered),
          avg_mastery: num(mm.avg_mastery),
        };
      })
    : [];
  const paths = Array.isArray(t.paths)
    ? t.paths.map((p) => ({ book_id: str((p as Record<string, unknown>)?.book_id) }))
    : [];
  return {
    paths,
    modules,
    totals: {
      path_count: num(totals.path_count),
      kp_count: num(totals.kp_count),
      mastered_kp_count: num(totals.mastered_kp_count),
      mastery_rate_pct: num(totals.mastery_rate_pct),
      avg_mastery_pct: num(totals.avg_mastery_pct),
    },
  };
}

function parseLearners(token: unknown): LearnerRow[] {
  if (!Array.isArray(token)) return [];
  return token.map((row) => {
    const r = row as Record<string, unknown>;
    return {
      user_id: str(r.user_id),
      username: str(r.username),
      role: str(r.role, "user"),
      created_at: str(r.created_at),
      disabled: bool(r.disabled),
    };
  });
}

export function parseOpsDashboard(payload: unknown): OpsDashboard {
  const t = (payload ?? {}) as Record<string, unknown>;
  const metrics = Array.isArray(t.metrics)
    ? t.metrics.map((m) => {
        const mm = m as Record<string, unknown>;
        return {
          key: str(mm.key),
          label: str(mm.label),
          value: str(mm.value),
          unit: str(mm.unit),
        };
      })
    : [];
  return {
    range: str(t.range, "30d"),
    generated_at: str(t.generated_at),
    sales: {
      units_sold: num((t.sales as Record<string, unknown>)?.units_sold),
      revenue_fen: num((t.sales as Record<string, unknown>)?.revenue_fen),
      plan_breakdown: parsePlanRows((t.sales as Record<string, unknown>)?.plan_breakdown),
      daily_revenue_fen: parseDaily((t.sales as Record<string, unknown>)?.daily_revenue_fen),
    },
    coaching: {
      sessions: num((t.coaching as Record<string, unknown>)?.sessions),
      revenue_fen: num((t.coaching as Record<string, unknown>)?.revenue_fen),
      daily_revenue_fen: parseDaily((t.coaching as Record<string, unknown>)?.daily_revenue_fen),
    },
    renewal: {
      total_buyers: num((t.renewal as Record<string, unknown>)?.total_buyers),
      renewal_buyers: num((t.renewal as Record<string, unknown>)?.renewal_buyers),
      renewal_rate_pct: num((t.renewal as Record<string, unknown>)?.renewal_rate_pct),
    },
    funnel: parseFunnel(t.funnel),
    mastery: parseMastery(t.mastery),
    metrics,
    learners: parseLearners(t.learners),
    sessions: Array.isArray(t.sessions) ? t.sessions : [],
  };
}
