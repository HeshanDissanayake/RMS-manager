import { expect, test } from 'claude-code/testing'

const BAND = {
  plugin: 'rms-thread',
  component: 'AbovePrompt',
  props: { hasSurvey: false, isCollapsed: false, isWorking: false, view: {} },
} as const

function engine(on: any, whoami: object | null) {
  // Each hook stands for the engine beneath the mod.
  on('command.register', () => ({ value: { command: 'thread' } }))
  on('session.start', (_: unknown, e: { cwd: string }) => ({ cwd: e.cwd }))
  on('session.id', () => ({ value: 'test-session' }))
  on('env.get', () => ({ value: undefined }))
  on('clock.after', () => ({ value: { cancel() {} } }))
  on('process.run', (_: unknown, e: { argv: string[] }) =>
    whoami && e.argv.includes('whoami')
      ? { value: { exitCode: 0, stdout: JSON.stringify(whoami), stderr: '' } }
      : { value: { exitCode: 1, stdout: '', stderr: 'rms: no rms.json' } },
  )
}

const HOLDS = {
  project: 'demo', root: '/p', session: 'S-TEST', thread: 'T01',
  thread_name: 'fast sort', side: null, worktree: 'store/worktrees/T01/', handover_due: true,
  usage: { total: 939812, cost: 0.47, models: [{ model: 'opus 5.5', pct: 91.8 }, { model: 'haiku 5.5', pct: 8.2 }] },
}

test('the band shows the held thread', async ($, on) => {
  engine(on, HOLDS)
  await $.session.start({ cwd: '/p', surface: 'terminal', isInteractive: true })
  const ui = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect((await ui.find({ type: 'Text', text: /RMS T01/ }))?.text).toBe('RMS T01')
  expect((await ui.find({ type: 'Text', text: /fast sort/ }))?.text).toBe(' fast sort')
  expect(await ui.find({ type: 'Text', text: /handover due/ })).toBeTruthy()
  expect((await ui.find({ type: 'Text', text: /tok/ }))?.text).toBe('  ·  939.8k tok (opus 5.5 92%, haiku 5.5 8%)')
  await ui.unmount()
})

test('no thread: the band offers the picker', async ($, on) => {
  engine(on, { ...HOLDS, thread: null, thread_name: null, worktree: null, handover_due: false })
  await $.session.start({ cwd: '/p', surface: 'terminal', isInteractive: true })
  const ui = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await ui.find({ type: 'Text', text: /no thread/ })).toBeTruthy()
  expect(await ui.find({ type: 'Button', key: 'pick' })).toBeTruthy()
  await ui.unmount()
})

test('outside an RMS project the band stays out of the way', async ($, on) => {
  engine(on, null)
  on('ui.render', ($: any, e: any) => {
    const { Text } = $.ui.resolve(e)
    return <Text>engine</Text>
  })
  await $.session.start({ cwd: '/elsewhere', surface: 'terminal', isInteractive: true })
  const ui = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect((await ui.find({ type: 'Text', text: /engine/ }))?.text).toBe('engine')
  await ui.unmount()
})
