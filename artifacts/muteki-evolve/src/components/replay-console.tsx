import type { ReactNode } from "react";
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  Check,
  ChevronRight,
  CircleDot,
  Clock3,
  Copy,
  Database,
  GitBranch,
  Info,
  Layers3,
  Pause,
  Play,
  RotateCcw,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  TimerOff,
  TriangleAlert,
  WifiOff,
} from "lucide-react";
import {
  demoScenario,
  type Evidence,
  type InvestigationEvent,
  type ReplayRound,
  type ReplayStatus,
  type SafeState,
  type ScoreReport,
  type Strategy,
} from "@/lib/replay";

type HeaderProps = {
  status: ReplayStatus;
  roundIndex: number;
  onStart: () => void;
  onPause: () => void;
  onNext: () => void;
  onReset: () => void;
  autoPlay: boolean;
  onAutoPlay: () => void;
};

export function DemoHeader({ status, roundIndex, onStart, onPause, onNext, onReset, autoPlay, onAutoPlay }: HeaderProps) {
  const completed = status === "COMPLETED";
  const running = status === "RUNNING";
  return (
    <header className="sticky top-0 z-40 border-b border-[hsl(var(--border)/.8)] bg-[hsl(var(--background)/.94)] backdrop-blur-xl">
      <div className="mx-auto flex max-w-[1600px] flex-wrap items-center justify-between gap-3 px-4 py-3 sm:px-6 lg:px-8">
        <div className="flex items-center gap-3">
          <div className="grid size-9 place-items-center rounded-[10px] bg-[hsl(var(--primary))] text-[hsl(var(--accent))]">
            <Sparkles size={17} strokeWidth={1.8} />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <span className="text-[15px] font-extrabold tracking-[-.03em]">MUTeki<span className="text-[hsl(var(--chart-2))]">-Evolve</span></span>
              <span className="rounded-full border border-[hsl(var(--accent)/.55)] bg-[hsl(var(--accent)/.16)] px-2 py-0.5 font-mono-ui text-[9px] font-medium uppercase tracking-[.14em] text-[hsl(var(--primary))]">live engine</span>
            </div>
            <p className="font-mono-ui text-[10px] text-[hsl(var(--muted-foreground))]">bounded autonomous investigation loop</p>
          </div>
        </div>
        <div className="flex items-center gap-1.5">
          <span data-testid="status-replay" className={`mr-2 hidden items-center gap-2 font-mono-ui text-[10px] uppercase tracking-[.14em] sm:flex ${running ? "text-[hsl(var(--chart-2))]" : "text-[hsl(var(--muted-foreground))]"}`}>
            <span className={`size-1.5 rounded-full bg-current ${running ? "pulse-signal" : ""}`} />
            {status.toLowerCase()} · round {Math.min(roundIndex + 1, 3)} / 3
          </span>
          <button data-testid="button-start-replay" onClick={onStart} disabled={running || completed} className="control-button control-button-primary disabled:cursor-not-allowed disabled:opacity-40"><Play size={13} fill="currentColor" />{completed ? "Swarm Complete" : "Run Swarm Strategy"}</button>
          <button data-testid="button-pause-replay" onClick={onPause} disabled={!running} className="control-button disabled:cursor-not-allowed disabled:opacity-40"><Pause size={13} />Pause</button>
          <button data-testid="button-next-round" onClick={onNext} disabled={completed} className="control-button disabled:cursor-not-allowed disabled:opacity-40"><ChevronRight size={14} />Next round</button>
          <button data-testid="button-reset-replay" onClick={onReset} className="control-button control-button-icon" aria-label="Reset session"><RotateCcw size={14} /></button>
          <button data-testid="button-auto-play" onClick={onAutoPlay} className={`control-button ${autoPlay ? "control-button-active" : ""}`}><Activity size={14} />Auto play</button>
        </div>
      </div>
    </header>
  );
}

