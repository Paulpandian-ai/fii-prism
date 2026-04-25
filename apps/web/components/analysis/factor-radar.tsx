"use client";

import { PolarAngleAxis, PolarGrid, Radar, RadarChart, ResponsiveContainer } from "recharts";

// Six factors carried over from v1's brand. Values are 0-10 scaled.
export type FactorScores = {
  fundamentals: number;
  valuation: number;
  moat: number;
  macro: number;
  technical: number;
  risk: number;
};

interface FactorRadarProps {
  scores: Partial<FactorScores>;
  height?: number;
}

const LABEL: Record<keyof FactorScores, string> = {
  fundamentals: "Fundamentals",
  valuation: "Valuation",
  moat: "Moat",
  macro: "Macro",
  technical: "Technical",
  risk: "Risk",
};

export function FactorRadar({ scores, height = 240 }: FactorRadarProps) {
  const data = (Object.keys(LABEL) as (keyof FactorScores)[]).map((k) => ({
    factor: LABEL[k],
    value: Math.max(0, Math.min(10, Number(scores[k] ?? 0))),
  }));
  return (
    <div style={{ height }} className="w-full">
      <ResponsiveContainer width="100%" height="100%">
        <RadarChart data={data} outerRadius="75%">
          <PolarGrid stroke="#e8edf3" />
          <PolarAngleAxis
            dataKey="factor"
            tick={{ fontSize: 11, fill: "#5b6b82" }}
          />
          <Radar
            dataKey="value"
            stroke="#2e6cff"
            fill="#2e6cff"
            fillOpacity={0.18}
            isAnimationActive={false}
          />
        </RadarChart>
      </ResponsiveContainer>
    </div>
  );
}
