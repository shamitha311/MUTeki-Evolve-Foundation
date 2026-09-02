import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ErrorBoundary } from '@/components/error-boundary';
import { Toaster } from '@/components/ui/toaster';
import { TooltipProvider } from '@/components/ui/tooltip';
import {
  DemoHeader,
  EvidencePanel,
  FailureStates,
  InvestigationTimeline,
  ObjectiveCard,
  ScoreHistory,
  ScorePanel,
  SafeStateBanner,
  StrategyCard,
  StrategyHistory,
  SystemStatus,
  TargetCard,
} from '@/components/replay-console';
import { demoScenario, type ReplayStatus, type SafeState } from '@/lib/replay';
import NotFound from '@/pages/not-found';
import {
  Route,
  Switch,
  useLocation,
  Router as WouterRouter,
} from 'wouter';

const queryClient = new QueryClient();

function Home() {
  const [status, setStatus] = useState<ReplayStatus>('IDLE');
  const [roundIndex, setRoundIndex] = useState(-1);
  const [autoPlay, setAutoPlay] = useState(false);
  const [safeState, setSafeState] = useState<SafeState | null>(null);

  const activeIndex = Math.max(0, roundIndex);
  const activeRound = demoScenario.rounds[activeIndex];
  const score = useMemo(() => {
    if (roundIndex < 0) {
      return {
        progress_score: 0,
        progress_level: 'awaiting replay',
        reasons: ['No evaluation has been admitted yet.'],
        solved: false,
      };
    }
    if (safeState === 'evaluator-failure' || safeState === 'timeout') {
      return {
        ...activeRound.score,
        progress_level:
          safeState === 'timeout' ? 'timed out' : 'evaluation unavailable',
        reasons: [
          safeState === 'timeout'
            ? 'The bounded investigation timed out before completion.'
            : 'The evaluator did not return a usable report.',
        ],
        solved: false,
        stagnated: true,
      };
    }
    return activeRound.score;
  }, [activeRound, roundIndex, safeState]);
  const events =
    roundIndex < 0 || safeState === 'empty-events'
      ? []
      : activeRound.result.events;
  const evidence =
    roundIndex < 0 || safeState === 'incomplete-evidence'
      ? []
      : activeRound.result.evidence;

  const advance = () => {
    setSafeState(null);
    if (roundIndex < 0) {
      setRoundIndex(0);
      setStatus('RUNNING');
      return;
    }
    if (roundIndex < demoScenario.rounds.length - 1) {
      setRoundIndex((current) => current + 1);
      setStatus('RUNNING');
    } else {
      setStatus('COMPLETED');
      setAutoPlay(false);
    }
  };

  useEffect(() => {
    if (!autoPlay || status !== 'RUNNING') return;
    const timer = window.setTimeout(advance, 3200);
    return () => window.clearTimeout(timer);
  }, [autoPlay, status, roundIndex]);

  const start = () => {
    setSafeState(null);
    if (status === 'PAUSED' && roundIndex >= 0) {
      setStatus('RUNNING');
      return;
    }
    setRoundIndex(0);
    setStatus('RUNNING');
  };
  const pause = () => {
    setStatus('PAUSED');
    setAutoPlay(false);
  };
  const reset = () => {
    setRoundIndex(-1);
    setStatus('IDLE');
    setAutoPlay(false);
    setSafeState(null);
  };
  const toggleAutoPlay = () => {
    if (status === 'COMPLETED') {
      setSafeState(null);
      setRoundIndex(0);
      setStatus('RUNNING');
      setAutoPlay(true);
      return;
    }
    if (status === 'IDLE') {
      setSafeState(null);
      setRoundIndex(0);
      setStatus('RUNNING');
      setAutoPlay(true);
      return;
    }
    setAutoPlay((current) => !current);
    if (status === 'PAUSED') setStatus('RUNNING');
  };

  return (
    <div className="noise min-h-[100dvh] bg-background text-foreground">
      <DemoHeader status={status} roundIndex={activeIndex} onStart={start} onPause={pause} onNext={advance} onReset={reset} autoPlay={autoPlay} onAutoPlay={toggleAutoPlay} />
      <div className="mx-auto grid max-w-[1600px] gap-5 px-4 pb-10 pt-5 sm:px-6 lg:grid-cols-[220px_minmax(0,1fr)] lg:gap-7 lg:px-8">
        <aside className="hidden lg:block">
          <div className="sticky top-[78px] space-y-6">
            <div>
              <div className="eyebrow text-muted-foreground">console / 01</div>
              <h1 className="mt-2 text-[25px] font-extrabold leading-[1.05] tracking-[-.06em]">Watch the loop<br /><span className="text-[hsl(var(--chart-2))]">get sharper.</span></h1>
            </div>
            <div className="border-l-2 border-[hsl(var(--accent))] pl-3 text-[11px] leading-5 text-muted-foreground">Evidence enters.<br />Strategy evolves.<br />The next move narrows.</div>
            <div className="space-y-2 border-t border-border pt-5">
              <div className="eyebrow text-muted-foreground">replay map</div>
              {['A · reconnaissance', 'B · correlation', 'C · verification'].map((label, index) => <div key={label} data-testid={`nav-round-${index}`} className={`flex items-center gap-2 py-1.5 font-mono-ui text-[10px] ${index === activeIndex && roundIndex >= 0 ? 'text-foreground' : 'text-muted-foreground'}`}><span className={`size-1.5 rounded-full ${index <= roundIndex ? 'bg-[hsl(var(--accent))]' : 'bg-[hsl(var(--border))]'}`} />{label}</div>)}
            </div>
            <div className="rounded-xl bg-[hsl(var(--primary))] p-3.5 text-[hsl(var(--primary-foreground))]">
              <div className="flex items-center gap-2 font-mono-ui text-[9px] uppercase tracking-[.13em] text-[hsl(var(--accent))]"><span className="pulse-signal size-1.5 rounded-full bg-current" />live grok swarm</div>
              <p className="mt-2 text-[11px] leading-4 text-[hsl(var(--primary-foreground)/.72)]">Connected to Muteki Grok Swarm engine. Multi-round MTASA strategy evolution active.</p>
            </div>
          </div>
        </aside>
        <main className="min-w-0">
          <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
            <div>
              <div className="eyebrow text-[hsl(var(--chart-2))]">judge-facing investigation console</div>
              <h2 data-testid="text-page-title" className="mt-1 text-[28px] font-extrabold tracking-[-.06em] sm:text-[34px]">Autonomous Swarm Investigation Console</h2>
            </div>
            <div className="flex items-center gap-3 font-mono-ui text-[10px] text-[hsl(var(--muted-foreground))]"><span className="hidden sm:inline">RUN ID</span><span data-testid="text-run-id" className="rounded bg-[hsl(var(--muted))] px-2 py-1 text-[hsl(var(--foreground)/.78)]">ev-001</span><span data-testid="text-iteration" className="rounded bg-[hsl(var(--muted))] px-2 py-1">ITERATION {roundIndex < 0 ? '—' : `${activeIndex + 1}/3`}</span></div>
          </div>
          <div className="grid min-w-0 gap-4 xl:grid-cols-[minmax(0,1.28fr)_minmax(0,.72fr)]">
            <div className="min-w-0 space-y-4">
              <TargetCard />
              <ObjectiveCard strategy={activeRound.strategy} score={score} />
              <StrategyCard strategy={activeRound.strategy} roundIndex={activeIndex} />
              <InvestigationTimeline
                events={events}
                status={status}
                emptyMessage={
                  safeState === 'empty-events'
                    ? 'No investigation events received yet.'
                    : undefined
                }
              />
            </div>
            <div className="min-w-0 space-y-4">
              <ScorePanel score={score} />
              <EvidencePanel evidence={evidence} />
              <ScoreHistory activeIndex={activeIndex} />
              <StrategyHistory activeIndex={activeIndex} />
              <SystemStatus status={status} />
            </div>
          </div>
          <FailureStates
            activeState={safeState}
            onSelect={setSafeState}
          />
          <SafeStateBanner
            state={safeState}
            onClear={() => setSafeState(null)}
          />
          <footer className="mt-7 flex flex-wrap items-center justify-between gap-3 border-t border-border pt-4 font-mono-ui text-[9px] uppercase tracking-[.12em] text-muted-foreground"><span>project-owned normalized view model</span><span>bounded · deterministic · display-only target</span></footer>
        </main>
      </div>
    </div>
  );
}

function Router() {
  return (
    // Keep a shared shell (sidebar, navbar) outside the boundary so it
    // survives a page crash.
    <RoutedErrorBoundary>
      <Switch>
        <Route path="/" component={Home} />
        <Route component={NotFound} />
      </Switch>
    </RoutedErrorBoundary>
  );
}

function RoutedErrorBoundary({ children }: { children: ReactNode }) {
  const [location] = useLocation();
  return <ErrorBoundary resetKey={location}>{children}</ErrorBoundary>;
}

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <WouterRouter base={import.meta.env.BASE_URL.replace(/\/$/, '')}>
          <Router />
        </WouterRouter>
        <Toaster />
      </TooltipProvider>
    </QueryClientProvider>
  );
}

export default App;