export function TargetCard() {
  const { target } = demoScenario;
  return (
    <section className="panel reveal" data-testid="card-target">
      <div className="panel-head flex items-center justify-between">
        <div className="flex items-center gap-2"><Database size={15} className="text-[hsl(var(--chart-2))]" /><span className="eyebrow text-[hsl(var(--muted-foreground))]">trusted target</span></div>
        <span className="flex items-center gap-1.5 font-mono-ui text-[9px] uppercase tracking-[.12em] text-[hsl(var(--chart-2))]"><ShieldCheck size={12} />display-only</span>
      </div>
      <div className="p-[18px]">
        <h2 data-testid="text-target-name" className="text-[18px] font-extrabold tracking-[-.04em]">{target.name}</h2>
        <p data-testid="text-target-description" className="mt-2 max-w-[36rem] text-[12px] leading-5 text-[hsl(var(--muted-foreground))]">{target.description}</p>
        <div className="mt-5 grid gap-2 border-t border-[hsl(var(--border))] pt-3 sm:grid-cols-2">
          <InfoRow label="target id" value={target.id} />
          <InfoRow label="runtime reference" value={target.runtime_reference} />
        </div>
      </div>
    </section>
  );
}

function InfoRow({ label, value }: { label: string; value: string }) {
  return <div><div className="eyebrow text-[hsl(var(--muted-foreground))]">{label}</div><div data-testid={`text-${label.replaceAll(" ", "-")}`} className="mt-1 break-all font-mono-ui text-[11px] text-[hsl(var(--foreground)/.82)]">{value}</div></div>;
}

export function ObjectiveCard({ strategy, score }: { strategy: Strategy; score: ScoreReport }) {
  return (
    <section className="panel reveal reveal-1" data-testid="card-objective">
      <div className="panel-head flex items-center justify-between">
        <div className="flex items-center gap-2"><CircleDot size={15} className="text-[hsl(var(--accent))]" /><span className="eyebrow text-[hsl(var(--muted-foreground))]">current objective</span></div>
        <span data-testid="text-progress-level" className="rounded-full bg-[hsl(var(--muted))] px-2.5 py-1 font-mono-ui text-[9px] uppercase tracking-[.1em] text-[hsl(var(--muted-foreground))]">{score.progress_level}</span>
      </div>
      <div className="p-[18px]">
        <p data-testid="text-current-objective" className="max-w-[52rem] text-[16px] font-semibold leading-6 tracking-[-.02em]">{strategy.objective}</p>
        <div className="mt-5 grid gap-5 sm:grid-cols-2">
          <ListBlock label="priority signals" items={strategy.priorities} accent />
          <ListBlock label="guardrails" items={strategy.constraints} />
        </div>
      </div>
    </section>
  );
}

function ListBlock({ label, items, accent }: { label: string; items: string[]; accent?: boolean }) {
  return <div><div className="eyebrow text-[hsl(var(--muted-foreground))]">{label}</div><ul className="mt-2 space-y-2">{items.map((item) => <li key={item} className="flex items-start gap-2 text-[11px] leading-4 text-[hsl(var(--foreground)/.78)]"><span className={`mt-1.5 size-1.5 shrink-0 rounded-full ${accent ? "bg-[hsl(var(--accent))]" : "border border-[hsl(var(--muted-foreground))]"}`} />{item}</li>)}</ul></div>;
}

export function StrategyCard({ strategy, roundIndex }: { strategy: Strategy; roundIndex: number }) {
  const roundName = ["A", "B", "C"][roundIndex] ?? "A";
  return (
    <section className="panel reveal reveal-2 overflow-hidden" data-testid="card-strategy">
      <div className="signal-line h-1 w-full" />
      <div className="panel-head flex items-center justify-between">
        <div className="flex items-center gap-2"><SlidersHorizontal size={15} className="text-[hsl(var(--chart-2))]" /><span className="eyebrow text-[hsl(var(--muted-foreground))]">evolved strategy</span></div>
        <div className="flex items-center gap-2 font-mono-ui text-[10px]"><span className="text-[hsl(var(--muted-foreground))]">REV</span><strong data-testid="text-strategy-revision" className="text-[hsl(var(--foreground))]">{strategy.revision}</strong><span className="rounded bg-[hsl(var(--accent)/.22)] px-1.5 py-0.5 text-[hsl(var(--primary))]">strategy {roundName}</span></div>
      </div>
      <div className="p-[18px]">
        <div className="flex flex-wrap items-center gap-2">
          <span data-testid="text-strategy-lineage" className="rounded border border-[hsl(var(--border))] px-2 py-1 font-mono-ui text-[10px]">revision {strategy.revision}</span>
          {strategy.parent_revision ? <><ArrowRight size={12} className="text-[hsl(var(--muted-foreground))]" /><span className="rounded border border-[hsl(var(--border))] bg-[hsl(var(--muted)/.5)] px-2 py-1 font-mono-ui text-[10px] text-[hsl(var(--muted-foreground))]">parent {strategy.parent_revision}</span></> : <span className="font-mono-ui text-[10px] text-[hsl(var(--muted-foreground))]">root strategy</span>}
        </div>
        <p className="mt-4 text-[12px] leading-5 text-[hsl(var(--foreground)/.76)]">{strategy.parent_revision ? `Built from the ${strategy.parent_revision === 1 ? "reconnaissance" : "correlation"} signal; now narrows the loop toward a verifiable outcome.` : "First pass establishes the surface and gathers signals without leaving the trusted boundary."}</p>
        {strategy.context && <div className="mt-4 flex items-center gap-2 font-mono-ui text-[10px] text-[hsl(var(--muted-foreground))]"><GitBranch size={12} />context: {Object.entries(strategy.context).map(([key, value]) => `${key}=${value}`).join(", ")}</div>}
      </div>
    </section>
  );
}

