import { PassThrough } from 'node:stream'

import { Box, renderSync, type ScrollBoxHandle, Text } from '@clawcodex/ink'
import React from 'react'
import { describe, expect, it } from 'vitest'

import { TranscriptScrollbar } from '../components/appChrome.js'
import { stripAnsi } from '../lib/text.js'
import { DEFAULT_THEME } from '../theme.js'

// A non-scrollable transcript keeps only the scrollbar's column gutter.
// A scrollable transcript renders the track inside the fullscreen viewport.

const SENTINEL = 'BAR_PROBE'

/** Reports a stale-tall viewport, exactly as it would one frame after a widen. */
function staleTallHandle(viewportHeight: number, scrollHeight: number): ScrollBoxHandle {
  return {
    getFreshScrollHeight: () => scrollHeight,
    getLastManualScrollAt: () => 0,
    getPendingDelta: () => 0,
    getScrollHeight: () => scrollHeight,
    getScrollTop: () => 0,
    getViewportHeight: () => viewportHeight,
    getViewportTop: () => 0,
    isSticky: () => true,
    scrollBy: () => {},
    scrollTo: () => {},
    scrollToBottom: () => {},
    scrollToElement: () => {},
    setClampBounds: () => {},
    subscribe: () => () => {}
  } as unknown as ScrollBoxHandle
}

/**
 * Rows the bar occupies, measured from a frame we KNOW was painted.
 *
 * A fixed settle would be load-dependent, and here it would fail open: with the
 * fix the bar paints nothing, so "no rows yet" is indistinguishable from "not
 * rendered yet" and a slow CI box would turn this green against the bug. The
 * sentinel is a positive signal — once it appears a frame has flushed, and the
 * bar's contribution to that frame's height is exactly what we measure.
 */
async function paintedRows(bar: null | { scrollHeight: number; viewportHeight: number }) {
  const stdout = new PassThrough()
  const stdin = new PassThrough()
  const stderr = new PassThrough()
  let out = ''

  stdout.on('data', (c: Buffer) => {
    out += c.toString()
  })
  Object.assign(stdout, { columns: 80, isTTY: true, rows: 24 })
  Object.assign(stdin, { isTTY: true, ref: () => {}, setRawMode: () => {}, unref: () => {} })

  const app = renderSync(
    React.createElement(
      Box,
      { flexDirection: 'row' },
      React.createElement(Text, null, SENTINEL),
      bar
        ? React.createElement(TranscriptScrollbar, {
            scrollRef: { current: staleTallHandle(bar.viewportHeight, bar.scrollHeight) },
            t: DEFAULT_THEME
          })
        : null
    ),
    {
      exitOnCtrlC: false,
      patchConsole: false,
      stderr: stderr as unknown as NodeJS.WriteStream,
      stdin: stdin as unknown as NodeJS.ReadStream,
      stdout: stdout as unknown as NodeJS.WriteStream
    }
  )

  // Two conditions, both required. The sentinel proves a frame was flushed —
  // without it, "no rows yet" and "not rendered yet" are indistinguishable and
  // the assertion would fail OPEN against the bug. The quiet period then proves
  // the frame is COMPLETE: ink writes incrementally, so measuring on first
  // paint can catch the sentinel in one chunk and the bar's rows in a later
  // one. Both renders settle the same way, which is what makes their row counts
  // comparable.
  const deadline = Date.now() + 15_000
  let quiet = 0
  let lastSize = -1

  while (Date.now() < deadline) {
    if (out.length === lastSize && stripAnsi(out).includes(SENTINEL)) {
      if (++quiet >= 5) {
        break
      }
    } else {
      quiet = 0
      lastSize = out.length
    }

    await new Promise(resolve => setTimeout(resolve, 20))
  }

  app.unmount()

  const painted = stripAnsi(out)

  if (!painted.includes(SENTINEL) || quiet < 5) {
    throw new Error('renderer never settled a frame')
  }

  return (painted.match(/\n/g) ?? []).length
}

describe('TranscriptScrollbar', () => {

  it('adds no rows when the content fits', async () => {
    const baseline = await paintedRows(null)

    expect(await paintedRows({ scrollHeight: 40, viewportHeight: 40 })).toBe(baseline)
  })

  it('renders a track when the transcript is scrollable', async () => {
    const baseline = await paintedRows(null)

    expect(await paintedRows({ scrollHeight: 120, viewportHeight: 40 })).toBeGreaterThan(baseline)
  })
})
