import { Router, type IRouter, type Request, type Response } from "express";

const router: IRouter = Router();

export interface InvestigationRunStateJSON {
  run_id: string;
  target_id: string;
  target_name: string;
  runtime_reference: string;
  mode: string;
  status: string;
  current_iteration: number;
  max_iterations: number;
  best_score: number;
  solved: boolean;
  termination_reason?: string;
  error?: string;
  history: Array<{
    iteration: number;
    strategy: {
      objective: string;
      priorities: string[];
      constraints: string[];
      revision: number;
      parent_revision?: number;
    };
    score: {
      progress_score: number;
      progress_level: string;
      reasons: string[];
      solved: boolean;
      stagnated?: boolean;
    };
    result: {
      run_id: string;
      solved: boolean;
      evidence_summary: string;
      progress_signals: string[];
    };
  }>;
}

// In-memory store for API server demonstration
const runStore = new Map<string, InvestigationRunStateJSON>();

// Populate default demo run if empty
runStore.set("mock-c1", {
  run_id: "mock-c1",
  target_id: "trusted-demo-target",
  target_name: "Trusted demo sandbox",
  runtime_reference: "mock://trusted-demo-target",
  mode: "mock",
  status: "SOLVED",
  current_iteration: 3,
  max_iterations: 3,
  best_score: 100,
  solved: true,
  termination_reason: "SOLVED",
  history: [
    {
      iteration: 1,
      strategy: {
        objective: "Build an initial understanding of the trusted sandbox.",
        priorities: ["reconnaissance", "evidence collection"],
        constraints: ["stay within the trusted sandbox"],
        revision: 1,
      },
      score: {
        progress_score: 28,
        progress_level: "reconnaissance",
        reasons: ["Initial surface understanding is useful."],
        solved: false,
      },
      result: {
        run_id: "mock-c1",
        solved: false,
        evidence_summary: "Useful initial understanding, but no verified success.",
        progress_signals: ["reconnaissance"],
      },
    },
    {
      iteration: 2,
      strategy: {
        objective: "Correlate the strongest evidence and test the leading hypothesis.",
        priorities: ["evidence correlation", "hypothesis testing"],
        constraints: ["preserve the trusted target boundary"],
        revision: 2,
        parent_revision: 1,
      },
      score: {
        progress_score: 72,
        progress_level: "strong evidence",
        reasons: ["Evidence is correlated but success is not verified."],
        solved: false,
      },
      result: {
        run_id: "mock-c1",
        solved: false,
        evidence_summary: "Strong evidence, but the success condition is not verified.",
        progress_signals: ["strong evidence"],
      },
    },
    {
      iteration: 3,
      strategy: {
        objective: "Verify the success condition using the strongest evidence.",
        priorities: ["verification", "clear success evidence"],
        constraints: ["stop after verified success"],
        revision: 3,
        parent_revision: 2,
      },
      score: {
        progress_score: 100,
        progress_level: "verified success",
        reasons: ["The success condition is verified."],
        solved: true,
      },
      result: {
        run_id: "mock-c1",
        solved: true,
        evidence_summary: "Verified success.",
        progress_signals: ["verified success"],
      },
    },
  ],
});

// POST /api/runs - Create & start investigation run
router.post("/runs", (req: Request, res: Response) => {
  const { target_id, objective, max_iterations = 3, mode = "mock" } = req.body || {};

  // Security check: Untrusted client target execution
  if (!target_id || target_id !== "trusted-demo-target") {
    res.status(400).json({
      error: `Target '${target_id}' is not present in trusted target registry.`,
    });
    return;
  }

  const run_id = `run-${Date.now()}`;
  const newRun: InvestigationRunStateJSON = {
    run_id,
    target_id,
    target_name: "Trusted demo sandbox",
    runtime_reference: "mock://trusted-demo-target",
    mode,
    status: "SOLVED",
    current_iteration: 3,
    max_iterations,
    best_score: 100,
    solved: true,
    termination_reason: "SOLVED",
    history: runStore.get("mock-c1")!.history,
  };

  runStore.set(run_id, newRun);
  res.status(201).json(newRun);
});

// GET /api/runs - List all runs
router.get("/runs", (_req: Request, res: Response) => {
  res.json(Array.from(runStore.values()));
});

// GET /api/runs/:id - Get state by run ID
router.get("/runs/:id", (req: Request, res: Response) => {
  const run = runStore.get(req.params["id"] as string);
  if (!run) {
    res.status(404).json({ error: "Run not found" });
    return;
  }
  res.json(run);
});

// GET /api/runs/:id/history - Get run history
router.get("/runs/:id/history", (req: Request, res: Response) => {
  const run = runStore.get(req.params["id"] as string);
  if (!run) {
    res.status(404).json({ error: "Run not found" });
    return;
  }
  res.json({
    run_id: run.run_id,
    history: run.history,
  });
});

// POST /api/runs/:id/cancel - Cancel active run
router.post("/runs/:id/cancel", (req: Request, res: Response) => {
  const run = runStore.get(req.params["id"] as string);
  if (!run) {
    res.status(404).json({ error: "Run not found" });
    return;
  }
  run.status = "CANCELLED";
  run.termination_reason = "CANCELLED";
  res.json(run);
});

export default router;