export function InvestigationTimeline({ events, status, emptyMessage }: { events: InvestigationEvent[]; status: ReplayStatus; emptyMessage?: string }) {
  return (
    <section className="panel reveal reveal-2" data-testid="panel-investigation-timeline">
      <div className="panel-head flex items-center justify-between">
        <div><div className="flex items-center gap-2"><Activity size={15} className="text-[hsl(var(--chart-2))]" /><span className="eyebrow text-[hsl(var(--muted-foreground))]">normalized event stream</span></div><h2 className="mt-1 text-[14px] font-bold tracking-[-.02em]">Investigation timeline</h2></div>
        <span data-testid="text-event-count" className="font-mono-ui text-[10px] text-[hsl(var(--muted-foreground))]">{events.length.toString().padStart(2, "0")} events</span>
      </div>
      <div className="p-[18px]">
        {events.length === 0 ? <EmptyEvents status={status} message={emptyMessage} /> : <ol className="relative ml-2 border-l border-[hsl(var(--border))]">{events.map((event) => <TimelineEvent key={event.sequence} event={event} />)}</ol>}
      </div>
    </section>
  );
}

function TimelineEvent({ event }: { event: InvestigationEvent }) {
  const color = event.status === "verified" ? "text-[hsl(var(--chart-2))]" : event.status === "signal" ? "text-[hsl(var(--chart-3))]" : "text-[hsl(var(--muted-foreground))]";
  return <li data-testid={`event-row-${event.sequence}`} className="relative mb-5 ml-5 last:mb-0"><span className={`absolute -left-[25px] top-0.5 grid size-3 place-items-center rounded-full border-2 border-[hsl(var(--card))] bg-current ${color}`}><span className="size-1 rounded-full bg-[hsl(var(--card))]" /></span><div className="flex flex-wrap items-center gap-x-2 gap-y-1"><span className="font-mono-ui text-[10px] text-[hsl(var(--muted-foreground))]">#{String(event.sequence).padStart(2, "0")}</span><span data-testid={`text-event-type-${event.sequence}`} className={`font-mono-ui text-[10px] uppercase tracking-[.08em] ${color}`}>{event.type}</span><span className="font-mono-ui text-[10px] text-[hsl(var(--muted-foreground))]">{event.timestamp.slice(11, 19)}</span></div><p data-testid={`text-event-summary-${event.sequence}`} className="mt-1 text-[12px] leading-5 text-[hsl(var(--foreground)/.84)]">{event.summary}</p><div className="mt-1.5 flex items-center gap-2 font-mono-ui text-[9px] uppercase tracking-[.08em] text-[hsl(var(--muted-foreground))]"><span>{event.worker ?? "normalized worker"}</span><span className="size-0.5 rounded-full bg-current" /><span>{event.status}</span></div></li>;
}

