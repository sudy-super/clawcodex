import { Box, Text } from '@clawcodex/ink'
import { useStore } from '@nanostores/react'

import { useGateway } from '../app/gatewayContext.js'
import type { AppOverlaysProps } from '../app/interfaces.js'
import { $overlayState, patchOverlayState } from '../app/overlayStore.js'
import { $uiLogoPalette, $uiPermissionMode, $uiSessionId, $uiTheme } from '../app/uiStore.js'

import { ActiveSessionSwitcher } from './activeSessionSwitcher.js'
import { FloatBox } from './appChrome.js'
import { BillingOverlay } from './billingOverlay.js'
import { LogoPicker } from './logoPicker.js'
import { MaskedPrompt } from './maskedPrompt.js'
import { MemoryPicker } from './memoryPicker.js'
import { ModelPicker } from './modelPicker.js'
import { OverlayHint } from './overlayControls.js'
import { PermissionsPicker } from './permissionsPicker.js'
import { PetPicker } from './petPicker.js'
import { PluginsHub } from './pluginsHub.js'
import { ApprovalPrompt, ClarifyPrompt, ConfirmPrompt, PlanApprovalPrompt } from './prompts.js'
import { QuestionPrompt } from './questionPrompt.js'
import { RewindPicker } from './rewindPicker.js'
import { SkillsHub } from './skillsHub.js'
import { WorktreeExitPrompt } from './worktreeExitPrompt.js'

export function PromptZone({
  cols,
  onApprovalChoice,
  onClarifyAnswer,
  onPlanApprovalChoice,
  onQuestionsAnswer,
  onRewindRestored,
  onSecretSubmit,
  onSudoSubmit
}: Pick<
  AppOverlaysProps,
  | 'cols'
  | 'onApprovalChoice'
  | 'onClarifyAnswer'
  | 'onPlanApprovalChoice'
  | 'onQuestionsAnswer'
  | 'onRewindRestored'
  | 'onSecretSubmit'
  | 'onSudoSubmit'
>) {
  const { gw } = useGateway()
  const sid = useStore($uiSessionId)
  const overlay = useStore($overlayState)
  const theme = useStore($uiTheme)

  // The exit flow outranks everything: the user has already asked to leave,
  // and the dialog's Esc explicitly returns to the session.
  if (overlay.worktreeExit) {
    return (
      <Box flexDirection="column" flexShrink={0} paddingX={1} paddingY={1}>
        <WorktreeExitPrompt req={overlay.worktreeExit} t={theme} />
      </Box>
    )
  }

  if (overlay.rewindPicker) {
    return (
      <RewindPicker
        gw={gw}
        onCancel={() => patchOverlayState({ rewindPicker: false })}
        onRestored={onRewindRestored}
        sessionId={sid}
        t={theme}
      />
    )
  }

  if (overlay.planApproval) {
    return (
      <Box flexDirection="column" flexShrink={0} paddingX={1} paddingY={1}>
        <PlanApprovalPrompt cols={cols} onChoice={onPlanApprovalChoice} req={overlay.planApproval} t={theme} />
      </Box>
    )
  }

  if (overlay.approval) {
    return (
      <Box flexDirection="column" flexShrink={0} paddingX={1} paddingY={1}>
        <ApprovalPrompt cols={cols} onChoice={onApprovalChoice} req={overlay.approval} t={theme} />
      </Box>
    )
  }

  // BELOW the approval box, deliberately. A permission request is a gate on an
  // action and must fail closed; a question is advisory. Ranked the other way,
  // an approval arriving during a (possibly 30-minute) question dialog renders
  // nothing at all, and ApprovalPrompt mounts with the allow option
  // preselected -- so a type-ahead Enter could approve something never seen.
  if (overlay.questions) {
    return (
      <Box flexDirection="column" flexShrink={0} paddingX={1} paddingY={1}>
        <QuestionPrompt
          cols={cols}
          onAnswer={onQuestionsAnswer}
          questions={overlay.questions.questions}
          t={theme}
        />
      </Box>
    )
  }

  if (overlay.billing) {
    const current = overlay.billing

    const onPatch = (next: Partial<typeof current>) =>
      patchOverlayState(prev => (prev.billing ? { ...prev, billing: { ...prev.billing, ...next } } : prev))

    const onClose = () => patchOverlayState({ billing: null })

    return (
      <Box flexDirection="column" flexShrink={0} paddingX={1} paddingY={1}>
        <BillingOverlay onClose={onClose} onPatch={onPatch} overlay={current} t={theme} />
      </Box>
    )
  }

  if (overlay.confirm) {
    const req = overlay.confirm

    const onConfirm = () => {
      patchOverlayState({ confirm: null })
      req.onConfirm()
    }

    const onCancel = () => patchOverlayState({ confirm: null })

    return (
      <Box flexDirection="column" flexShrink={0} paddingX={1} paddingY={1}>
        <ConfirmPrompt onCancel={onCancel} onConfirm={onConfirm} req={req} t={theme} />
      </Box>
    )
  }

  if (overlay.clarify) {
    return (
      <Box flexDirection="column" flexShrink={0} paddingX={1} paddingY={1}>
        <ClarifyPrompt
          cols={cols}
          onAnswer={onClarifyAnswer}
          onCancel={() => onClarifyAnswer('')}
          req={overlay.clarify}
          t={theme}
        />
      </Box>
    )
  }

  if (overlay.sudo) {
    return (
      <Box flexDirection="column" flexShrink={0} paddingX={1} paddingY={1}>
        <MaskedPrompt cols={cols} icon="🔐" label="sudo password required" onSubmit={onSudoSubmit} t={theme} />
      </Box>
    )
  }

  if (overlay.secret) {
    return (
      <Box flexDirection="column" flexShrink={0} paddingX={1} paddingY={1}>
        <MaskedPrompt
          cols={cols}
          icon="🔑"
          label={overlay.secret.prompt}
          onSubmit={onSecretSubmit}
          sub={`for ${overlay.secret.envVar}`}
          t={theme}
        />
      </Box>
    )
  }

  return null
}

