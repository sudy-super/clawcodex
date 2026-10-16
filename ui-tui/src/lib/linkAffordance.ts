import { supportsHyperlinks } from '@clawcodex/ink'

import { isMac } from './platform.js'

export function linkOpenHotkey(supported: boolean = supportsHyperlinks()): [string, string] {
  if (supported) {
    const mod = isMac ? 'Cmd' : 'Ctrl'

    return [`${mod}+click link`, `open link in browser (${mod}+hover underlines it)`]
  }

  return ['click link', 'open link in browser (highlights on hover)']
}
