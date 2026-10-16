import { supportsHyperlinks } from '@clawcodex/ink'
import { describe, expect, it } from 'vitest'

import { linkOpenHotkey } from './linkAffordance.js'
import { isMac } from './platform.js'

describe('linkOpenHotkey', () => {
  it('advertises the platform click gesture when the terminal supports OSC 8', () => {
    const mod = isMac ? 'Cmd' : 'Ctrl'

    expect(linkOpenHotkey(true)).toEqual([
      `${mod}+click link`,
      `open link in browser (${mod}+hover underlines it)`
    ])
  })

  it('advertises a plain click when the TUI opens links itself', () => {
    expect(linkOpenHotkey(false)).toEqual(['click link', 'open link in browser (highlights on hover)'])
  })
})

describe('supportsHyperlinks terminal detection', () => {
  it('recognizes VS Code (xterm.js has handled OSC 8 Cmd+click since 1.72)', () => {
    expect(supportsHyperlinks({ env: { TERM_PROGRAM: 'vscode' }, stdoutSupported: false })).toBe(true)
  })

  it('does not recognize Apple Terminal (no OSC 8 support as of macOS 26)', () => {
    expect(supportsHyperlinks({ env: { TERM_PROGRAM: 'Apple_Terminal' }, stdoutSupported: false })).toBe(false)
  })
})

