// The run explorer, ported from RUN_EXPLORER_JS (runner/report.py:933-1354).
//
// The behavioural change is what it loads, not what it does. report.py inlined all 713 transcripts —
// 15.5 MB — to render an 11.8 KB grid. Here the grid comes from the embedded RunIndex, a run's detail
// is fetched when it is opened, and its transcript only when the full trace is opened.
//
// DRAWER_JS is not ported: it targets #rundrawer/#dtitle/#mbody, which report.py never emitted. The
// drawer that actually runs is this file's run panel.
import { useCallback, useEffect, useMemo, useRef, useState } from 'preact/hooks';

import type { BuildManifest, Provenance, RunCell, RunDetail, RunIndex, RunTranscript, Verdict } from '../shared/contract';
import { runKey } from '../shared/contract';
import { fmtTime, modelEffortLabel } from '../shared/format';
import { loadRunDetail, loadTranscript } from '../shared/resources';
import { Nav, ReportFooter } from '../ui/Chrome';

const FOCUS_KEY = 'appcontrolbench:run-focus:v1';

/** What a cell shows: a verdict, or where the run is in the pipeline. */
type CellState = Verdict | 'pending' | 'scoring' | 'stale' | 'annulled';

type Config = {
  id: string;
  modelId: string;
  toolId: string;
  base: string;
  effort: string | null;
  effortRank: number;
  toolLabel: string;
  toolShort: string;
};

type Selection =
  | { level: 'field'; taskId: string; configId: null }
  | { level: 'task'; taskId: string; configId: null }
  | { level: 'run'; taskId: string; configId: string };

type Filters = { query: string; suite: string; outcome: 'all' | 'mixed' | 'awaiting' };

const EM_DASH = /[–—]/g;
/** report.py's clean(): en/em dashes are normalised before display. */
const clean = (s: unknown): string => String(s ?? '').replace(EM_DASH, '-');

function configLabel(c: Config): string {
  return modelEffortLabel(c.base, c.effort);
}
function configSub(c: Config): string {
  return c.toolLabel;
}

function stateFromCell(cell: RunCell | undefined): CellState {
  if (!cell || cell.lifecycle === 'pending') return 'pending';
  if (cell.lifecycle === 'stale') return 'stale';
  if (cell.lifecycle === 'completed') return 'scoring';
  return cell.verdict ?? 'pending';
}

export type TaskSummary = Record<CellState, number>;

export function summarizeTask(
  taskId: string,
  configs: readonly Pick<Config, 'id'>[],
  displayState: (taskId: string, configId: string) => CellState,
): TaskSummary {
  const summary: TaskSummary = {
    success: 0,
    partial: 0,
    fail: 0,
    error: 0,
    pending: 0,
    scoring: 0,
    stale: 0,
    annulled: 0,
  };
  for (const config of configs) summary[displayState(taskId, config.id)] += 1;
  return summary;
}

const stateLabel: Record<CellState, string> = {
  success: 'passed',
  partial: 'partial',
  fail: 'failed',
  error: 'error',
  pending: 'pending',
  scoring: 'scoring',
  stale: 'stale',
  annulled: 'annulled',
};

function taskSummaryText(summary: TaskSummary): string {
  return (Object.keys(summary) as CellState[])
    .filter((state) => summary[state] > 0)
    .map((state) => `${summary[state]} ${stateLabel[state]}`)
    .join(' · ');
}

const outcomeLabel: Record<Filters['outcome'], string> = {
  all: 'all outcomes',
  mixed: 'mixed outcomes',
  awaiting: 'awaiting results',
};

function Chip({ state }: { state: CellState }) {
  const label = state === 'scoring' ? 'scoring' : state === 'pending' ? 'not run' : state;
  return (
    <span class={`rx-chip rx-chip-${state}`}>
      <i />
      {label}
    </span>
  );
}

function ConfigHead({ config }: { config: Config }) {
  return (
    <>
      <span class="rx-config-name">{configLabel(config)}</span>
      <span class="rx-config-sub">{configSub(config)}</span>
    </>
  );
}

