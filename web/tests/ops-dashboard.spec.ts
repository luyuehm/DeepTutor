import { describe, expect, it } from "vitest";

import { parseOpsDashboard } from "@/features/ops-dashboard/model";

describe("ops-dashboard model parser", () => {
  it("coerces a full payload into typed shapes", () => {
    const payload = {
      range: "30d",
      generated_at: "2026-09-14T12:00:00+00:00",
      sales: {
        units_sold: 4,
        revenue_fen: 12890,
        plan_breakdown: [
          { plan_id: "p1", units: 3, revenue_fen: 8970 },
          { plan_id: "plan_coach_basic", units: 1, revenue_fen: 3920 },
        ],
        daily_revenue_fen: [{ date: "2026-09-01", value: 2990 }],
      },
      coaching: { sessions: 1, revenue_fen: 3920, daily_revenue_fen: [] },
      renewal: { total_buyers: 3, renewal_buyers: 1, renewal_rate_pct: 33.33 },
      funnel: {
        visitors: 10,
        active: 4,
        engaged_sessions: 60,
        converted: 2,
        activation_rate_pct: 40.0,
        engagement_rate_pct: 60.0,
        conversion_rate_pct: 20.0,
      },
      mastery: {
        paths: [{ book_id: "b1" }],
        modules: [{ module_name: "M1", kp_count: 2, mastered: 1, avg_mastery: 0.72 }],
        totals: {
          path_count: 1,
          kp_count: 3,
          mastered_kp_count: 1,
          mastery_rate_pct: 33.33,
          avg_mastery_pct: 72.0,
        },
      },
      metrics: [{ key: "sales.units", label: "Units", value: "4", unit: "" }],
      learners: [
        { user_id: "u1", username: "alice", role: "user", created_at: "", disabled: false },
      ],
      sessions: [],
    };

    const dash = parseOpsDashboard(payload);
    expect(dash.range).toBe("30d");
    expect(dash.sales.units_sold).toBe(4);
    expect(dash.sales.plan_breakdown).toHaveLength(2);
    expect(dash.sales.plan_breakdown[0].plan_id).toBe("p1");
    expect(dash.renewal.renewal_rate_pct).toBe(33.33);
    expect(dash.funnel.conversion_rate_pct).toBe(20.0);
    expect(dash.mastery.totals.mastery_rate_pct).toBe(33.33);
    expect(dash.mastery.modules[0].avg_mastery).toBe(0.72);
    expect(dash.learners[0].username).toBe("alice");
  });

  it("degrades gracefully on empty / malformed payloads", () => {
    const dash = parseOpsDashboard(null);
    expect(dash.sales.units_sold).toBe(0);
    expect(dash.sales.revenue_fen).toBe(0);
    expect(dash.sales.plan_breakdown).toEqual([]);
    expect(dash.mastery.totals.kp_count).toBe(0);
    expect(dash.mastery.totals.mastery_rate_pct).toBe(0);
    expect(dash.funnel.visitors).toBe(0);
    expect(dash.range).toBe("30d");

    const dash2 = parseOpsDashboard({
      sales: { units_sold: "bad", revenue_fen: undefined },
      mastery: { totals: { kp_count: "3", mastery_rate_pct: "50" } },
    });
    expect(dash2.sales.units_sold).toBe(0);
    expect(dash2.mastery.totals.kp_count).toBe(3);
    expect(dash2.mastery.totals.mastery_rate_pct).toBe(50);
  });
});