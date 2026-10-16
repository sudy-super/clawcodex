import { Box, Text, useInput, useStdout } from '@clawcodex/ink'
import { useEffect, useRef, useState } from 'react'

import type { GatewayClient } from '../gatewayClient.js'
import type { RewindMode, RewindPoint, RewindPointsResponse, RewindResponse } from '../gatewayTypes.js'
import { rpcErrorMessage } from '../lib/rpc.js'
import type { Theme } from '../theme.js'

import { OverlayHint, windowItems } from './overlayControls.js'

export const REWIND_COLOR = '#9ecbff'

export function rewindChoices(point: RewindPoint): Array<{ mode: RewindMode; label: string }> {
  return point.can_restore_code && point.files.length
    ? [
        { mode: 'both', label: 'Restore code and conversation' },
        { mode: 'conversation', label: 'Restore conversation (keep current code)' },
        { mode: 'code', label: 'Restore code (keep conversation)' }
      ]
    : [{ mode: 'conversation', label: 'Restore conversation' }]
}

export function RewindPicker({ gw, sessionId, onCancel, onRestored, t }: RewindPickerProps) {
  const [points, setPoints] = useState<RewindPoint[]>([])
  const [idx, setIdx] = useState(0)
  const [confirm, setConfirm] = useState(false)
  const [choice, setChoice] = useState(0)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const restoring = useRef(false)
  const mounted = useRef(true)
  const { stdout } = useStdout()
  const width = Math.max(12, stdout?.columns ?? 80)
  const visible = Math.max(1, Math.min(8, Math.floor(((stdout?.rows ?? 24) * 0.65 - 9) / 3)))
  useEffect(() => {
    mounted.current = true
    gw.request<RewindPointsResponse>('rewind.points', { session_id: sessionId })
      .then(result => {
        if (!mounted.current) {
          return
        }

        if (!result?.ok || !Array.isArray(result.points)) {
          throw new Error(result?.error || 'Invalid rewind points')
        }

        setPoints(result.points)
        setIdx(result.points.length)
      })
      .catch(e => {
        if (mounted.current) {
          setError(rpcErrorMessage(e))
        }
      })
      .finally(() => {
        if (mounted.current) {
          setLoading(false)
        }
      })

    return () => {
      mounted.current = false
    }
  }, [gw, sessionId])
  const point = points[idx]
  const choices = point ? rewindChoices(point) : []
  const selectedMode = choices[choice]?.mode
  useInput((ch, key) => {
    if (restoring.current) {
      return
    }

    if (key.escape) {
      if (confirm) {
        setConfirm(false)
        setError('')
      } else {
        onCancel()
      }

      return
    }

    if (loading) {
      return
    }

    if (key.upArrow || key.downArrow) {
      const step = key.upArrow ? -1 : 1

      if (confirm) {
        setChoice(i => Math.max(0, Math.min(choices.length - 1, i + step)))
      } else {
        setIdx(i => Math.max(0, Math.min(points.length, i + step)))
      }

      return
    }

    if (confirm && /^[1-3]$/.test(ch) && choices[Number(ch) - 1]) {
      setChoice(Number(ch) - 1)

      return
    }

    if (!key.return) {
      return
    }

    if (!confirm && idx === points.length) {
      onCancel()

      return
    }

    if (!point) {
      return
    }

    if (!confirm) {
      setChoice(0)
      setConfirm(true)

      return
    }

    const selected = choices[choice]

    if (!selected) {
      return
    }

    restoring.current = true
    setBusy(true)
    setError('')
    gw.request<RewindResponse>('rewind.restore', {
      session_id: sessionId,
      message_id: point.message_id,
      mode: selected.mode
    })
      .then(result => {
        if (!mounted.current) {
          return
        }

        if (!result?.ok || !Array.isArray(result.messages)) {
          throw new Error(result?.error || 'Rewind failed')
        }

        onRestored(result, selected.mode)
      })
      .catch(e => {
        if (mounted.current) {
          setError(rpcErrorMessage(e))
        }
      })
      .finally(() => {
        restoring.current = false

        if (mounted.current) {
          setBusy(false)
        }
      })
  })
  const { items, offset } = windowItems([...points, null], idx, visible)

  return (
    <Box
      borderBottom={false}
      borderColor={REWIND_COLOR}
      borderLeft={false}
      borderRight={false}
      borderStyle="single"
      borderTop
      flexDirection="column"
      flexShrink={0}
      paddingX={1}
      paddingY={1}
      width={width}
    >
      <Text bold color={REWIND_COLOR}>
        Rewind
      </Text>
      <Box marginBottom={1} marginTop={1}>
        <Text>
          {confirm ? 'Restore to before this message?' : 'Restore the code and/or conversation to the point before…'}
        </Text>
      </Box>
      {loading ? <Text dimColor>Loading messages…</Text> : null}
      {!loading && !points.length && !error ? <Text dimColor>No messages to rewind</Text> : null}
      {confirm && point ? (
        <Box flexDirection="column">
          <Text wrap="truncate-end">{point.text.replace(/\s+/g, ' ')}</Text>
          {point.timestamp ? <Text dimColor>{point.timestamp}</Text> : null}
          <Box flexDirection="column" marginBottom={1} marginTop={1}>
            <Text dimColor>
              {selectedMode === 'code' ? 'The conversation will be kept.' : 'The conversation will be forked.'}
            </Text>
            {selectedMode === 'conversation' ? (
              <Text dimColor>The current code will be kept.</Text>
            ) : (
              <Text dimColor wrap="truncate-end">
                The code will be restored <Text color={t.color.ok}>+{point.added}</Text>{' '}
                <Text color={t.color.error}>-{point.removed}</Text> in {point.files.join(', ')}.
              </Text>
            )}
            {!point.can_restore_code ? <Text dimColor>No code restore</Text> : null}
          </Box>
          {choices.map((item, i) => (
            <Text color={i === choice ? REWIND_COLOR : t.color.text} key={item.mode}>
              {i === choice ? '❯ ' : '  '}
              {i + 1}. {item.label}
            </Text>
          ))}
        </Box>
      ) : (
        <Box flexDirection="column">
          <Text dimColor>{offset ? `↑ ${offset} more` : ' '}</Text>
          {items.map((item, i) =>
            item ? (
              <Box flexDirection="column" height={3} key={item.message_id}>
                <Text color={offset + i === idx ? REWIND_COLOR : t.color.text} wrap="truncate-end">
                  {offset + i === idx ? '❯ ' : '  '}
                  {item.text.replace(/\s+/g, ' ')}
                </Text>
                <Text dimColor wrap="truncate-end">
                  {' '}
                  {item.files.length ? (
                    <>
                      {item.files.length === 1 ? item.files[0] : `${item.files.length} files changed`}{' '}
                      <Text color={t.color.ok}>+{item.added}</Text> <Text color={t.color.error}>-{item.removed}</Text>
                    </>
                  ) : item.can_restore_code ? (
                    'No code changes'
                  ) : (
                    'No code restore'
                  )}
                </Text>
              </Box>
            ) : (
              <Box height={3} key="current">
                <Text color={offset + i === idx ? REWIND_COLOR : t.color.text} italic>
                  {offset + i === idx ? '❯ ' : '  '}(current)
                </Text>
              </Box>
            )
          )}
          <Text dimColor>
            {offset + items.length < points.length + 1 ? `↓ ${points.length + 1 - offset - items.length} more` : ' '}
          </Text>
        </Box>
      )}
      {error ? <Text color={t.color.error}>{error}</Text> : null}
      {busy ? (
        <Text dimColor>Restoring…</Text>
      ) : (
        <OverlayHint t={t}>
          {`↑/↓ select · Enter ${confirm ? 'restore' : 'continue'} · Esc ${confirm ? 'back' : 'cancel'}`}
        </OverlayHint>
      )}
    </Box>
  )
}

interface RewindPickerProps {
  gw: GatewayClient
  sessionId: string | null
  onCancel: () => void
  onRestored: (result: RewindResponse, mode: RewindMode) => void
  t: Theme
}
