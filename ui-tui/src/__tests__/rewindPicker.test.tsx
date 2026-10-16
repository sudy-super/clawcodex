import { PassThrough } from 'node:stream'

import { renderSync } from '@clawcodex/ink'
import React from 'react'
import { describe, expect, it, vi } from 'vitest'

import { RewindPicker } from '../components/rewindPicker.js'
import { stripAnsi } from '../lib/text.js'
import { DEFAULT_THEME } from '../theme.js'

const delay = () => new Promise(resolve => setTimeout(resolve, 50))

const points = [
  {
    message_id: 'old',
    text: 'First request',
    timestamp: null,
    files: [],
    added: 0,
    removed: 0,
    can_restore_code: false
  },
  {
    message_id: 'new',
    text: 'Change the file',
    timestamp: '2026-10-16',
    files: ['hello.ts'],
    added: 4,
    removed: 2,
    can_restore_code: true
  }
]

function mount(fail = false, rows = 40, history = points) {
  const stdin = new PassThrough()
  const stdout = new PassThrough()
  const stderr = new PassThrough()
  let output = ''
  stdout.on('data', chunk => {
    output += chunk.toString()
  })
  Object.assign(stdout, { columns: 100, rows })
  Object.assign(stdin, { isTTY: true, ref() {}, unref() {}, setRawMode() {} })
  const onCancel = vi.fn()
  const onRestored = vi.fn()

  const request = vi.fn((method: string) =>
    Promise.resolve(
      method === 'rewind.points'
        ? { ok: true, points: history }
        : fail
          ? { ok: false, error: 'File changed outside the agent' }
          : { ok: true, messages: [], prompt: 'Change the file', session_turns: 0 }
    )
  )

  const app = renderSync(
    <RewindPicker
      gw={{ request } as never}
      onCancel={onCancel}
      onRestored={onRestored}
      sessionId="s1"
      t={DEFAULT_THEME}
    />,
    {
      exitOnCtrlC: false,
      patchConsole: false,
      stdout: stdout as NodeJS.WriteStream,
      stderr: stderr as NodeJS.WriteStream,
      stdin: stdin as NodeJS.ReadStream
    }
  )

  return {
    app,
    request,
    onCancel,
    onRestored,
    frame: () => stripAnsi(output.split('\x1b[?2026h').at(-1)?.split('\x1b[?2026l')[0] ?? ''),
    async press(key: string) {
      stdin.write(key)
      await delay()
    }
  }
}

describe('rewind picker', () => {
  it('starts at current and Enter dismisses without changing code or conversation', async () => {
    const m = mount()

    try {
      await delay()
      expect(m.frame()).toContain('❯ (current)')
      expect(m.frame()).toContain('─')
      expect(m.frame()).not.toMatch(/[╔╗╚╝║│]/)
      await m.press('\r')
      expect(m.onCancel).toHaveBeenCalledOnce()
      expect(m.request).toHaveBeenCalledTimes(1)
      expect(m.onRestored).not.toHaveBeenCalled()
    } finally {
      m.app.unmount()
    }
  })

  it('keeps a bounded window including current on a 24-row terminal', async () => {
    const history = Array.from({ length: 20 }, (_, i) => ({ ...points[0]!, message_id: `m${i}`, text: `Request ${i}` }))
    const m = mount(false, 24, history)

    try {
      await delay()
      expect(m.frame()).toContain('❯ (current)')
      expect(m.frame()).toContain('↑ 19 more')
      expect(m.frame()).toContain('Request 19')
      expect(m.frame()).not.toContain('Request 18')
      await m.press('\x1b[A')
      await m.press('\x1b[A')
      await m.press('\x1b[A')
      expect(m.frame()).toContain('❯ Request 17')
      expect(m.frame()).toContain('↓ 3 more')
      expect(m.frame()).not.toContain('(current)')
      await m.press('\x1b[B')
      await m.press('\x1b[B')
      await m.press('\x1b[B')
      await m.press('\x1b[B')
      expect(m.frame()).toContain('❯ (current)')
      await m.press('\r')
      expect(m.onCancel).toHaveBeenCalledOnce()
      expect(m.request).toHaveBeenCalledTimes(1)
    } finally {
      m.app.unmount()
    }
  })

  it('moves from current to the latest message and confirms conversation-only restoration while keeping code', async () => {
    const m = mount()

    try {
      await delay()
      expect(m.frame()).toContain('❯ (current)')
      await m.press('\x1b[A')
      expect(m.frame()).toContain('❯ Change the file')
      await m.press('\r')
      expect(m.frame()).toContain('Restore code and conversation')
      expect(m.request).toHaveBeenCalledTimes(1)
      await m.press('\x1b[B')
      await m.press('\r')
      expect(m.request).toHaveBeenLastCalledWith('rewind.restore', {
        session_id: 's1',
        message_id: 'new',
        mode: 'conversation'
      })
      expect(m.onRestored).toHaveBeenCalledWith(expect.objectContaining({ ok: true }), 'conversation')
    } finally {
      m.app.unmount()
    }
  })
  it('moves between messages and excludes code restoration without a snapshot', async () => {
    const m = mount()

    try {
      await delay()
      await m.press('\x1b[A')
      await m.press('\x1b[A')
      await m.press('\r')
      expect(m.frame()).toContain('First request')
      expect(m.frame()).not.toContain('Restore code and conversation')
      await m.press('\r')
      expect(m.request).toHaveBeenLastCalledWith('rewind.restore', {
        session_id: 's1',
        message_id: 'old',
        mode: 'conversation'
      })
    } finally {
      m.app.unmount()
    }
  })
  it('restores both by default and lets Esc return without mutating', async () => {
    const m = mount()

    try {
      await delay()
      await m.press('\x1b[A')
      await m.press('\r')
      await m.press('\x1b')
      expect(m.request).toHaveBeenCalledTimes(1)
      expect(m.onRestored).not.toHaveBeenCalled()
      await m.press('\r')
      await m.press('\r')
      expect(m.request).toHaveBeenLastCalledWith('rewind.restore', expect.objectContaining({ mode: 'both' }))
    } finally {
      m.app.unmount()
    }
  })
  it('shows backend rejection without reporting success', async () => {
    const m = mount(true)

    try {
      await delay()
      await m.press('\x1b[A')
      await m.press('\r')
      await m.press('3')
      await m.press('\r')
      expect(m.request).toHaveBeenLastCalledWith('rewind.restore', expect.objectContaining({ mode: 'code' }))
      expect(m.frame()).toContain('File changed outside the agent')
      expect(m.onRestored).not.toHaveBeenCalled()
    } finally {
      m.app.unmount()
    }
  })
})
