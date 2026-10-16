import { Box, Text, useStdout, wrapAnsi } from '@clawcodex/ink'

import type { CompletionItem } from '../app/interfaces.js'
import { argumentHintFor } from '../app/slash/argumentHints.js'

export const COMPLETION_COLOR = '#a8b2f3'

export function CompletionMenu({ cols, compIdx, completions, input }: CompletionMenuProps) {
  const { stdout } = useStdout()

  if (!completions.length) {
    return null
  }

  const query = input.startsWith('/') ? input.slice(1).toLowerCase() : input.toLowerCase()
  const width = Math.max(8, cols - 2)
  const nameWidth = Math.max(1, Math.floor(width * 0.27) - 2)
  const descriptionWidth = Math.max(1, width - nameWidth - 4)
  const rowBudget = Math.max(2, Math.min(16, Math.floor((stdout?.rows ?? 24) / 2)))

  const entries = completions.map((item, index) => {
    const hint = item.text.startsWith('/') ? (argumentHintFor(item.text.slice(1)) ?? item.hint) : item.hint
    const description = [hint, item.meta].filter(Boolean).join(' ')
    const lines = wrapAnsi(description || ' ', descriptionWidth, { hard: true, trim: true }).split('\n')

    if (lines.length > rowBudget) {
      lines.length = rowBudget
      lines[rowBudget - 1] = `${Array.from(lines[rowBudget - 1] ?? '')
        .slice(0, -1)
        .join('')}…`
    }

    return { item, index, lines, height: lines.length }
  })

  const selected = Math.max(0, Math.min(compIdx, entries.length - 1))
  let start = selected
  let end = selected + 1
  let rows = entries[selected]!.height

  while (start > 0 && rows + entries[start - 1]!.height <= Math.floor(rowBudget / 2)) {
    rows += entries[--start]!.height
  }

  while (end < entries.length && rows + entries[end]!.height <= rowBudget) {
    rows += entries[end++]!.height
  }

  while (start > 0 && rows + entries[start - 1]!.height <= rowBudget) {
    rows += entries[--start]!.height
  }

  return (
    <Box
      flexDirection="column"
      flexShrink={0}
      height={Math.min(
        rowBudget,
        entries.reduce((sum, entry) => sum + entry.height, 0)
      )}
      width={width}
    >
      {entries.slice(start, end).map(({ item, index, lines }) => {
        const active = index === selected
        const matchAt = query ? item.display.toLowerCase().indexOf(query) : -1

        return (
          <Box key={`${index}:${item.text}:${item.display}`}>
            <Box flexShrink={0} width={2}>
              <Text color={COMPLETION_COLOR}>{active ? '❯ ' : '  '}</Text>
            </Box>
            <Box flexShrink={0} width={nameWidth}>
              <Text color={active ? COMPLETION_COLOR : '#ffffff'} wrap="truncate-start">
                {matchAt >= 0 ? (
                  <>
                    {item.display.slice(0, matchAt)}
                    <Text bold>{item.display.slice(matchAt, matchAt + query.length)}</Text>
                    {item.display.slice(matchAt + query.length)}
                  </>
                ) : (
                  item.display
                )}
              </Text>
            </Box>
            <Box flexShrink={0} width={2} />
            <Box flexDirection="column" flexShrink={0} width={descriptionWidth}>
              {lines.map((line, lineIndex) => (
                <Text color={active ? COMPLETION_COLOR : '#8e8e8e'} key={lineIndex}>
                  {line || ' '}
                </Text>
              ))}
            </Box>
          </Box>
        )
      })}
    </Box>
  )
}

interface CompletionMenuProps {
  input: string
  cols: number
  compIdx: number
  completions: CompletionItem[]
}
