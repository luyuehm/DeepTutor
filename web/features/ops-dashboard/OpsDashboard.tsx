"use client";

import { useTranslation } from "react-i18next";
import {
  ArrowLeft,
  Activity,
  BarChart3,
  Download,
  RefreshCw,
  Sparkles,
  Target,
  TrendingUp,
  Users,
} from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { exportDashboardCsv, RANGES, type DashboardRange } from "./api";
import type { OpsDashboard } from "./model";
import { useOpsDashboard } from "./useOpsDashboard";

/** Monetary display: fen -> ¥ with a mono tabular numeral. */
function formatFen(fen: number): string {
  return `¥${(fen / 100).toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

function percent(value: number): string {
  return `${value.toFixed(1)}%`;
}

function StatCard({
  icon,
  label,
  value,
  unit,
  glow,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  unit?: string;
  glow?: boolean;
}) {
  return (
    <div
      className={
        "rounded-2xl border border-[rgba(0,229,255,0.14)] bg-[rgba(14,20,32,0.72)] px-4 py-4 " +
        (glow
          ? "shadow-[0_0_24px_rgba(0,229,255,0.18)]"
          : "shadow-[0_0_12px_rgba(0,0,0,0.28)]")
      }
    >
      <div className="flex items-center gap-2 text-xs text-[rgba(155,183,255,0.85)]">
        <span className="text-[#00E5FF]">{icon}</span>
        {label}
      </div>
      <p className="mt-2 font-mono text-2xl font-semibold tracking-tight text-[#F4F6FF]">
        {value}
        {unit ? (
          <span className="ml-1 font-mono text-xs text-[rgba(155,183,255,0.7)]">{unit}</span>
        ) : null}
      </p>
    </div>
  );
}

function BarRow({ label, value, max }: { label: string; value: number; max: number }) {
  const pct = max > 0 ? Math.min(100, Math.round((value / max) * 100)) : 0;
  return (
    <div className="mb-2">
      <div className="mb-1 flex items-center justify-between text-xs">
        <span className="text-[rgba(155,183,255,0.85)]">{label}</span>
        <span className="font-mono text-[#F4F6FF]">{value}</span>
      </div>
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-[rgba(255,255,255,0.06)]">
        <div
          className="h-full rounded-full bg-gradient-to-r from-[#00E5FF] to-[#2F80FF]"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

interface OpsDashboardViewProps {
  data: OpsDashboard;
  range: DashboardRange;
  onRangeChange: (r: DashboardRange) => void;
  onRefresh: () => void;
  loading: boolean;
}

function OpsDashboardView({
  data,
  range,
  onRangeChange,
  onRefresh,
  loading,
}: OpsDashboardViewProps) {
  const { t } = useTranslation();
  const [exporting, setExporting] = useState(false);

  const mastery = data.mastery.totals;
  const funnel = data.funnel;
  const maxPlanUnits = Math.max(1, ...data.sales.plan_breakdown.map((p) => p.units));

  async function handleExport() {
    setExporting(true);
    try {
      await exportDashboardCsv(range);
    } catch {
      // silent — export button is a convenience surface
    } finally {
      setExporting(false);
    }
  }

  return (
    <div className="ops-dashboard">
      <style jsx global>{`
        .ops-dashboard {
          --bg-deep: #080b14;
          --bg-panel: #0e1420;
          --cyan: #00e5ff;
          --blue: #2f80ff;
          --text-bright: #f4f6ff;
          --text-dim: rgba(155, 183, 255, 0.85);
        }
        .ops-dashboard {
          color: var(--text-bright);
        }
      `}</style>

      {/* Header */}
      <div className="mb-6 flex flex-wrap items-center justify-between gap-4">
        <div>
          <Link
            href="/admin"
            className="mb-2 inline-flex items-center gap-1.5 text-sm text-[var(--text-dim)] transition-colors hover:text-[var(--cyan)]"
          >
            <ArrowLeft size={16} />
            {t("Back to Admin")}
          </Link>
          <div className="flex items-center gap-2">
            <Sparkles size={18} className="text-[#00E5FF]" />
            <h1 className="font-mono text-lg font-semibold tracking-wide text-[#F4F6FF]">
              {t("Ops Dashboard")}
            </h1>
          </div>
          <p className="mt-0.5 text-xs text-[var(--text-dim)]">
            {t("Revenue, renewal, funnel and mastery — aggregated from existing data.")}
          </p>
        </div>

        <div className="flex items-center gap-2">
          <div className="flex items-center overflow-hidden rounded-lg border border-[rgba(0,229,255,0.25)]">
            {RANGES.map((r) => (
              <button
                key={r.value}
                onClick={() => onRangeChange(r.value)}
                className={
                  "px-3 py-1.5 font-mono text-xs transition-colors " +
                  (range === r.value
                    ? "bg-[rgba(0,229,255,0.14)] text-[#00E5FF]"
                    : "text-[var(--text-dim)] hover:text-[#F4F6FF]")
                }
              >
                {r.label}
              </button>
            ))}
          </div>
          <button
            onClick={onRefresh}
            disabled={loading}
            aria-label={t("Refresh")}
            className="rounded-lg border border-[rgba(0,229,255,0.25)] p-1.5 text-[var(--text-dim)] transition-colors hover:text-[#00E5FF] disabled:opacity-50"
          >
            <RefreshCw size={15} className={loading ? "animate-spin" : ""} />
          </button>
          <button
            onClick={() => void handleExport()}
            disabled={exporting}
            className="flex items-center gap-1.5 rounded-lg border border-[rgba(0,229,255,0.35)] bg-[rgba(0,229,255,0.1)] px-3 py-1.5 font-mono text-xs text-[#00E5FF] transition-colors hover:bg-[rgba(0,229,255,0.18)] disabled:opacity-50"
          >
            <Download size={14} />
            {t("Export CSV")}
          </button>
        </div>
      </div>

      {/* KPI strip — mono numerals, ≤3 saturated colors */}
      <div className="mb-5 grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <StatCard
          icon={<BarChart3 size={15} />}
          label={t("Units sold")}
          value={String(data.sales.units_sold)}
        />
        <StatCard
          icon={<Activity size={15} />}
          label={t("Sales revenue")}
          value={formatFen(data.sales.revenue_fen)}
        />
        <StatCard
          icon={<Target size={15} />}
          label={t("1v1 coaching")}
          value={formatFen(data.coaching.revenue_fen)}
          glow
        />
        <StatCard
          icon={<TrendingUp size={15} />}
          label={t("Renewal rate")}
          value={percent(data.renewal.renewal_rate_pct)}
        />
        <StatCard
          icon={<Users size={15} />}
          label={t("Conversion rate")}
          value={percent(funnel.conversion_rate_pct)}
        />
        <StatCard
          icon={<Sparkles size={15} />}
          label={t("Mastery rate")}
          value={percent(mastery.mastery_rate_pct)}
        />
      </div>

      {/* Two-column: funnel + renewal & sales plans */}
      <div className="mb-5 grid grid-cols-1 gap-4 lg:grid-cols-2">
        <section className="rounded-2xl border border-[rgba(0,229,255,0.14)] bg-[rgba(14,20,32,0.6)] p-4">
          <h2 className="mb-3 flex items-center gap-2 font-mono text-sm text-[#F4F6FF]">
            <Users size={15} className="text-[#2F80FF]" />
            {t("Conversion funnel")}
          </h2>
          <div className="space-y-3">
            {[
              { key: "visitors", label: t("Visitors"), value: funnel.visitors },
              { key: "active", label: t("Active learners"), value: funnel.active },
              { key: "engaged", label: t("Engaged sessions"), value: funnel.engaged_sessions },
              { key: "converted", label: t("Converted buyers"), value: funnel.converted },
            ].map((row) => (
              <BarRow
                key={row.key}
                label={row.label}
                value={row.value}
                max={funnel.visitors}
              />
            ))}
          </div>
        </section>

        <section className="rounded-2xl border border-[rgba(0,229,255,0.14)] bg-[rgba(14,20,32,0.6)] p-4">
          <h2 className="mb-3 flex items-center gap-2 font-mono text-sm text-[#F4F6FF]">
            <BarChart3 size={15} className="text-[#00E5FF]" />
            {t("Course / plan sales")}
          </h2>
          {data.sales.plan_breakdown.length === 0 ? (
            <p className="text-xs text-[var(--text-dim)]">{t("No sales in this period.")}</p>
          ) : (
            <div>
              {data.sales.plan_breakdown.map((plan) => (
                <BarRow
                  key={plan.plan_id}
                  label={plan.plan_id}
                  value={plan.units}
                  max={maxPlanUnits}
                />
              ))}
              <p className="mt-2 font-mono text-xs text-[var(--text-dim)]">
                {t("Total")}:{" "}
                <span className="text-[#00E5FF]">
                  {data.sales.units_sold} · {formatFen(data.sales.revenue_fen)}
                </span>
              </p>
            </div>
          )}
        </section>
      </div>

      {/* Mastery by module */}
      <section className="rounded-2xl border border-[rgba(0,229,255,0.14)] bg-[rgba(14,20,32,0.6)] p-4">
        <h2 className="mb-3 flex items-center gap-2 font-mono text-sm text-[#F4F6FF]">
          <Sparkles size={15} className="text-[#2F80FF]" />
          {t("Mastery by knowledge point")}
        </h2>
        <div className="mb-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
          <div className="rounded-lg border border-[rgba(255,255,255,0.06)] px-3 py-2">
            <p className="text-[10px] uppercase tracking-wider text-[var(--text-dim)]">
              {t("Paths")}
            </p>
            <p className="font-mono text-lg text-[#F4F6FF]">{mastery.path_count}</p>
          </div>
          <div className="rounded-lg border border-[rgba(255,255,255,0.06)] px-3 py-2">
            <p className="text-[10px] uppercase tracking-wider text-[var(--text-dim)]">
              {t("Knowledge points")}
            </p>
            <p className="font-mono text-lg text-[#F4F6FF]">{mastery.kp_count}</p>
          </div>
          <div className="rounded-lg border border-[rgba(255,255,255,0.06)] px-3 py-2">
            <p className="text-[10px] uppercase tracking-wider text-[var(--text-dim)]">
              {t("Mastered")}
            </p>
            <p className="font-mono text-lg text-[#00E5FF]">{mastery.mastered_kp_count}</p>
          </div>
          <div className="rounded-lg border border-[rgba(255,255,255,0.06)] px-3 py-2">
            <p className="text-[10px] uppercase tracking-wider text-[var(--text-dim)]">
              {t("Avg mastery")}
            </p>
            <p className="font-mono text-lg text-[#2F80FF]">{percent(mastery.avg_mastery_pct)}</p>
          </div>
        </div>

        {data.mastery.modules.length === 0 ? (
          <p className="text-xs text-[var(--text-dim)]">
            {t("No mastery data yet — start a mastery path.")}
          </p>
        ) : (
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-3">
            {data.mastery.modules.map((module) => (
              <div
                key={module.module_name}
                className="rounded-lg border border-[rgba(255,255,255,0.06)] px-3 py-2"
              >
                <div className="mb-1 flex items-center justify-between">
                  <span className="text-xs text-[var(--text-dim)]">{module.module_name}</span>
                  <span className="font-mono text-[10px] text-[#2F80FF]">
                    {module.mastered}/{module.kp_count}
                  </span>
                </div>
                <div className="h-1.5 w-full overflow-hidden rounded-full bg-[rgba(255,255,255,0.06)]">
                  <div
                    className="h-full rounded-full bg-gradient-to-r from-[#00E5FF] to-[#2F80FF]"
                    style={{
                      width: `${Math.min(100, Math.round((module.avg_mastery / 1) * 100))}%`,
                    }}
                  />
                </div>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

interface OpsDashboardFeatureProps {
  initialRange?: DashboardRange;
}

export function OpsDashboardFeature({ initialRange = "30d" }: OpsDashboardFeatureProps) {
  const [range, setRange] = useState<DashboardRange>(initialRange);
  const { data, loading, error, refresh } = useOpsDashboard(range);

  return (
    <div className="min-h-screen bg-[#080B14] px-4 py-8 [scrollbar-gutter:stable] md:px-6 xl:px-8">
      <div className="mx-auto max-w-[1400px]">
        {error ? (
          <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-400">
            {error}
          </div>
        ) : data ? (
          <OpsDashboardView
            data={data}
            range={range}
            onRangeChange={setRange}
            onRefresh={refresh}
            loading={loading}
          />
        ) : (
          <div className="flex h-64 items-center justify-center">
            <div className="h-6 w-6 animate-spin rounded-full border-2 border-[rgba(0,229,255,0.3)] border-t-[#00E5FF]" />
          </div>
        )}
      </div>
    </div>
  );
}
