export const meta = {
  name: 'genesis-2.0-parallel-tracks',
  description: 'Four parallel tracks: install hardening, Tauri shell, Ask-Claude copilot, strategy & risk hardening',
  phases: [
    { title: 'Install hardening' },
    { title: 'Tauri desktop shell' },
    { title: 'Ask-Claude copilot' },
    { title: 'Strategy & risk hardening' },
  ],
}

const TRACK_SCHEMA = {
  type: 'object',
  properties: {
    track: { type: 'string' },
    goal: { type: 'string' },
    current_state: { type: 'string' },
    changes_to_make: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          file: { type: 'string' },
          action: { type: 'string', enum: ['create', 'modify', 'delete'] },
          summary: { type: 'string' },
          size: { type: 'string', enum: ['S', 'M', 'L'] },
        },
        required: ['file', 'action', 'summary', 'size'],
      },
    },
    files_to_create: { type: 'array', items: { type: 'string' } },
    files_to_modify: { type: 'array', items: { type: 'string' } },
    estimated_total_loc: { type: 'number' },
    risks: { type: 'array', items: { type: 'string' } },
    dependencies: { type: 'array', items: { type: 'string' } },
    recommended_first_step: { type: 'string' },
  },
  required: [
    'track', 'goal', 'current_state', 'changes_to_make',
    'files_to_create', 'files_to_modify', 'estimated_total_loc',
    'risks', 'dependencies', 'recommended_first_step',
  ],
}

function prompt(req, files, goal, designNotes) {
  return [
    'You are working on the Genesis Trading Bot project at C:\\\\Users\\\\Moses Egbunike\\\\Documents\\\\Claude Code Projects\\\\Genesis.',
    '',
    'ABSOLUTE RULES:',
    '-  Use Read, Glob, Grep, and Bash with READ commands only.use Write, Edit, NotebookEdit, or any command that creates, modifies, or deletes files. Plan only.',
    '- Never print secret values. If a file contains secrets, describe structure only.',
    '- Use the structured output schema. Be specific: name real files, real config keys, real env vars.',
    '',
    'GOAL: ' + goal,
    '',
    'KEY FILES TO READ:',
    ...files.map(f => '- ' + f),
    '',
    req,
    '',
    designNotes,
    '',
    'DELIVER per the schema:',
    '- current_state: frank assessment',
    '- changes_to_make: concrete file + action + summary + size (S=under 50 LOC, M=50-200 LOC, L=over 200 LOC)',
    '- files_to_create / files_to_modify',
    '- estimated_total_loc',
    '- risks',
    '- dependencies',
    '- recommended_first_step: the single change that gives the biggest win',
    '',
    'No fluff, no speculation. Name real knobs and real files.',
  ].join('\n')
}