export function ExplorerPage({
  runIndex,
  manifest,
  provenance,
}: {
  runIndex: RunIndex;
  manifest: BuildManifest;
  provenance: Provenance;
}) {
  const { catalog } = runIndex;

  const configs = useMemo<Config[]>(
    () =>
      catalog.models.flatMap((model) =>
        catalog.tools.map((tool) => ({
          id: `${model.id}__${tool.id}`,
          modelId: model.id,
          toolId: tool.id,
          base: model.base,
          effort: model.effort,
          effortRank: model.effortRank,
          toolLabel: tool.label,
          toolShort: tool.id === runIndex.scoring.baselineToolId ? 'no tool' : tool.id,
        })),
      ),
    [catalog, runIndex.scoring.baselineToolId],
  );

  const cellIndex = useMemo(() => {
    const map = new Map<string, RunCell>();
    for (const cell of runIndex.cells) map.set(runKey(cell.modelId, cell.toolId, cell.taskId), cell);
    return map;
  }, [runIndex.cells]);

  const cellAt = useCallback(
    (taskId: string, configId: string): RunCell | undefined => cellIndex.get(`${configId}__${taskId}`),
    [cellIndex],
  );

  // report.py:2422 — a task appears only if it is annulled or has at least one run that is not pending.
  // Q_EXPLORER shipped that filtered list; here it is derived from the index.
  const tasks = useMemo(() => {
    const live = new Set<string>();
    for (const cell of runIndex.cells) if (cell.lifecycle !== 'pending') live.add(cell.taskId);
    return catalog.tasks.filter((task) => task.annulled || live.has(task.id));
  }, [catalog.tasks, runIndex.cells]);

  const taskById = useMemo(() => new Map(tasks.map((t) => [t.id, t])), [tasks]);
  const cfgById = useMemo(() => new Map(configs.map((c) => [c.id, c])), [configs]);

  const displayState = useCallback(
    (taskId: string, configId: string): CellState =>
      taskById.get(taskId)?.annulled ? 'annulled' : stateFromCell(cellAt(taskId, configId)),
    [taskById, cellAt],
  );

  const [focus, setFocus] = useState<string[]>(() => configs.slice(0, 5).map((c) => c.id));
  const [maxFocus, setMaxFocus] = useState(5);
  const [filters, setFilters] = useState<Filters>({ query: '', suite: 'all', outcome: 'all' });
  const [selection, setSelection] = useState<Selection | null>(null);
  const [expandedTaskId, setExpandedTaskId] = useState<string | null>(null);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [traceOpen, setTraceOpen] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const panelRef = useRef<HTMLElement | null>(null);
  const panelReturnFocusRef = useRef<HTMLElement | null>(null);
  const toastTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const announce = useCallback((message: string) => {
    setToast(clean(message));
    clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(null), 2200);
  }, []);

  // Width and localStorage are browser-only, so the saved focus set is restored after mount. The
  // pre-rendered markup shows the first five configurations, which is report.py's own fallback.
  useEffect(() => {
    const width = rootRef.current?.clientWidth || window.innerWidth;
    const fits = Math.min(6, Math.max(1, Math.floor((width - 376) / 144)));
    setMaxFocus(fits);
    let stored: unknown = [];
    try {
      stored = JSON.parse(localStorage.getItem(FOCUS_KEY) || '[]');
    } catch {
      stored = [];
    }
    const saved = Array.isArray(stored)
      ? (stored as string[]).filter((id, i, a) => cfgById.has(id) && a.indexOf(id) === i).slice(0, fits)
      : [];
    setFocus(saved.length ? saved : configs.slice(0, Math.min(5, fits)).map((c) => c.id));
  }, [cfgById, configs]);

  const saveFocus = useCallback((next: string[]) => {
    try {
      localStorage.setItem(FOCUS_KEY, JSON.stringify(next));
    } catch {
      /* private mode, quota — the focus set simply does not persist */
    }
  }, []);

  // ---- URL state. Same three params report.py used, so deep links keep working. ----
  const readUrl = useCallback((): Selection | null => {
    const q = new URLSearchParams(location.search);
    const level = q.get('level');
    const taskId = q.get('task');
    const configId = q.get('config');
    if (!taskId || !taskById.has(taskId)) return null;
    if (level !== 'run' && level !== 'task' && level !== 'field') return null;
    if (level === 'run') {
      if (!configId || !cfgById.has(configId)) return null;
      return { level, taskId, configId };
    }
    return { level, taskId, configId: null };
  }, [taskById, cfgById]);

  useEffect(() => {
    setSelection(readUrl());
    const onPop = () => setSelection(readUrl());
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, [readUrl]);

  const panelOpen = selection !== null;
  useEffect(() => {
    if (panelOpen) {
      panelReturnFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      const frame = requestAnimationFrame(() => panelRef.current?.focus());
      return () => cancelAnimationFrame(frame);
    }
    panelReturnFocusRef.current?.focus();
    panelReturnFocusRef.current = null;
  }, [panelOpen]);

  const select = useCallback((next: Selection | null, sync = true) => {
    setSelection(next);
    setTraceOpen(false);
    if (!sync) return;
    const q = new URLSearchParams(location.search);
    for (const name of ['level', 'task', 'config']) q.delete(name);
    if (next) {
      q.set('level', next.level);
      q.set('task', next.taskId);
      if (next.configId) q.set('config', next.configId);
    }
    const query = q.toString();
    history.replaceState(null, '', location.pathname + (query ? `?${query}` : ''));
  }, []);

  // ---- filtering (report.py:1019) ----
  const visible = useMemo(() => {
    const q = filters.query.trim().toLowerCase();
    return tasks.filter((task) => {
      if (filters.suite !== 'all' && task.app !== filters.suite) return false;
      if (q && ![task.id, task.kind, task.app, task.prompt].join(' ').toLowerCase().includes(q)) return false;
      if (filters.outcome === 'all') return true;
      const states = configs.map((c) => stateFromCell(cellAt(task.id, c.id)));
      if (filters.outcome === 'mixed') {
        const judged = states.filter((s) => s === 'success' || s === 'partial' || s === 'fail');
        return new Set(judged).size >= 2;
      }
      return states.some((s) => s === 'pending' || s === 'scoring' || s === 'stale');
    });
  }, [tasks, configs, filters, cellAt]);

  const promote = useCallback(
    (configId: string) => {
      if (focus.includes(configId)) return;
      if (focus.length >= maxFocus) {
        announce(`This width fits ${maxFocus} focus columns. Remove one before adding another.`);
        return;
      }
      const next = [...focus, configId];
      setFocus(next);
      saveFocus(next);
      setPickerOpen(false);
    },
    [focus, maxFocus, announce, saveFocus],
  );

  const removeFocus = useCallback(
    (configId: string) => {
      if (focus.length <= 1) {
        announce('Keep at least one configuration in focus.');
        return;
      }
      const next = focus.filter((id) => id !== configId);
      setFocus(next);
      saveFocus(next);
    },
    [focus, announce, saveFocus],
  );

  const moveTask = useCallback(
    (delta: number) => {
      if (!selection) return;
      const i = tasks.findIndex((t) => t.id === selection.taskId);
      const next = tasks[i + delta];
      if (!next) return;
      select(
        selection.level === 'run'
          ? { level: 'run', taskId: next.id, configId: selection.configId }
          : { level: selection.level, taskId: next.id, configId: null },
      );
    },
    [selection, tasks, select],
  );

  const moveConfig = useCallback(
    (delta: number) => {
      if (!selection || selection.level !== 'run') return;
      const i = configs.findIndex((c) => c.id === selection.configId);
      const next = configs[i + delta];
      if (!next) return;
      select({ level: 'run', taskId: selection.taskId, configId: next.id });
    },
    [selection, configs, select],
  );

  // ---- keyboard nav (report.py:1341) ----
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      if (e.key === 'Escape' && pickerOpen) {
        e.preventDefault();
        setPickerOpen(false);
        return;
      }
      if (target && (/^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName) || target.isContentEditable)) return;
      if (e.key === 'Escape' && selection) {
        e.preventDefault();
        select(null);
        return;
      }
      if (!selection) return;
      if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
        e.preventDefault();
        moveTask(e.key === 'ArrowUp' ? -1 : 1);
      } else if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
        e.preventDefault();
        moveConfig(e.key === 'ArrowLeft' ? -1 : 1);
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [selection, pickerOpen, select, moveTask, moveConfig]);

  const restCount = Math.max(0, configs.length - focus.length);
  const othersWide = restCount ? restCount * 4 + (restCount - 1) * 2 + 16 : 16;
  const othersCompact = restCount ? restCount * 3 + (restCount - 1) * 2 + 16 : 16;
  const suites = useMemo(() => [...new Set(tasks.map((t) => t.app))], [tasks]);

  return (
    <>
      <Nav page="explorer" />
      <main id="top">
        <section class="runs-section" id="runs">
          <header class="rx-page-head">
            <h1>Run explorer</h1>
            <p>Discover how configurations handled each task. Choose a small focus set, then inspect every remaining configuration in the field.</p>
          </header>
          <div id="run-explorer-root" ref={rootRef}>
            <TaskFilters
              filters={filters}
              suites={suites}
              visibleCount={visible.length}
              totalCount={tasks.length}
              onQuery={(query) => setFilters((f) => ({ ...f, query }))}
              onSuite={(suite) => setFilters((f) => ({ ...f, suite }))}
              onOutcome={(outcome) => setFilters((f) => ({ ...f, outcome }))}
            />
            <section class="rx-frame">
              <div
                class={selection ? 'rx-workspace has-panel' : 'rx-workspace'}
                style={{
                  '--rx-focus-count': focus.length,
                  '--rx-others-wide': `${othersWide}px`,
                  '--rx-task-wide': `${352 - othersWide}px`,
                  '--rx-others-compact': `${othersCompact}px`,
                  '--rx-task-compact': `${274 - othersCompact}px`,
                }}
              >
                <div class="rx-table-area">
                  <Toolbar
                    configs={configs}
                    cfgById={cfgById}
                    focus={focus}
                    pickerOpen={pickerOpen}
                    onTogglePicker={() => setPickerOpen((o) => !o)}
                    onClosePicker={() => setPickerOpen(false)}
                    onPromote={promote}
                    onRemove={removeFocus}
                  />
                  <div class="rx-matrix rx-desktop-only">
                    <Matrix
                      list={visible}
                      focus={focus}
                      cfgById={cfgById}
                      configs={configs}
                      restCount={restCount}
                      selection={selection}
                      cellAt={cellAt}
                      displayState={displayState}
                      onOpen={select}
                      onFieldHelp={() =>
                        announce('Each field strip opens the configurations outside focus for that task.')
                      }
                      onResetFilters={() => setFilters({ query: '', suite: 'all', outcome: 'all' })}
                    />
                  </div>
                  <div class="rx-mobile-task-list">
                    <MobileTaskList
                      list={visible}
                      configs={configs}
                      cellAt={cellAt}
                      selection={selection}
                      displayState={displayState}
                      expandedTaskId={expandedTaskId}
                      onToggleTask={(taskId) => setExpandedTaskId((current) => (current === taskId ? null : taskId))}
                      onOpenRun={(taskId, configId) => select({ level: 'run', taskId, configId })}
                      onResetFilters={() => setFilters({ query: '', suite: 'all', outcome: 'all' })}
                    />
                  </div>
                </div>
                <aside
                  ref={panelRef}
                  class={selection?.level !== 'run' ? 'rx-panel is-field' : 'rx-panel'}
                  aria-label="Run explorer details"
                  role={selection ? 'dialog' : undefined}
                  hidden={!selection}
                  tabIndex={-1}
                >
                  {selection && (
                    <Panel
                      selection={selection}
                      manifest={manifest}
                      scoringWeights={runIndex.scoring.weights}
                      configs={configs}
                      cfgById={cfgById}
                      tasks={tasks}
                      taskById={taskById}
                      focus={focus}
                      cellAt={cellAt}
                      displayState={displayState}
                      traceOpen={traceOpen}
                      onToggleTrace={() => setTraceOpen((t) => !t)}
                      onSelect={select}
                      onMoveTask={moveTask}
                      onPromote={promote}
                      onOpenPicker={() => setPickerOpen(true)}
                      announce={announce}
                    />
                  )}
                </aside>
              </div>
            </section>
          </div>
          <div id="run-peek" class="rx-peek" role="tooltip" hidden />
          <div id="run-toast" class="rx-toast" role="status" hidden={!toast}>
            {toast}
          </div>
        </section>
      </main>
      <ReportFooter provenance={provenance} />
    </>
  );
}

