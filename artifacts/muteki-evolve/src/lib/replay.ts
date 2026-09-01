export type ReplayStatus = "IDLE" | "RUNNING" | "PAUSED" | "COMPLETED";
export type SafeState =
  | "muteki-unavailable"
  | "evaluator-failure"
  | "incomplete-evidence"
  | "timeout"
  | "empty-events";

export type SandboxTarget = {
  id: string;
  name: string;
  description: string;
  runtime_reference: string;
};

export type Strategy = {
  objective: string;
  priorities: string[];
  constraints: string[];
  context?: Record<string, string>;
  revision: number;
  parent_revision?: number;
};

export type InvestigationEvent = {
  sequence: number;
  timestamp: string;
  type: string;
  summary: string;
  worker?: string;
  status: "complete" | "signal" | "verified";
};

export type Evidence = {
  type: string;
  summary: string;
  confidence: number;
  source_event?: number;
};

export type InvestigationResult = {
  run_id: string;
  events: InvestigationEvent[];
  evidence: Evidence[];
  final_summary?: string;
  solved?: boolean;
  error?: string;
};

export type ScoreReport = {
  progress_score: number;
  progress_level: string;
  reasons: string[];
  solved: boolean;
  stagnated?: boolean;
};

export type ReplayRound = {
  strategy: Strategy;
  result: InvestigationResult;
  score: ScoreReport;
};

export type ReplayScenario = {
  target: SandboxTarget;
  rounds: ReplayRound[];
};

export const demoScenario: ReplayScenario = {
  target: {
    id: "trusted-demo-target",
    name: "Trusted demo sandbox",
    description: "A deterministic local fixture target for contract tests.",
    runtime_reference: "mock://trusted-demo-target",
  },
  rounds: [
    {
      strategy: {
        objective: "Build an initial understanding of the trusted sandbox.",
        priorities: ["reconnaissance", "evidence collection"],
        constraints: ["stay within the trusted sandbox"],
        revision: 1,
      },
      result: {
        run_id: "mock-c1",
        events: [
          { sequence: 1, timestamp: "2025-03-08T14:02:04Z", type: "observation", summary: "Sandbox surface enumerated; three relevant services are in scope.", worker: "worker-01", status: "complete" },
          { sequence: 2, timestamp: "2025-03-08T14:02:11Z", type: "signal", summary: "A repeating access pattern appears in the service trace.", worker: "worker-02", status: "signal" },
        ],
        evidence: [
          { type: "surface map", summary: "Three services respond inside the trusted boundary.", confidence: .83, source_event: 1 },
        ],
        final_summary: "Initial surface understanding established.",
        solved: false,
      },
      score: {
        progress_score: 28,
        progress_level: "reconnaissance",
        reasons: ["Initial surface understanding is useful."],
        solved: false,
      },
    },
    {
      strategy: {
        objective: "Correlate the strongest evidence and test the leading hypothesis.",
        priorities: ["evidence correlation", "hypothesis testing"],
        constraints: ["preserve the trusted target boundary"],
        context: { based_on: "round-1" },
        revision: 2,
        parent_revision: 1,
      },
      result: {
        run_id: "mock-c1",
        events: [
          { sequence: 3, timestamp: "2025-03-08T14:02:23Z", type: "correlation", summary: "Access pattern aligns with the service trace from round one.", worker: "worker-01", status: "complete" },
          { sequence: 4, timestamp: "2025-03-08T14:02:31Z", type: "hypothesis", summary: "Leading hypothesis tested against the correlated trace.", worker: "worker-03", status: "signal" },
        ],
        evidence: [
          { type: "correlated trace", summary: "The repeating access pattern is linked to the same service boundary.", confidence: .91, source_event: 3 },
          { type: "hypothesis test", summary: "The leading hypothesis holds across the observed trace.", confidence: .76, source_event: 4 },
        ],
        final_summary: "Evidence is correlated, but success is not verified.",
        solved: false,
      },
      score: {
        progress_score: 72,
        progress_level: "strong evidence",
        reasons: ["Evidence is correlated but success is not verified."],
        solved: false,
      },
    },
    {
      strategy: {
        objective: "Verify the success condition using the strongest evidence.",
        priorities: ["verification", "clear success evidence"],
        constraints: ["stop after verified success"],
        context: { based_on: "round-2" },
        revision: 3,
        parent_revision: 2,
      },
      result: {
        run_id: "mock-c1",
        events: [
          { sequence: 5, timestamp: "2025-03-08T14:02:45Z", type: "verification", summary: "Success condition checked against the strongest correlated trace.", worker: "worker-03", status: "complete" },
          { sequence: 6, timestamp: "2025-03-08T14:02:52Z", type: "resolution", summary: "Independent verification confirms the expected sandbox state.", worker: "worker-01", status: "verified" },
        ],
        evidence: [
          { type: "verified condition", summary: "The expected sandbox state is confirmed by an independent check.", confidence: .98, source_event: 6 },
          { type: "resolution signal", summary: "No additional investigation is required within the trusted boundary.", confidence: .95, source_event: 5 },
        ],
        final_summary: "The success condition is verified.",
        solved: true,
      },
      score: {
        progress_score: 100,
        progress_level: "verified success",
        reasons: ["The success condition is verified."],
        solved: true,
      },
    },
  ],
};