const tracks = [
  {
    key: 'install',
    phase: 'Install hardening',
    prompt: prompt(
      'Look for which install steps are vague or missing, which env vars lack defaults or validation, which Windows-only commands block non-Windows users, whether there is a first-run check script, whether MT5 missing is handled gracefully, how the dashboard URL is communicated to users, and whether there is a meaningful "this is wrong" error anywhere.',
      [
        'C:\\\\Users\\\\Moses Egbunike\\\\Documents\\\\Claude Code Projects\\\\Genesis\\\\README.md',
        'C:\\\\Users\\\\Moses Egbunike\\\\Documents\\\\Claude Code Projects\\\\Genesis\\\\requirements.txt',
        'C:\\\\Users\\\\Moses Egbunike\\\\Documents\\\\Claude Code Projects\\\\Genesis\\\\launcher.py',
        'C:\\\\Users\\\\Moses Egbunike\\\\Documents\\\\Claude Code Projects\\\\Genesis\\\\.env.example',
        'C:\\\\Users\\\\Moses Egbunike\\\\Documents\\\\Claude Code Projects\\\\Genesis\\\\main.py',
        'C:\\\\Users\\\\Moses Egbunike\\\\Documents\\\\Claude Code Projects\\\\Genesis\\\\bot\\\\config\\\\settings.py',
      ],
      'Make "fresh clone to running in paper mode" take no more than ~10 minutes for someone who is not the author. Today the README quickstart understates real friction: Windows-only copy-paste lines, .env.example must be committed and complete, no MT5 path validation, no graceful missing-MT5 messaging, profile files exist (.env.breakout, .env.scalper) but launcher.py uses pystray, no first-run check before the bot starts and crashes upstream, dashboard URL only printed as the LAST line of stdout.',
      'Consider whether setup.py or pyproject.toml is needed for a real installable package. Consider cross-platform support (Windows first, but Mac/Linux must not be silently broken).'
    ),
  },
  {
    key: 'tauri',
    phase: 'Tauri desktop shell',
    prompt: prompt(
      'Tauri 2.0 specifically. Windows-primary. Python bot is not replaced; only visually packaged by Tauri. Dashboard keeps listening on 127.0.0.1. Tray icon replacement is part of scope (pystray retires). Do not ship a release-installer in MVP; cargo tauri dev is the goal.',
      [
        'C:\\\\Users\\\\Moses Egbunike\\\\Documents\\\\Claude Code Projects\\\\Genesis\\\\launcher.py',
        'C:\\\\Users\\\\Moses Egbunike\\\\Documents\\\\Claude Code Projects\\\\Genesis\\\\main.py',
        'C:\\\\Users\\\\Moses Egbunike\\\\Documents\\\\Claude Code Projects\\\\Genesis\\\\dashboard\\\\backend\\\\main.py (first 80 lines)',
        'C:\\\\Users\\\\Moses Egbunike\\\\Documents\\\\Claude Code Projects\\\\Genesis\\\\dashboard\\\\frontend\\\\index.html (first 30 lines)',
        'C:\\\\Users\\\\Moses Egbunike\\\\Documents\\\\Claude Code Projects\\\\Genesis\\\\README.md (the launcher section)',
      ],
      'Replace the pystray-based launcher.py with a Tauri 2.0 desktop shell. The shell wraps the existing FastAPI dashboard at 127.0.0.1:8000 as a native webview window. A second window or sidebar holds the Ask-Claude panel later. The Tauri shell must NOT spawn or manage main.py directly; the Python bot still boots itself; Tauri is a UI wrapper only.',
      'Consider Windows MSBuild toolchain, WebView2 runtime install state (it ships with Win11 but not always), dev vs prod URL handling, build matrix, dev-time hot reload of the dashboard.'
    ),
  },
  {
    key: 'claude',
    phase: 'Ask-Claude copilot',
    prompt: prompt(
      'Read the full dashboard backend to understand existing routes, the DB layer to understand what context is queryable, the orchestrator to understand available runtime state. Frontend is vanilla JS (no React). Add anthropic SDK deps only when needed. Streaming responses are nice-to-have but not MVP. All queries must respect local data; no telemetry, no remote logging of prompts.',
      [
        'C:\\\\Users\\\\Moses Egbunike\\\\Documents\\\\Claude Code Projects\\\\Genesis\\\\dashboard\\\\backend\\\\main.py (full file)',
        'C:\\\\Users\\\\Moses Egbunike\\\\Documents\\\\Claude Code Projects\\\\Genesis\\\\dashboard\\\\frontend\\\\index.html',
        'C:\\\\Users\\\\Moses Egbunike\\\\Documents\\\\Claude Code Projects\\\\Genesis\\\\database\\\\db_manager.py',
        'C:\\\\Users\\\\Moses Egbunike\\\\Documents\\\\Claude Code Projects\\\\Genesis\\\\database\\\\models.py',
        'C:\\\\Users\\\\Moses Egbunike\\\\Documents\\\\Claude Code Projects\\\\Genesis\\\\bot\\\\risk\\\\risk_manager.py',
        'C:\\\\Users\\\\Moses Egbunike\\\\Documents\\\\Claude Code Projects\\\\Genesis\\\\bot\\\\risk\\\\performance_tracker.py',
        'C:\\\\Users\\\\Moses Egbunike\\\\Documents\\\\Claude Code Projects\\\\Genesis\\\\bot\\\\core\\\\orchestrator.py',
        'C:\\\\Users\\\\Moses Egbunike\\\\Documents\\\\Claude Code Projects\\\\Genesis\\\\requirements.txt',
      ],
      'Add an "Ask Claude" copilot panel in the dashboard. Single user only. Capabilities: plain-English questions about bot state, explanation of a recent trade (combining trade + signal from SQLite + last 20 log lines), Q&A grounded in the project actual settings (loaded from bot_state + pydantic settings). Must NOT require ANTHROPIC_API_KEY at install; features degrade gracefully with a Copilot-disabled UI state.',
      'Plan how the system prompt constructs context (recent trades + settings + log tail). Cap context at reasonable size. Plan defense against prompt injection from log lines. System prompt must require cited facts: when stating a stat, reference the underlying source (settings key, trade id, log line).'
    ),
  },
  {
    key: 'strategy',
    phase: 'Strategy & risk hardening',
    prompt: prompt(
      'The regime selector picks one of smart_trend, mean_reversion, scalper_momentum, session_breakout. Determine whether all four are physically reachable from the selector logic. DO NOT propose new strategies; only harden existing ones. If scalper_momentum or session_breakout are dead code, say so plainly.',
      [
        'C:\\\\Users\\\\Moses Egbunike\\\\Documents\\\\Claude Code Projects\\\\Genesis\\\\bot\\\\strategies\\\\strategy_selector.py',
        'C:\\\\Users\\\\Moses Egbunike\\\\Documents\\\\Claude Code Projects\\\\Genesis\\\\bot\\\\strategies\\\\smart_trend.py',
        'C:\\\\Users\\\\Moses Egbunike\\\\Documents\\\\Claude Code Projects\\\\Genesis\\\\bot\\\\strategies\\\\mean_reversion.py',
        'C:\\\\Users\\\\Moses Egbunike\\\\Documents\\\\Claude Code Projects\\\\Genesis\\\\bot\\\\strategies\\\\scalper_momentum.py',
        'C:\\\\Users\\\\Moses Egbunike\\\\Documents\\\\Claude Code Projects\\\\Genesis\\\\bot\\\\strategies\\\\session_breakout.py',
        'C:\\\\Users\\\\Moses Egbunike\\\\Documents\\\\Claude Code Projects\\\\Genesis\\\\bot\\\\strategies\\\\base_strategy.py',
        'C:\\\\Users\\\\Moses Egbunike\\\\Documents\\\\Claude Code Projects\\\\Genesis\\\\bot\\\\risk\\\\risk_manager.py',
        'C:\\\\Users\\\\Moses Egbunike\\\\Documents\\\\Claude Code Projects\\\\Genesis\\\\bot\\\\risk\\\\performance_tracker.py',
        'C:\\\\Users\\\\Moses Egbunike\\\\Documents\\\\Claude Code Projects\\\\Genesis\\\\bot\\\\risk\\\\news_filter.py',
        'C:\\\\Users\\\\Moses Egbunike\\\\Documents\\\\Claude Code Projects\\\\Genesis\\\\bot\\\\core\\\\orchestrator.py',
        'C:\\\\Users\\\\Moses Egbunike\\\\Documents\\\\Claude Code Projects\\\\Genesis\\\\backtest\\\\backtester.py',
        'C:\\\\Users\\\\Moses Egbunike\\\\Documents\\\\Claude Code Projects\\\\Genesis\\\\tests\\\\test_suite.py',
      ],
      'Hardening pass on the strategy & risk layer. Top opportunities: ADX regime thresholds (20/25) are they arbitrary or backed by data; risk knobs for real-money safety (max_daily_drawdown kill-switch wire path, max_open_positions, correlation filter coverage); news filter robustness (calendar fallback, late event fetches, pre-emptive vs reaction pause); emergency flatten path (kill switch design end-to-end through order_manager + Discord); MT5 disconnect handling in orchestrator (retries, reconciliation, notification); dead-code audit on scalper_momentum + session_breakout; test coverage in tests/test_suite.py.',
      'Largest risk is "backtest does not generalize"; every strategy change should be paired with a backtest checkpoint. Be conservative in scope. Flag dead code, fragile thresholds, missing kill-switch coverage honestly.'
    ),
  },
]

const results = await parallel(tracks.map(t => () => agent(t.prompt, {
  label: t.key,
  phase: t.phase,
  schema: TRACK_SCHEMA,
})))

return results.filter(Boolean)