export function FloatingOverlays({
  onActiveSessionSelect,
  onActiveSessionClose,
  onLogoSelect,
  onMemorySelect,
  onModelSelect,
  onNewLiveSession,
  onNewPromptSession,
  onPermissionsSelect,
  onResumeSelect,
  pagerPageSize
}: Pick<
  AppOverlaysProps,
  | 'onActiveSessionSelect'
  | 'onActiveSessionClose'
  | 'onLogoSelect'
  | 'onMemorySelect'
  | 'onModelSelect'
  | 'onNewLiveSession'
  | 'onNewPromptSession'
  | 'onPermissionsSelect'
  | 'onResumeSelect'
  | 'pagerPageSize'
>) {
  const { gw } = useGateway()
  const overlay = useStore($overlayState)
  const sid = useStore($uiSessionId)
  const theme = useStore($uiTheme)
  const logoPalette = useStore($uiLogoPalette)
  const permissionMode = useStore($uiPermissionMode)

  const hasAny =
    overlay.logoPicker ||
    overlay.memoryPicker ||
    overlay.modelPicker ||
    overlay.permissionsPicker ||
    overlay.pager ||
    overlay.petPicker ||
    overlay.sessions ||
    overlay.skillsHub ||
    overlay.pluginsHub

  if (!hasAny) {
    return null
  }

  return (
    <Box alignItems="flex-start" bottom="100%" flexDirection="column" left={0} position="absolute" right={0}>
      {overlay.sessions && (
        <FloatBox color={theme.color.border}>
          <ActiveSessionSwitcher
            currentSessionId={sid}
            gw={gw}
            onCancel={() => patchOverlayState({ sessions: false })}
            onClose={onActiveSessionClose}
            onNew={onNewLiveSession}
            onNewPrompt={onNewPromptSession}
            onResume={onResumeSelect}
            onSelect={onActiveSessionSelect}
            t={theme}
          />
        </FloatBox>
      )}

      {overlay.modelPicker && (
        <FloatBox color={theme.color.border}>
          <ModelPicker
            gw={gw}
            onCancel={() => patchOverlayState({ modelPicker: false })}
            onSelect={onModelSelect}
            sessionId={sid}
            t={theme}
          />
        </FloatBox>
      )}

      {overlay.logoPicker && (
        <FloatBox color={theme.color.border}>
          <LogoPicker
            current={logoPalette}
            onClose={() => patchOverlayState({ logoPicker: false })}
            onSelect={onLogoSelect}
            t={theme}
          />
        </FloatBox>
      )}

      {overlay.permissionsPicker && (
        <FloatBox color={theme.color.border}>
          <PermissionsPicker
            current={permissionMode}
            onClose={() => patchOverlayState({ permissionsPicker: false })}
            onSelect={onPermissionsSelect}
            t={theme}
          />
        </FloatBox>
      )}

      {overlay.memoryPicker && (
        <FloatBox color={theme.color.border}>
          <MemoryPicker
            gw={gw}
            onCancel={() => patchOverlayState({ memoryPicker: false })}
            onSelect={onMemorySelect}
            t={theme}
          />
        </FloatBox>
      )}

      {overlay.petPicker && (
        <FloatBox color={theme.color.border}>
          <PetPicker gw={gw} onClose={() => patchOverlayState({ petPicker: false })} t={theme} />
        </FloatBox>
      )}

      {overlay.skillsHub && (
        <FloatBox color={theme.color.border}>
          <SkillsHub gw={gw} onClose={() => patchOverlayState({ skillsHub: false })} t={theme} />
        </FloatBox>
      )}

      {overlay.pluginsHub && (
        <FloatBox color={theme.color.border}>
          <PluginsHub gw={gw} onClose={() => patchOverlayState({ pluginsHub: false })} t={theme} />
        </FloatBox>
      )}

      {overlay.pager && (
        <FloatBox color={theme.color.border}>
          <Box flexDirection="column" paddingX={1} paddingY={1}>
            {overlay.pager.title && (
              <Box justifyContent="center" marginBottom={1}>
                <Text bold color={theme.color.primary}>
                  {overlay.pager.title}
                </Text>
              </Box>
            )}

            {overlay.pager.lines.slice(overlay.pager.offset, overlay.pager.offset + pagerPageSize).map((line, i) => (
              <Text key={i}>{line}</Text>
            ))}

            <Box marginTop={1}>
              <OverlayHint t={theme}>
                {overlay.pager.offset + pagerPageSize < overlay.pager.lines.length
                  ? `↑↓/jk line · Enter/Space/PgDn page · b/PgUp back · g/G top/bottom · Esc/q close (${Math.min(overlay.pager.offset + pagerPageSize, overlay.pager.lines.length)}/${overlay.pager.lines.length})`
                  : `end · ↑↓/jk · b/PgUp back · g top · Esc/q close (${overlay.pager.lines.length} lines)`}
              </OverlayHint>
            </Box>
          </Box>
        </FloatBox>
      )}
    </Box>
  )
}
