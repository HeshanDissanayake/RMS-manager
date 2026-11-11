// rms-thread: RMS (Research Manager) in the Claude Code terminal.
//
// - A session that starts in an RMS project (a folder with rms.json above it)
//   and holds no thread gets a picker: "Which thread will this session work on?".
//   The pick runs `rms claim T<NN>` for this session; RMS's own UserPromptSubmit
//   hook then tells Claude on the next prompt.
// - Right above the prompt: the held thread's number and name (or "no thread",
//   with a button to pick one). /thread picks or shows it again.
//
// All state comes from the rms CLI; the mod keeps only a copy to draw from.

import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { RmsInfo } from '../types'

const info = atom({ plugin: 'rms-thread', key: 'info' } as const, null)

// The usual install (this repo's .venv); $RMS_BIN overrides, `rms` on PATH is the fallback.
const DEFAULT_RMS = '/home/heshds/working_dir/LLVM-regsw/side/RMS-manager/.venv/bin/rms'
const NO_THREAD = 'No thread now'

type Thread = {
  id: string
  name: string
  status: string
  done: boolean
  claim: 'free' | 'live' | 'stale'
  held_by: string | null
}

// The rms program: $RMS_BIN, else the usual install, else `rms` on PATH.
async function rms($: EngineInterface, args: string[]) {
  const set = await $.env.get('RMS_BIN')
  const sid = await $.session.id()
  for (const bin of set ? [set] : [DEFAULT_RMS, 'rms']) {
    try {
      return await $.process.run([bin, ...args], {
        env: { CLAUDE_CODE_SESSION_ID: sid },
        timeoutMs: 30000,
      })
    } catch {
      // cannot start: try the next; none: behave as outside RMS
    }
  }
  return null
}

async function refresh($: EngineInterface): Promise<RmsInfo | null> {
  const r = await rms($, ['whoami', '--json'])
  let v: RmsInfo | null = null
  if (r && r.exitCode === 0) {
    try {
      v = JSON.parse(r.stdout) as RmsInfo
    } catch {
      v = null
    }
  }
  await update($, info, () => v)
  return v
}

function label(t: Thread): string {
  const name = t.name.length > 34 ? t.name.slice(0, 33) + '…' : t.name
  return `${t.id} ${name}`
}

function firstLine(text: string): string {
  return text.replace(/^rms:\s*/, '').split('\n')[0] ?? ''
}

async function threadsOf($: EngineInterface): Promise<Thread[]> {
  const r = await rms($, ['threads', '--json'])
  if (!r || r.exitCode !== 0) return []
  try {
    return JSON.parse(r.stdout) as Thread[]
  } catch {
    return []
  }
}

// Ask which thread, claim it; up to three tries (a refused claim asks again).
async function pick($: EngineInterface): Promise<string> {
  for (let attempt = 0; attempt < 3; attempt++) {
    const all = await threadsOf($)
    const open = all
      .filter(t => !t.done && t.claim !== 'live')
      .sort((a, b) => Number(a.claim !== 'free') - Number(b.claim !== 'free'))
    const options = [...open.slice(0, 3).map(label), NO_THREAD]
    let answer: string
    try {
      answer = await $.ui.ask('Which thread will this session work on?', {
        options,
        header: 'RMS thread',
      })
    } catch {
      return 'No thread picked.'
    }
    if (answer === NO_THREAD) return 'No thread picked.'
    const m = /\bT\d+\b/i.exec(answer)
    if (!m) return 'No thread picked (type a thread ID like T04 under Other).'
    const id = m[0].toUpperCase()
    const t = all.find(one => one.id === id)
    const args = ['claim', id]
    if (t?.claim === 'stale') {
      const yes = await $.ui
        .ask(`${id} has a stale claim by ${t.held_by}. Take it over?`, ['Take it over', 'Pick another'])
        .catch(() => 'Pick another')
      if (yes !== 'Take it over') continue
      args.push('--take-over')
    }
    if (t?.done) {
      const yes = await $.ui
        .ask(`${id} is done. Work on it again?`, ['Work on it', 'Pick another'])
        .catch(() => 'Pick another')
      if (yes !== 'Work on it') continue
      args.push('--allow-done')
    }
    const r = await rms($, args)
    if (r && r.exitCode === 0) {
      const v = await refresh($)
      const msg = `Claimed ${id}${v?.thread_name ? ` (${v.thread_name})` : ''}.`
      $.ui.toast(msg)
      return msg
    }
    $.ui.toast(r ? firstLine(r.stderr || r.stdout) : 'rms did not run')
  }
  return 'No thread claimed.'
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.command.register({
      name: 'thread',
      description: "RMS: pick this session's thread, or show the one it holds",
    })
    const started = await next(e)
    if (!e.isInteractive) return started
    const v = await refresh($)
    if (v && !v.thread && !v.side) {
      // after the start, so the dialog has a screen to open on
      $.clock.after(500, () => {
        void pick($)
      })
    }
    return started
  })

  on('command.run', { command: 'thread' }, async $ => {
    const v = await refresh($)
    if (!v) return { text: 'Not in an RMS project (no rms.json above this folder).' }
    if (v.thread) {
      return {
        text:
          `This session holds ${v.thread} (${v.thread_name}).` +
          (v.worktree ? ` Checkout: ${v.worktree}.` : '') +
          ' To change it, hand it over first (rms handover).',
      }
    }
    return { text: await pick($) }
  })

  // Claude may claim, hand over or release with rms commands: re-read after each turn.
  on('turn.complete', async ($, e, next) => {
    const done = await next(e)
    await refresh($)
    return done
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    const v = await read($, info)
    if (!v || e.props.hasSurvey) return next(e)
    const { Box, Button, Text } = $.ui.resolve(e)

    if (v.thread) {
      return (
        <Box>
          <Text color="cyan" weight="bold">
            {`RMS ${v.thread}`}
          </Text>
          <Text>{` ${v.thread_name ?? ''}`}</Text>
          {v.worktree ? <Text dimColor>{`  ·  ${v.worktree}`}</Text> : null}
          {v.handover_due ? <Text color="yellow">{'  ·  handover due'}</Text> : null}
        </Box>
      )
    }
    if (v.side) {
      return (
        <Box>
          <Text dimColor>{`RMS  ·  side session side/${v.side}/ (no thread)`}</Text>
        </Box>
      )
    }
    return (
      <Box>
        <Text color="yellow">{'RMS  ·  no thread  '}</Text>
        <Button key="pick" label="Pick a thread" onPress={() => void pick($)} />
      </Box>
    )
  })
}
