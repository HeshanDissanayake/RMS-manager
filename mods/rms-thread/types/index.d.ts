/** What `rms whoami --json` answers for this session (null: not an RMS project). */
export type RmsInfo = {
  project: string
  root: string
  session: string | null
  thread: string | null
  thread_name: string | null
  side: string | null
  worktree: string | null
  handover_due: boolean
}

declare module 'claude-code' {
  interface PluginState {
    'rms-thread': { info: RmsInfo | null }
  }
}