function EmptyEvents({ status, message }: { status: ReplayStatus; message?: string }) {
  const label = message ?? (status === "IDLE" ? "Start the replay to admit normalized events." : "No events were returned for this round.");
  return <div data-testid="state-empty-events" className="flex min-h-32 flex-col items-center justify-center rounded-lg border border-dashed border-[hsl(var(--border))] bg-[hsl(var(--muted)/.35)] text-center"><Clock3 size={18} className="text-[hsl(var(--muted-foreground))]" /><p className="mt-2 text-[11px] text-[hsl(var(--muted-foreground))]">{label}</p></div>;
}

export function EvidencePanel({ evidence }: { evidence: Evidence[] }) {
  return <section className="panel reveal reveal-3" data-testid="panel-evidence"><div className="panel-head flex items-center justify-between"><div className="flex items-center gap-2"><Layers3 size={15} className="text-[hsl(var(--chart-3))]" /><span className="eyebrow text-[hsl(var(--muted-foreground))]">evidence ledger</span></div><span data-testid="text-evidence-count" className="font-mono-ui text-[10px] text-[hsl(var(--muted-foreground))]">{evidence.length} signals</span></div><div className="divide-y divide-[hsl(var(--border))]">{evidence.length === 0 ? <div data-testid="state-incomplete-evidence" className="p-[18px] text-[11px] text-[hsl(var(--muted-foreground))]">Evidence is incomplete for this round. Confidence will appear when a normalized source event arrives.</div> : evidence.map((item, index) => <EvidenceRow key={`${item.type}-${index}`} evidence={item} index={index} />)}</div></section>;
}