// ---------------------------------------------------------------------------- filters + toolbar + picker

function FilterControls({
  filters,
  suites,
  onSuite,
  onOutcome,
}: {
  filters: Filters;
  suites: string[];
  onSuite: (suite: string) => void;
  onOutcome: (outcome: Filters['outcome']) => void;
}) {
  return (
    <>
      <label>
        App suite
        <select value={filters.suite} onChange={(e) => onSuite((e.target as HTMLSelectElement).value)}>
          <option value="all">all suites</option>
          {suites.map((suite) => (
            <option key={suite} value={suite}>
              {suite}
            </option>
          ))}
        </select>
      </label>
      <label>
        Show
        <select
          value={filters.outcome}
          onChange={(e) => onOutcome((e.target as HTMLSelectElement).value as Filters['outcome'])}
        >
          <option value="all">all outcomes</option>
          <option value="mixed">mixed outcomes</option>
          <option value="awaiting">awaiting results</option>
        </select>
      </label>
    </>
  );
}

function TaskFilters({
  filters,
  suites,
  visibleCount,
  totalCount,
  onQuery,
  onSuite,
  onOutcome,
}: {
  filters: Filters;
  suites: string[];
  visibleCount: number;
  totalCount: number;
  onQuery: (query: string) => void;
  onSuite: (suite: string) => void;
  onOutcome: (outcome: Filters['outcome']) => void;
}) {
  return (
    <div class="rx-filterbar">
      <label class="rx-task-search">
        Search tasks
        <input
          id="rx-query"
          type="search"
          placeholder="Task id or prompt"
          value={filters.query}
          onInput={(e) => onQuery((e.target as HTMLInputElement).value)}
        />
      </label>
      <div class="rx-filter-desktop">
        <FilterControls filters={filters} suites={suites} onSuite={onSuite} onOutcome={onOutcome} />
      </div>
      <details class="rx-filter-mobile">
        <summary>Filters · {outcomeLabel[filters.outcome]}</summary>
        <div>
          <FilterControls filters={filters} suites={suites} onSuite={onSuite} onOutcome={onOutcome} />
        </div>
      </details>
      <span class="rx-task-count">
        {visibleCount} of {totalCount} tasks
      </span>
    </div>
  );
}

