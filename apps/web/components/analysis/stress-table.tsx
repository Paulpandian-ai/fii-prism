// DFAST stress table — preserves v1's dollar-terms presentation. Reads raw scenarios
// dict (from RiskOutput.dfast_scenarios) and renders a 5-row dollar table.

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { fmtPct, fmtUsd } from "@/lib/format";

interface Scenario {
  name?: string;
  assumed_move_pct?: number;
  position_value_start_usd?: number;
  position_value_after_usd?: number;
  drawdown_usd?: number;
  notes?: string | null;
}

const SCENARIO_LABELS: Record<string, string> = {
  pullback: "Pullback (-15%)",
  recession: "Recession (-30%)",
  severe: "Severe (-50%)",
  sector_shock: "Sector shock (-20%)",
  bull_rally: "Bull rally (+25%)",
};

export function StressTable({ scenarios }: { scenarios: Record<string, Scenario> | undefined }) {
  if (!scenarios || Object.keys(scenarios).length === 0) {
    return (
      <Card>
        <CardContent className="py-6 text-center text-sm text-fii-mute">
          Risk specialist did not produce stress scenarios.
        </CardContent>
      </Card>
    );
  }
  const rows = Object.entries(scenarios);
  return (
    <Card>
      <CardHeader>
        <CardTitle>DFAST stress test (per $10,000 position)</CardTitle>
      </CardHeader>
      <CardContent>
        <table className="w-full text-sm">
          <thead className="border-b border-fii-navy-100 text-left text-xs uppercase tracking-wide text-fii-mute">
            <tr>
              <th className="py-2 pr-4">Scenario</th>
              <th className="py-2 pr-4">Move</th>
              <th className="py-2 pr-4 text-right">Start</th>
              <th className="py-2 pr-4 text-right">After</th>
              <th className="py-2 text-right">P/L</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(([key, s]) => {
              const dd = Number(s.drawdown_usd ?? 0);
              return (
                <tr key={key} className="border-b border-fii-navy-50 last:border-0">
                  <td className="py-2 pr-4 text-fii-ink">{SCENARIO_LABELS[key] ?? key}</td>
                  <td className="py-2 pr-4 font-mono text-fii-mute">
                    {s.assumed_move_pct != null ? fmtPct(Number(s.assumed_move_pct), 0) : "—"}
                  </td>
                  <td className="py-2 pr-4 text-right font-mono text-fii-mute">
                    {fmtUsd(Number(s.position_value_start_usd ?? 0))}
                  </td>
                  <td className="py-2 pr-4 text-right font-mono">
                    {fmtUsd(Number(s.position_value_after_usd ?? 0))}
                  </td>
                  <td
                    className={
                      "py-2 text-right font-mono " +
                      (dd >= 0 ? "text-green-700" : "text-red-700")
                    }
                  >
                    {fmtUsd(dd)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </CardContent>
    </Card>
  );
}
