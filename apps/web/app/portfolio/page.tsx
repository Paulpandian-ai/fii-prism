"use client";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Disclaimer } from "@/components/chrome/disclaimer";
import { FactorRadar } from "@/components/analysis/factor-radar";

export default function PortfolioPage() {
  return (
    <main className="mx-auto max-w-6xl space-y-6 px-6 py-8">
      <Card>
        <CardHeader>
          <CardTitle>Portfolio</CardTitle>
          <CardDescription>
            Aggregate factor profile across your actual positions. Scaffolded only;
            position entry + factor aggregation lands in a follow-up section.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Aggregate factor radar</CardTitle>
            </CardHeader>
            <CardContent>
              <FactorRadar
                scores={{ fundamentals: 6, valuation: 5, moat: 7, macro: 5, technical: 4, risk: 6 }}
              />
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Concentration warnings</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-fii-mute">
                None — you have no positions recorded yet.
              </p>
            </CardContent>
          </Card>
        </CardContent>
      </Card>
      <Disclaimer />
    </main>
  );
}