function Toolbar({
  configs,
  cfgById,
  focus,
  pickerOpen,
  onTogglePicker,
  onClosePicker,
  onPromote,
  onRemove,
}: {
  configs: Config[];
  cfgById: Map<string, Config>;
  focus: string[];
  pickerOpen: boolean;
  onTogglePicker: () => void;
  onClosePicker: () => void;
  onPromote: (id: string) => void;
  onRemove: (id: string) => void;
}) {
  const [search, setSearch] = useState('');
  const available = configs.filter((c) => !focus.includes(c.id));
  const q = search.toLowerCase();
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const searchRef = useRef<HTMLInputElement | null>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!pickerOpen) return;
    returnFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    searchRef.current?.focus();
  }, [pickerOpen]);

  useEffect(() => {
    if (!pickerOpen) return;
    const onPointerDown = (e: PointerEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) onClosePicker();
    };
    document.addEventListener('pointerdown', onPointerDown);
    return () => document.removeEventListener('pointerdown', onPointerDown);
  }, [pickerOpen, onClosePicker]);

  return (
    <div class="rx-toolbar">
      <div class="rx-focus-desktop">
        <span class="rx-toolbar-label">Comparing</span>
        <div class="rx-focus-list">
          {focus.map((id) => {
            const c = cfgById.get(id);
            if (!c) return null;
            return (
              <span class="rx-focus-pill" key={id}>
                <span>
                  {configLabel(c)} <small>{configSub(c)}</small>
                </span>
                <button
                  type="button"
                  aria-label={`Remove ${configLabel(c)} from focus`}
                  onClick={() => onRemove(id)}
                >
                  ×
                </button>
              </span>
            );
          })}
        </div>
      </div>
      <div class="rx-add-wrap" ref={wrapRef}>
        <button
          class="rx-add rx-add-desktop"
          type="button"
          aria-expanded={pickerOpen}
          onClick={onTogglePicker}
        >
          Add more  
        </button>
        {pickerOpen && (
          <div
            class="rx-picker"
            role="dialog"
            aria-label="Add a configuration to focus"
            onKeyDown={(event) => {
              if (event.key !== 'Escape') return;
              event.preventDefault();
              event.stopPropagation();
              onClosePicker();
              requestAnimationFrame(() => returnFocusRef.current?.focus());
            }}
          >
            <label>
              Find configuration
              <input
                id="rx-picker-search"
                type="search"
                placeholder="model, effort, or harness"
                value={search}
                onInput={(e) => setSearch((e.target as HTMLInputElement).value)}
                ref={searchRef}
              />
            </label>
            <div class="rx-picker-list">
              {available.length ? (
                available.map((c) => (
                  <button
                    key={c.id}
                    type="button"
                    hidden={q ? !`${configLabel(c)} ${configSub(c)}`.toLowerCase().includes(q) : false}
                    onClick={() => onPromote(c.id)}
                  >
                    <span>
                      <ConfigHead config={c} />
                    </span>
                  </button>
                ))
              ) : (
                <p>Every configuration is already in focus.</p>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------- matrix

type TaskRow = RunIndex['catalog']['tasks'][number];

function Matrix({
  list,
  focus,
  cfgById,
  configs,
  restCount,
  selection,
  cellAt,
  displayState,
  onOpen,
  onFieldHelp,
  onResetFilters,
}: {
  list: TaskRow[];
  focus: string[];
  cfgById: Map<string, Config>;
  configs: Config[];
  restCount: number;
  selection: Selection | null;
  cellAt: (taskId: string, configId: string) => RunCell | undefined;
  displayState: (taskId: string, configId: string) => CellState;
  onOpen: (next: Selection | null) => void;
  onFieldHelp: () => void;
  onResetFilters: () => void;
}) {
  const metric = (cell: RunCell | undefined) => (cell?.wallSeconds != null ? fmtTime(cell.wallSeconds) : 'n/a');
  const rest = configs.filter((c) => !focus.includes(c.id));
  return (
    <>
      <div class="rx-grid rx-column-head">
        <div>Task</div>
        {focus.map((id) => {
          const c = cfgById.get(id);
          return <div key={id}>{c && <ConfigHead config={c} />}</div>;
        })}
        <button type="button" onClick={onFieldHelp}>
          <span class="rx-config-name">Other configurations</span>
          <span class="rx-config-sub" aria-hidden="true">
            &nbsp;
          </span>
        </button>
      </div>
      <div class="rx-rows">
        {list.length === 0 ? (
          <div class="rx-empty">
            <b>No tasks match these filters.</b>
            <button type="button" onClick={onResetFilters}>
              Reset filters
            </button>
          </div>
        ) : (
          list.map((task, index) => (
            <div
              key={task.id}
              class={selection?.taskId === task.id ? 'rx-grid rx-row is-selected' : 'rx-grid rx-row'}
              data-task={task.id}
              style={{ '--rx-row-index': index }}
            >
              <div class="rx-task-cell">
                <span>
                  <b>{task.id}</b>
                  <small>{task.kind}</small>
                </span>
                <span>{clean(task.prompt)}</span>
              </div>
              {focus.map((id) => {
                const c = cfgById.get(id);
                const cell = cellAt(task.id, id);
                const state = displayState(task.id, id);
                return (
                  <button
                    key={id}
                    type="button"
                    class="rx-result-cell"
                    data-task={task.id}
                    data-config={id}
                    aria-label={`${c ? configLabel(c) : id} ${state} ${metric(cell)}`}
                    onClick={() => onOpen({ level: 'run', taskId: task.id, configId: id })}
                  >
                    <Chip state={state} />
                    <span class="rx-cell-metric">{metric(cell)}</span>
                  </button>
                );
              })}
              <button
                type="button"
                class="rx-field-strip"
                aria-label={`Open ${restCount} configurations outside focus for ${task.id}`}
                onClick={() => onOpen({ level: 'field', taskId: task.id, configId: null })}
              >
                <span>
                  {rest.map((c) => (
                    <i key={c.id} class={`rx-tick rx-tick-${displayState(task.id, c.id)}`} />
                  ))}
                </span>
              </button>
            </div>
          ))
        )}
      </div>
    </>
  );
}

function MobileTaskList({
  list,
  configs,
  cellAt,
  selection,
  displayState,
  expandedTaskId,
  onToggleTask,
  onOpenRun,
  onResetFilters,
}: {
  list: TaskRow[];
  configs: Config[];
  cellAt: (taskId: string, configId: string) => RunCell | undefined;
  selection: Selection | null;
  displayState: (taskId: string, configId: string) => CellState;
  expandedTaskId: string | null;
  onToggleTask: (taskId: string) => void;
  onOpenRun: (taskId: string, configId: string) => void;
  onResetFilters: () => void;
}) {
  if (!list.length) {
    return (
      <div class="rx-empty">
        <b>No tasks match these filters.</b>
        <button type="button" onClick={onResetFilters}>
          Reset filters
        </button>
      </div>
    );
  }

  return (
    <div class="rx-mobile-rows">
      {list.map((task) => {
        const summary = summarizeTask(task.id, configs, displayState);
        const summaryText = taskSummaryText(summary);
        const expanded =
          expandedTaskId === task.id || (selection?.level !== 'run' && selection?.taskId === task.id);
        return (
          <article
            class={expanded || selection?.taskId === task.id ? 'rx-mobile-task is-selected' : 'rx-mobile-task'}
            key={task.id}
          >
            <button
              class="rx-mobile-task-open"
              type="button"
              onClick={() => onToggleTask(task.id)}
              aria-expanded={expanded}
              aria-label={`${expanded ? 'Collapse' : 'Open'} results for ${task.id}: ${summaryText}`}
            >
              <span class="rx-mobile-task-meta">
                <b>{task.id}</b>
                <small>
                  {task.kind} · {task.app}
                </small>
              </span>
              <span class="rx-mobile-task-prompt">{clean(task.prompt)}</span>
              <span class="rx-mobile-task-summary">{summaryText}</span>
              <span
                class="rx-mobile-result-map"
                role="img"
                aria-label={`Outcome map: ${summaryText} across ${configs.length} configurations`}
              >
                {configs.map((config) => (
                  <i key={config.id} class={`rx-tick rx-tick-${displayState(task.id, config.id)}`} />
                ))}
              </span>
            </button>
            {expanded && (
              <div class="rx-mobile-task-details">
                <div class="rx-mobile-detail-head">
                  <b>{configs.length} configurations</b>
                  <span>Tap a result to inspect its run</span>
                </div>
                <div class="rx-mobile-run-list">
                  {configs.map((config) => {
                    const cell = cellAt(task.id, config.id);
                    const state = displayState(task.id, config.id);
                    return (
                      <button
                        key={config.id}
                        type="button"
                        onClick={() => onOpenRun(task.id, config.id)}
                        aria-label={`Open ${configLabel(config)} ${configSub(config)} run for ${task.id}: ${stateLabel[state]}`}
                      >
                        <span class="rx-mobile-config">
                          <span class="rx-mobile-config-name">{configLabel(config)}</span>
                          <span class="rx-mobile-config-tool">{config.toolLabel}</span>
                        </span>
                        <Chip state={state} />
                        <small>{cell?.wallSeconds != null ? fmtTime(cell.wallSeconds) : 'n/a'}</small>
                      </button>
                    );
                  })}
                </div>
              </div>
            )}
          </article>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------- panels

function Panel(props: {
  selection: Selection;
  manifest: BuildManifest;
  scoringWeights: RunIndex['scoring']['weights'];
  configs: Config[];
  cfgById: Map<string, Config>;
  tasks: TaskRow[];
  taskById: Map<string, TaskRow>;
  focus: string[];
  cellAt: (taskId: string, configId: string) => RunCell | undefined;
  displayState: (taskId: string, configId: string) => CellState;
  traceOpen: boolean;
  onToggleTrace: () => void;
  onSelect: (next: Selection | null) => void;
  onMoveTask: (delta: number) => void;
  onPromote: (id: string) => void;
  onOpenPicker: () => void;
  announce: (message: string) => void;
}) {
  if (props.selection.level === 'task') return <TaskPanel {...props} taskId={props.selection.taskId} />;
  if (props.selection.level === 'field') return <FieldPanel {...props} taskId={props.selection.taskId} />;
  return (
    <RunPanel
      {...props}
      key={`${props.selection.taskId}__${props.selection.configId}`}
      taskId={props.selection.taskId}
      configId={props.selection.configId}
    />
  );
}

function PanelHead({
  children,
  nav,
  onClose,
}: {
  children: preact.ComponentChildren;
  nav?: preact.ComponentChildren;
  onClose: () => void;
}) {
  return (
    <div class="rx-panel-head">
      <div>{children}</div>
      {nav && <span class="rx-panel-nav">{nav}</span>}
      <button type="button" onClick={onClose} aria-label="Close details">
        <svg width="15" height="15" viewBox="0 0 15 15" fill="none" xmlns="http://www.w3.org/2000/svg">
          <path
            d="M11.7816 4.03157C12.0062 3.80702 12.0062 3.44295 11.7816 3.2184C11.5571 2.99385 11.193 2.99385 10.9685 3.2184L7.50005 6.68682L4.03164 3.2184C3.80708 2.99385 3.44301 2.99385 3.21846 3.2184C2.99391 3.44295 2.99391 3.80702 3.21846 4.03157L6.68688 7.49999L3.21846 10.9684C2.99391 11.193 2.99391 11.557 3.21846 11.7816C3.44301 12.0061 3.80708 12.0061 4.03164 11.7816L7.50005 8.31316L10.9685 11.7816C11.193 12.0061 11.5571 12.0061 11.7816 11.7816C12.0062 11.557 12.0062 11.193 11.7816 10.9684L8.31322 7.49999L11.7816 4.03157Z"
            fill="currentColor"
            fill-rule="evenodd"
            clip-rule="evenodd"
          />
        </svg>
      </button>
    </div>
  );
}

function scoreOf(cell: RunCell | undefined, weights: RunIndex['scoring']['weights']): number | null {
  if (cell?.verdict === 'success' || cell?.verdict === 'partial' || cell?.verdict === 'fail') {
    return weights[cell.verdict];
  }
  return null;
}

function median(values: number[]): number | null {
  if (!values.length) return null;
  values.sort((a, b) => a - b);
  const mid = Math.floor(values.length / 2);
  return values.length % 2 ? values[mid] : (values[mid - 1] + values[mid]) / 2;
}

function signed(value: number): string {
  return `${value > 0 ? '+' : ''}${value}`;
}

function TaskPanel({
  taskId,
  configs,
  tasks,
  taskById,
  scoringWeights,
  cellAt,
  displayState,
  onSelect,
  onMoveTask,
  onOpenPicker,
  announce,
}: Parameters<typeof Panel>[0] & { taskId: string }) {
  const task = taskById.get(taskId)!;
  const taskIndex = tasks.findIndex((item) => item.id === taskId);
  const byBase = new Map<string, Config[]>();
  const effortRanks = new Map<string | null, number>();
  const tools = new Map<string, Pick<Config, 'toolId' | 'toolLabel' | 'toolShort'>>();
  const byTool = new Map<string, { label: string; scores: number[] }>();
  const byEffort = new Map<string, { rank: number; scores: number[] }>();
  const wallSeconds: number[] = [];
  let pass = 0;

  for (const config of configs) {
    const cell = cellAt(taskId, config.id);
    const baseConfigs = byBase.get(config.base) ?? [];
    baseConfigs.push(config);
    byBase.set(config.base, baseConfigs);
    effortRanks.set(config.effort, Math.min(effortRanks.get(config.effort) ?? Infinity, config.effortRank));
    tools.set(config.toolId, config);
    if (cell?.verdict === 'success') pass += 1;
    if (cell?.wallSeconds != null) wallSeconds.push(cell.wallSeconds);

    const score = scoreOf(cell, scoringWeights);
    if (score == null) continue;
    const tool = byTool.get(config.toolId) ?? { label: config.toolLabel, scores: [] };
    tool.scores.push(score);
    byTool.set(config.toolId, tool);
    if (config.effort) {
      const effort = byEffort.get(config.effort) ?? { rank: config.effortRank, scores: [] };
      effort.scores.push(score);
      byEffort.set(config.effort, effort);
    }
  }

  const toolScores = [...byTool.values()]
    .map((tool) => ({ ...tool, value: tool.scores.reduce((sum, score) => sum + score, 0) / tool.scores.length }))
    .sort((a, b) => a.value - b.value);
  const worstTool = toolScores[0];
  const bestTool = toolScores.at(-1);
  const effortScores = [...byEffort.values()].sort((a, b) => a.rank - b.rank);
  const lowEffort = effortScores[0];
  const highEffort = effortScores.at(-1);
  const effortDelta =
    lowEffort && highEffort && lowEffort !== highEffort
      ? Math.round(
          (highEffort.scores.reduce((sum, score) => sum + score, 0) / highEffort.scores.length -
            lowEffort.scores.reduce((sum, score) => sum + score, 0) / lowEffort.scores.length) *
            100,
        )
      : null;
  const worst =
    worstTool && bestTool
      ? `${worstTool.label} ${signed(Math.round((worstTool.value - bestTool.value) * 100))}`
      : 'no judged runs';
  const effort = effortDelta == null ? 'effort n/a' : `effort ${signed(effortDelta)}`;
  const medianSeconds = median(wallSeconds);
  const med = medianSeconds == null ? 'median n/a' : `median ${fmtTime(medianSeconds)}`;
  const efforts = [...effortRanks.keys()].sort(
    (a, b) => (effortRanks.get(a) ?? Infinity) - (effortRanks.get(b) ?? Infinity),
  );
  const axes = efforts.flatMap((axisEffort) =>
    [...tools.values()].map((tool) => ({ effort: axisEffort, tool })),
  );
  const firstFailure = configs.find((config) => cellAt(taskId, config.id)?.verdict === 'fail');

  return (
    <>
      <PanelHead
        onClose={() => onSelect(null)}
        nav={
          <>
            <button type="button" disabled={taskIndex <= 0} onClick={() => onMoveTask(-1)} aria-label="Previous task">
              ‹
            </button>
            <button
              type="button"
              disabled={taskIndex < 0 || taskIndex >= tasks.length - 1}
              onClick={() => onMoveTask(1)}
              aria-label="Next task"
            >
              ›
            </button>
          </>
        }
      >
        <span class="rx-breadcrumb">{taskId}</span>
      </PanelHead>
      <div class="rx-panel-scroll">
        <section class="rx-task-summary">
          <h2>{taskId}</h2>
          <span>
            {task.kind} / {task.app}
          </span>
          <p>{clean(task.prompt)}</p>
          <div class="rx-summary-chips">
            <b>
              {pass}/{configs.length} pass
            </b>
            <b class="is-negative">{worst}</b>
            <b class={effortDelta != null && effortDelta >= 0 ? 'is-positive' : 'is-negative'}>{effort}</b>
            <b>{med}</b>
          </div>
        </section>
        <section class="rx-panel-section">
          <div class="rx-fingerprint" style={{ '--rx-fp-cols': axes.length }}>
            <div class="rx-fp-head">model</div>
            {axes.map((axis) => (
              <div
                class="rx-fp-head"
                key={`${axis.effort ?? 'default'}__${axis.tool.toolId}`}
                title={`${axis.effort ?? 'default'} / ${axis.tool.toolId}`}
              >
                {axis.tool.toolShort}
              </div>
            ))}
            {[...byBase].flatMap(([base, baseConfigs]) => [
              <div class="rx-fp-model" key={`${base}__label`}>
                {base}
              </div>,
              ...axes.map((axis) => {
                const config = baseConfigs.find(
                  (item) => item.effort === axis.effort && item.toolId === axis.tool.toolId,
                );
                return config ? (
                  <button
                    type="button"
                    class={`rx-fp-cell rx-tick-${displayState(taskId, config.id)}`}
                    key={`${base}__${axis.effort ?? 'default'}__${axis.tool.toolId}`}
                    title={`${configLabel(config)} ${configSub(config)}`}
                    aria-label={`Open ${configLabel(config)} ${configSub(config)} run`}
                    onClick={() => onSelect({ level: 'run', taskId, configId: config.id })}
                  />
                ) : (
                  <span
                    class="rx-fp-cell rx-tick-pending"
                    key={`${base}__${axis.effort ?? 'default'}__${axis.tool.toolId}`}
                  />
                );
              }),
            ])}
          </div>
          <div class="rx-fp-legend">
            → effort × harness&nbsp;&nbsp; / &nbsp;&nbsp;↓ {byBase.size} models&nbsp;&nbsp; / &nbsp;&nbsp;all{' '}
            {configs.length} configurations
          </div>
        </section>
        <section class="rx-panel-section">
          <header>
            <b>BY MODEL</b>
          </header>
          {[...byBase].map(([base, baseConfigs]) => (
            <div class="rx-model-row" key={base}>
              <b>{base}</b>
              <span>
                {baseConfigs.map((config) => (
                  <i
                    class={`rx-model-dot rx-tick-${displayState(taskId, config.id)}`}
                    key={config.id}
                    title={configSub(config)}
                  />
                ))}
              </span>
              <small>
                {baseConfigs.filter((config) => cellAt(taskId, config.id)?.verdict === 'success').length}/
                {baseConfigs.length}
              </small>
            </div>
          ))}
        </section>
        <div class="rx-panel-actions">
          <button
            class="is-primary"
            type="button"
            onClick={() =>
              firstFailure
                ? onSelect({ level: 'run', taskId, configId: firstFailure.id })
                : announce('No failed run is recorded for this task.')
            }
          >
            open first failure
          </button>
          <button type="button" onClick={onOpenPicker}>
            add a config to focus
          </button>
        </div>
      </div>
    </>
  );
}


function FieldPanel({
  taskId,
  configs,
  focus,
  cellAt,
  displayState,
  onSelect,
  onPromote,
}: Parameters<typeof Panel>[0] & { taskId: string }) {
  const order: Record<string, number> = {
    fail: 0,
    partial: 1,
    error: 2,
    stale: 3,
    scoring: 4,
    pending: 5,
    success: 6,
  };
  const rest = configs
    .filter((c) => !focus.includes(c.id))
    .sort((a, b) => (order[displayState(taskId, a.id)] ?? 9) - (order[displayState(taskId, b.id)] ?? 9));
  return (
    <>
      <PanelHead onClose={() => onSelect(null)}>
        <span class="rx-crumb-link">{taskId}</span>
      </PanelHead>
      <div class="rx-field-title">
        <h2>Other configurations</h2>
      </div>
      <div class="rx-field-list">
        {rest.length ? (
          rest.map((c) => {
            const cell = cellAt(taskId, c.id);
            return (
              <div class="rx-field-row" key={c.id}>
                <button
                  type="button"
                  class="rx-field-main"
                  onClick={() => onSelect({ level: 'run', taskId, configId: c.id })}
                >
                  <span>
                    <ConfigHead config={c} />
                  </span>
                  <Chip state={displayState(taskId, c.id)} />
                  <small>{cell?.wallSeconds != null ? fmtTime(cell.wallSeconds) : 'n/a'}</small>
                </button>
                <button
                  class="rx-promote"
                  type="button"
                  onClick={() => onPromote(c.id)}
                  aria-label={`Promote ${configLabel(c)} into focus`}
                >
                  +
                </button>
              </div>
            );
          })
        ) : (
          <div class="rx-panel-empty">No configurations remain outside focus.</div>
        )}
      </div>
      <div class="rx-panel-footer">a row opens that run · + promotes it into a focus column</div>
    </>
  );
}

type LoadState<T> = { status: 'loading' } | { status: 'ready'; data: T } | { status: 'error'; message: string };
type DeferredLoadState<T> = LoadState<T> | { status: 'idle' };

function loadErrorMessage(error: unknown): string {
  return clean(error instanceof Error ? error.message : error);
}

function RunPanel({
  taskId,
  configId,
  manifest,
  cfgById,
  cellAt,
  displayState,
  traceOpen,
  onToggleTrace,
  onSelect,
}: Parameters<typeof Panel>[0] & { taskId: string; configId: string }) {
  const c = cfgById.get(configId);
  const cell = cellAt(taskId, configId);
  const state = displayState(taskId, configId);

  const [detailState, setDetailState] = useState<LoadState<RunDetail>>({ status: 'loading' });
  const [transcriptState, setTranscriptState] = useState<DeferredLoadState<RunTranscript>>({ status: 'idle' });

  // One request per opened run, and only when it is opened. report.py inlined every run's facts and
  // every transcript into the page instead.
  useEffect(() => {
    let live = true;
    setDetailState({ status: 'loading' });
    setTranscriptState({ status: 'idle' });
    if (!c) {
      setDetailState({ status: 'error', message: 'Configuration not found.' });
      return;
    }
    loadRunDetail(manifest, c.modelId, c.toolId, taskId)
      .then((d) => {
        if (live) setDetailState({ status: 'ready', data: d });
      })
      .catch((error: unknown) => {
        if (live) setDetailState({ status: 'error', message: loadErrorMessage(error) });
      });
    return () => {
      live = false;
    };
  }, [c, taskId, manifest]);

  // The trace is a second request, made only when the reader opens it. A run with no transcript has
  // href null and must never produce a request (contract line 247).
  useEffect(() => {
    if (!traceOpen || detailState.status !== 'ready' || !detailState.data.transcript.href) return;
    let live = true;
    setTranscriptState({ status: 'loading' });
    loadTranscript(detailState.data.transcript.href)
      .then((t) => {
        if (live) setTranscriptState({ status: 'ready', data: t });
      })
      .catch((error: unknown) => {
        if (live) setTranscriptState({ status: 'error', message: loadErrorMessage(error) });
      });
    return () => {
      live = false;
    };
  }, [traceOpen, detailState]);

  const metric = cell?.wallSeconds != null ? fmtTime(cell.wallSeconds) : 'n/a';

  return (
    <>
      <PanelHead onClose={() => onSelect(null)}>
        <button
          class="rx-crumb-link"
          type="button"
          onClick={() => onSelect({ level: 'task', taskId, configId: null })}
        >
          {taskId}
        </button>
        <span class="rx-breadcrumb">▸ run</span>
      </PanelHead>
      <div class="rx-panel-scroll">
        <section class="rx-run-title">
          <h2>{c && configLabel(c)}</h2>
          <span>{c && configSub(c)}</span>
          <div>
            <Chip state={state} />
            <b>{metric}</b>
          </div>
        </section>
        {detailState.status === 'loading' ? (
          <div class="rx-panel-empty">Loading run details…</div>
        ) : detailState.status === 'error' ? (
          <div class="rx-panel-empty">Could not load run details: {detailState.message}</div>
        ) : (
          <>
            <section class="rx-judge">
              <b>JUDGE</b>
              <p>{clean(detailState.data.run.reason || 'No judge verdict recorded.')}</p>
            </section>
            <section class="rx-panel-section">
              <header>
                <b>STEPS</b>
                <span>
                  {detailState.data.steps.length} of {detailState.data.steps.length}
                </span>
              </header>
              <div class="rx-steps">
                {detailState.data.steps.length ? (
                  detailState.data.steps.map((step, index) => (
                    <div
                      key={step.index}
                      class={
                        detailState.data.run.verdict === 'fail' && index === detailState.data.steps.length - 1
                          ? 'rx-step is-terminal'
                          : 'rx-step'
                      }
                    >
                      <span>{step.index}</span>
                      <i />
                      <b>{clean(step.label)}</b>
                      <small>{step.elapsedSeconds == null ? '' : fmtTime(step.elapsedSeconds)}</small>
                    </div>
                  ))
                ) : (
                  <div class="rx-panel-empty">No tool-call steps recorded.</div>
                )}
              </div>
            </section>
            <section class="rx-panel-section">
              <header>
                <b>FINAL FRAME</b>
              </header>
              <div class="rx-run-media">
                {/* The screenshot is requested only because this drawer rendered. */}
                {detailState.data.run.screenshotHref ? (
                  <img
                    src={detailState.data.run.screenshotHref}
                    alt={`Final screenshot for ${c && configLabel(c)} ${taskId}`}
                  />
                ) : (
                  <div class="rx-media-placeholder">No final screenshot recorded</div>
                )}
              </div>
            </section>
            {traceOpen && (
              <section class="rx-panel-section">
                <header>
                  <b>FULL TRACE</b>
                  <span>{detailState.data.transcript.eventCount} events</span>
                </header>
                <Trace detail={detailState.data} transcriptState={transcriptState} />
              </section>
            )}
            <div class="rx-panel-actions">
              <button class="is-primary" type="button" onClick={onToggleTrace}>
                {traceOpen ? 'hide full trace' : 'open full trace'}
              </button>
            </div>
          </>
        )}
      </div>
    </>
  );
}

function Trace({
  detail,
  transcriptState,
}: {
  detail: RunDetail;
  transcriptState: DeferredLoadState<RunTranscript>;
}) {
  if (!detail.transcript.href) return <div class="rx-panel-empty">No transcript recorded.</div>;
  if (transcriptState.status === 'idle' || transcriptState.status === 'loading') {
    return <div class="rx-panel-empty">Loading transcript…</div>;
  }
  if (transcriptState.status === 'error') {
    return <div class="rx-panel-empty">Could not load transcript: {transcriptState.message}</div>;
  }
  if (!transcriptState.data.events.length) return <div class="rx-panel-empty">No transcript recorded.</div>;
  return (
    <div class="rx-trace">
      {transcriptState.data.events.map((entry, i) => {
        if (entry.k === 'u') {
          const input = (entry.i ?? {}) as { description?: unknown; command?: unknown };
          let raw = typeof input.description === 'string' ? input.description.trim() : '';
          if (!raw && typeof input.command === 'string') raw = input.command.trim().split('\n')[0];
          return (
            <details key={i}>
              <summary>
                <b>{clean(entry.n || 'tool')}</b>
                <span>{clean(raw || entry.n || 'tool call')}</span>
              </summary>
              <pre>{clean(entry.o || 'No output recorded')}</pre>
            </details>
          );
        }
        return (
          <details key={i}>
            <summary>
              <b>{entry.k === 'r' ? 'reasoning' : 'assistant'}</b>
              <span>{clean((entry.x || '').slice(0, 76))}</span>
            </summary>
            <pre>{clean(entry.x || '')}</pre>
          </details>
        );
      })}
    </div>
  );
}
