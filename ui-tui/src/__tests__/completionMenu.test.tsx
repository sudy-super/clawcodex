import { PassThrough } from 'node:stream'

import { renderSync, stringWidth } from '@clawcodex/ink'
import React from 'react'
import { describe, expect, it } from 'vitest'

import type { CompletionItem } from '../app/interfaces.js'
import { CompletionMenu } from '../components/completionMenu.js'
import { stripAnsi } from '../lib/text.js'

function renderMenu(items: CompletionItem[], cols = 100, selected = 0) {
  const stdout = new PassThrough()
  const stdin = new PassThrough()
  const stderr = new PassThrough()
  let output = ''
  stdout.on('data', chunk => {
    output += chunk.toString()
  })
  Object.assign(stdout, { columns: cols, rows: 24 })
  Object.assign(stdin, { isTTY: false })

  const app = renderSync(<CompletionMenu cols={cols} compIdx={selected} completions={items} input="/ex" />, {
    patchConsole: false,
    stdout: stdout as NodeJS.WriteStream,
    stderr: stderr as NodeJS.WriteStream,
    stdin: stdin as NodeJS.ReadStream
  })

  const rendered = output
  app.unmount()
  app.cleanup()

  return stripAnsi(rendered).split('\n')
}

describe('completion menu', () => {
  it('renders aligned names and wrapped descriptions without a box', () => {
    const lines = renderMenu([
      { text: '/exit', display: '/exit', meta: 'Exit clawcodex' },
      {
        text: '/export-fixture',
        display: '/export-fixture',
        meta: 'Export a conversation into a readable document with all messages, tool details and the original order retained for review.'
      }
    ])

    expect(lines[0]).toContain('❯ /exit')
    const firstDescription = lines.find(line => line.includes('Exit clawcodex'))!
    const secondDescription = lines.find(line => line.includes('Export a conversation'))!
    expect(firstDescription.indexOf('Exit clawcodex')).toBe(secondDescription.indexOf('Export a conversation'))
    expect(lines.join('\n')).toContain('review.')
    expect(lines.join('\n')).not.toMatch(/[╔╗╚╝║│]/)
    expect(lines.filter(line => line.includes('❯'))).toHaveLength(1)
  })

  it('keeps a fixed menu height while moving through variable-height descriptions', () => {
    const items = Array.from({ length: 25 }, (_, i) => ({
      text: `/example-${i}`,
      display: `/example-${i}`,
      meta:
        i % 2 ? 'A short explanation' : 'A longer explanation that wraps onto several lines inside this narrow menu.'
    }))

    const first = renderMenu(items, 40, 0)
    const middle = renderMenu(items, 40, 12)
    const last = renderMenu(items, 40, 24)
    expect(first.length).toBe(12)
    expect(middle.length).toBe(first.length)
    expect(last.length).toBe(first.length)
    expect(renderMenu([{ text: '/exit', display: '/exit', meta: 'Exit clawcodex' }], 40)).toHaveLength(1)
  })

  it('keeps the end of long names and the selected candidate in a narrow viewport', () => {
    const items = Array.from({ length: 30 }, (_, i) => ({
      text: `/ex-plugin-namespace-very-long-skill-${i}`,
      display: `/ex-plugin-namespace-very-long-skill-${i}`,
      meta: `Description ${i} with 日本語 and a longer explanation that wraps.`
    }))

    const lines = renderMenu(items, 40, 29)
    expect(lines.join('\n')).toContain('…kill-29')
    expect(lines.join('\n')).not.toContain('/ex-plugin-namespace')
    expect(lines.some(line => line.includes('…'))).toBe(true)
    expect(lines.filter(line => line.includes('❯'))).toHaveLength(1)
    expect(lines.length).toBeLessThanOrEqual(12)
    expect(lines.every(line => stringWidth(line) <= 38)).toBe(true)
    expect(items[29]?.text).toBe('/ex-plugin-namespace-very-long-skill-29')
  })
})
