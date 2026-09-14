import { apiFetch, apiUrl, requestJson } from "@/shared/api/client";
import { parseOpsDashboard, type OpsDashboard } from "./model";

const DASHBOARD_PATH = "/api/ops";
const EXPORT_PATH = "/api/ops/export";

export type DashboardRange = "7d" | "30d" | "90d" | "all";

export const RANGES: ReadonlyArray<{ value: DashboardRange; label: string }> = [
  { value: "7d", label: "7D" },
  { value: "30d", label: "30D" },
  { value: "90d", label: "90D" },
  { value: "all", label: "All" },
];

export async function fetchOpsDashboard(
  range: DashboardRange,
  signal?: AbortSignal,
): Promise<OpsDashboard> {
  const payload = await requestJson<unknown>(
    `${DASHBOARD_PATH}?range=${encodeURIComponent(range)}`,
    { cache: "no-store", signal, scope: "settings" },
  );
  return parseOpsDashboard(payload);
}

export async function exportDashboardCsv(range: DashboardRange): Promise<void> {
  const res = await apiFetch(
    apiUrl(`${EXPORT_PATH}?fmt=csv&range=${encodeURIComponent(range)}`),
  );
  if (!res.ok) throw new Error("Failed to export dashboard");
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `ops_dashboard_${range}.csv`;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}
