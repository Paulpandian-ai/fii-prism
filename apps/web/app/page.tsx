export default function Home() {
  return (
    <main className="min-h-screen">
      <header className="bg-fii-navy text-white">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-5">
          <div className="flex items-center gap-3">
            <div className="h-8 w-8 rounded-sm bg-fii-blue" aria-hidden />
            <span className="font-serif text-xl font-semibold tracking-tight">FII-PRISM</span>
          </div>
          <nav className="text-sm text-fii-navy-50">
            <span className="opacity-60">Deep-Dive · Portfolio · Feed · Stress Test</span>
          </nav>
        </div>
      </header>

      <section className="mx-auto max-w-6xl px-6 py-16">
        <p className="text-xs font-medium uppercase tracking-[0.18em] text-fii-blue-600">
          Factor Impact Intelligence · v2
        </p>
        <h1 className="mt-3 font-serif text-5xl font-semibold leading-tight text-fii-navy">
          Multi-agent research for
          <br />
          long-horizon investors.
        </h1>
        <p className="mt-6 max-w-2xl text-lg leading-relaxed text-fii-mute">
          A Master Orchestrator delegates to specialist agents — Fundamentals, Valuation, Moat,
          Macro, Technical, News, Insider, and Risk — to produce sourced, debate-tested investment
          theses with DFAST-calibrated dollar stress tests.
        </p>

        <div className="mt-10 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {[
            {
              title: "Deep-Dive",
              body: "Full specialist swarm on a single ticker. Archived reasoning trail.",
            },
            {
              title: "Portfolio",
              body: "Position-level theses, drift, and factor exposure.",
            },
            {
              title: "Feed",
              body: "Breaking news and filings scored against your watchlist.",
            },
            {
              title: "Stress Test",
              body: "DFAST-style scenarios in dollar terms, not percentages.",
            },
          ].map((card) => (
            <div
              key={card.title}
              className="rounded-lg border border-fii-navy-100 bg-white p-5 shadow-sm"
            >
              <h3 className="font-serif text-lg font-semibold text-fii-navy">{card.title}</h3>
              <p className="mt-2 text-sm leading-relaxed text-fii-mute">{card.body}</p>
            </div>
          ))}
        </div>

        <p className="mt-16 text-xs text-fii-mute">
          For educational purposes only. Not investment advice.
        </p>
      </section>
    </main>
  );
}