function EvidenceRow({ evidence, index }: { evidence: Evidence; index: number }) {
  return <div data-testid={`evidence-row-${index}`} className="p-[18px]"><div className="flex items-start justify-between gap-4"><div><div className="flex items-center gap-2"><span className="size-1.5 rounded-full bg-[hsl(var(--chart-3))]" /><span data-testid={`text-evidence-type-${index}`} className="font-mono-ui text-[10px] uppercase tracking-[.1em] text-[hsl(var(--muted-foreground))]">{evidence.type}</span></div><p data-testid={`text-evidence-summary-${index}`} className="mt-2 text-[12px] leading-5 text-[hsl(var(--foreground)/.82)]">{evidence.summary}</p></div><strong data-testid={`text-evidence-confidence-${index}`} className="shrink-0 font-mono-ui text-[13px] text-[hsl(var(--chart-3))]">{Math.round(evidence.confidence * 100)}<span className="text-[10px]"> / 100</span></strong></div><div className="mt-3 flex items-center gap-3"><div className="h-1 flex-1 overflow-hidden rounded-full bg-[hsl(var(--muted))]"><div className="h-full rounded-full bg-[hsl(var(--chart-3))]" style={{ width: `${evidence.confidence * 100}%` }} /></div>{evidence.source_event ? <span data-testid={`text-evidence-source-${index}`} className="font-mono-ui text-[9px] text-[hsl(var(--muted-foreground))]">source event #{evidence.source_event}</span> : <span className="font-mono-ui text-[9px] text-[hsl(var(--muted-foreground))]">source pending</span>}</div></div>;
}

export function ScorePanel({ score }: { score: ScoreReport }) {
  const solved = score.solved;
  return <section className="panel reveal reveal-3 overflow-hidden" data-testid="panel-score"><div className="panel-head flex items-center justify-between"><div className="flex items-center gap-2"><span className="grid size-5 place-items-center rounded-full bg-[hsl(var(--primary))] text-[hsl(var(--accent))]"><Check size={12} strokeWidth={3} /></span><span className="eyebrow text-[hsl(var(--muted-foreground))]">evaluation output</span></div><span data-testid="status-solved" className={`flex items-center gap-1.5 font-mono-ui text-[10px] uppercase tracking-[.1em] ${solved ? "text-[hsl(var(--chart-2))]" : "text-[hsl(var(--muted-foreground))]"}`}><span className="size-1.5 rounded-full bg-current" />{solved ? "solved" : score.stagnated ? "stagnated" : "in progress"}</span></div><div className="p-[18px]"><div className="flex items-end justify-between"><div><div data-testid="text-progress-score" className="font-mono-ui text-[44px] font-medium leading-none tracking-[-.08em]">{score.progress_score}<span className="ml-1 text-[16px] text-[hsl(var(--muted-foreground))]">/ 100</span></div><p className="mt-2 text-[11px] text-[hsl(var(--muted-foreground))]">investigation progress, not solved percentage</p></div><div className="grid size-[74px] place-items-center rounded-full border-[7px] border-[hsl(var(--muted))]" style={{ background: `conic-gradient(hsl(var(--accent)) ${score.progress_score}%, hsl(var(--muted)) 0)` }}><div className="grid size-[58px] place-items-center rounded-full bg-[hsl(var(--card))] font-mono-ui text-[11px]">{score.progress_score}</div></div></div><div className="mt-5 h-2 overflow-hidden rounded-full bg-[hsl(var(--muted))]"><div data-testid="progress-bar" className="h-full rounded-full bg-[hsl(var(--accent))] transition-[width] duration-500" style={{ width: `${score.progress_score}%` }} /></div><div className="mt-5 border-l-2 border-[hsl(var(--accent))] pl-3"><div className="eyebrow text-[hsl(var(--muted-foreground))]">why this score</div>{score.reasons.map((reason) => <p data-testid="text-score-reason" key={reason} className="mt-1 text-[12px] leading-5 text-[hsl(var(--foreground)/.78)]">{reason}</p>)}</div></div></section>;
}

export function ScoreHistory({ activeIndex }: { activeIndex: number }) {
  return <section className="panel reveal reveal-4" data-testid="panel-score-history"><div className="panel-head flex items-center gap-2"><Activity size={15} className="text-[hsl(var(--chart-2))]" /><span className="eyebrow text-[hsl(var(--muted-foreground))]">score progression</span></div><div className="p-[18px]"><div className="flex h-24 items-end gap-2">{demoScenario.rounds.map((round, index) => <div key={round.strategy.revision} className="flex flex-1 flex-col items-center gap-2"><span data-testid={`text-history-score-${index}`} className={`font-mono-ui text-[10px] ${index === activeIndex ? "font-bold text-[hsl(var(--primary))]" : "text-[hsl(var(--muted-foreground))]"}`}>{round.score.progress_score}</span><div className="relative flex h-16 w-full items-end rounded-t bg-[hsl(var(--muted)/.55)]"><div className={`w-full rounded-t transition-[height] duration-500 ${index === activeIndex ? "bg-[hsl(var(--accent))]" : "bg-[hsl(var(--chart-2)/.7)]"}`} style={{ height: `${round.score.progress_score}%` }} /></div><span className="font-mono-ui text-[9px] text-[hsl(var(--muted-foreground))]">REV {round.strategy.revision}</span></div>)}</div></div></section>;
}

export function StrategyHistory({ activeIndex }: { activeIndex: number }) {
  return <section className="panel reveal reveal-4" data-testid="panel-strategy-history"><div className="panel-head flex items-center gap-2"><GitBranch size={15} className="text-[hsl(var(--chart-3))]" /><span className="eyebrow text-[hsl(var(--muted-foreground))]">strategy lineage</span></div><div className="p-[18px]"><div className="space-y-2">{demoScenario.rounds.map((round, index) => <div data-testid={`lineage-row-${index}`} key={round.strategy.revision} className={`flex items-center gap-3 rounded-lg border p-2.5 transition-colors ${index === activeIndex ? "border-[hsl(var(--accent)/.7)] bg-[hsl(var(--accent)/.1)]" : "border-[hsl(var(--border))]"}`}><span className="grid size-6 place-items-center rounded bg-[hsl(var(--primary))] font-mono-ui text-[10px] text-[hsl(var(--accent))]">{String.fromCharCode(65 + index)}</span><div className="min-w-0 flex-1"><div className="flex items-center gap-2 font-mono-ui text-[10px]"><strong>revision {round.strategy.revision}</strong>{round.strategy.parent_revision && <span className="text-[hsl(var(--muted-foreground))]">← parent {round.strategy.parent_revision}</span>}</div><p className="mt-1 truncate text-[11px] text-[hsl(var(--muted-foreground))]">{round.strategy.objective}</p></div>{index === activeIndex && <span className="size-1.5 rounded-full bg-[hsl(var(--accent))]" />}</div>)}</div></div></section>;
}

export function SystemStatus({ status }: { status: ReplayStatus }) {
  return <section className="panel reveal reveal-4" data-testid="panel-system-status"><div className="panel-head flex items-center gap-2"><ShieldCheck size={15} className="text-[hsl(var(--chart-2))]" /><span className="eyebrow text-[hsl(var(--muted-foreground))]">system status</span></div><div className="divide-y divide-[hsl(var(--border))]"><StatusRow icon={<CircleDot size={13} />} label="replay source" value="deterministic fixture" tone="good" /><StatusRow icon={<WifiOff size={13} />} label="Muteki upstream" value="unavailable by design" tone="neutral" /><StatusRow icon={<TriangleAlert size={13} />} label="failure handling" value="error / timeout safe" tone="neutral" /><StatusRow icon={<span className={status === "RUNNING" ? "pulse-signal" : ""}><CircleDot size={13} /></span>} label="loop state" value={status.toLowerCase()} tone={status === "COMPLETED" ? "good" : status === "RUNNING" ? "active" : "neutral"} /></div></section>;
}

function StatusRow({ icon, label, value, tone }: { icon: ReactNode; label: string; value: string; tone: "good" | "active" | "neutral" }) {
  const color = tone === "good" ? "text-[hsl(var(--chart-2))]" : tone === "active" ? "text-[hsl(var(--accent))]" : "text-[hsl(var(--muted-foreground))]";
  return <div className="flex items-center justify-between gap-2 px-[18px] py-3"><div className={`flex items-center gap-2 ${color}`} >{icon}<span className="text-[11px] text-[hsl(var(--foreground)/.76)]">{label}</span></div><span data-testid={`status-${label.replaceAll(" ", "-")}`} className="font-mono-ui text-[10px] text-[hsl(var(--muted-foreground))]">{value}</span></div>;
}

export function FailureStates({ activeState, onSelect }: { activeState: SafeState | null; onSelect: (state: SafeState) => void }) {
  const states: { id: SafeState; label: string; detail: string; icon: ReactNode }[] = [
    { id: "evaluator-failure", label: "evaluator failure", detail: "keeps the run unsolved", icon: <AlertTriangle size={13} /> },
    { id: "timeout", label: "timeout", detail: "closes the bounded run", icon: <TimerOff size={13} /> },
    { id: "incomplete-evidence", label: "incomplete evidence", detail: "does not fabricate confidence", icon: <Copy size={13} /> },
    { id: "muteki-unavailable", label: "Muteki unavailable", detail: "preserves last known state", icon: <WifiOff size={13} /> },
    { id: "empty-events", label: "empty event stream", detail: "shows an honest empty state", icon: <Clock3 size={13} /> },
  ];
  return <section className="mt-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-5" data-testid="panel-safe-states">{states.map((state) => <button type="button" data-testid={`button-safe-state-${state.id}`} aria-pressed={activeState === state.id} onClick={() => onSelect(state.id)} key={state.id} className={`state-chip text-left transition-colors ${activeState === state.id ? "border-[hsl(var(--accent)/.8)] bg-[hsl(var(--accent)/.12)]" : "hover:border-[hsl(var(--foreground)/.3)]"}`}>{state.icon}<span><strong>{state.label}</strong><small>{state.detail}</small></span></button>)}</section>;
}

export function SafeStateBanner({ state, onClear }: { state: SafeState | null; onClear: () => void }) {
  if (!state) return null;
  const messages: Record<SafeState, string> = {
    "muteki-unavailable": "Muteki is currently unavailable.",
    "evaluator-failure": "Evaluation unavailable.",
    "incomplete-evidence": "Evidence incomplete.",
    timeout: "Investigation timed out.",
    "empty-events": "No investigation events received yet.",
  };
  return <div role="alert" data-testid="status-safe-state" className="mt-3 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-[hsl(var(--chart-3)/.35)] bg-[hsl(var(--chart-3)/.08)] px-3 py-2.5 text-[11px] text-[hsl(var(--foreground)/.82)]"><span className="flex items-center gap-2"><TriangleAlert size={14} className="text-[hsl(var(--chart-3))]" />{messages[state]}</span><button type="button" data-testid="button-clear-safe-state" onClick={onClear} className="font-mono-ui text-[10px] uppercase tracking-[.1em] text-[hsl(var(--primary))] underline decoration-[hsl(var(--accent)/.6)] underline-offset-4">Clear preview</button></div>;
}