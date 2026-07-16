'use client'

import { WorkflowActionsReview } from './WorkflowActionsReview'
import { WorkflowConfigForm } from './WorkflowConfigForm'
import { WorkflowExecutionSummary } from './WorkflowExecutionSummary'
import { WorkflowRunPoller } from './WorkflowRunPoller'
import { WorkflowSelector } from './WorkflowSelector'
import { useState } from 'react'

type WorkflowPhase =
  | { stage: 'idle' }
  | {
      stage: 'configuring'
      workflowName: string
      configSchema: Record<string, unknown>
    }
  | { stage: 'preparing'; runId: string }
  | { stage: 'reviewing'; runId: string }
  | { stage: 'executing'; runId: string }
  | { stage: 'done'; runId: string }

export function WorkflowsTab({ transcriptionId }: { transcriptionId: string }) {
  const [phase, setPhase] = useState<WorkflowPhase>({ stage: 'idle' })

  switch (phase.stage) {
    case 'idle':
      return (
        <WorkflowSelector
          onSelect={(workflowName, configSchema) =>
            setPhase({ stage: 'configuring', workflowName, configSchema })
          }
        />
      )

    case 'configuring':
      return (
        <WorkflowConfigForm
          transcriptionId={transcriptionId}
          workflowName={phase.workflowName}
          configSchema={phase.configSchema}
          onBack={() => setPhase({ stage: 'idle' })}
          onRunCreated={(runId) => setPhase({ stage: 'preparing', runId })}
        />
      )

    case 'preparing':
      return (
        <WorkflowRunPoller
          runId={phase.runId}
          phase="preparing"
          onAwaiting={(runId) => setPhase({ stage: 'reviewing', runId })}
          onDone={(runId) => setPhase({ stage: 'done', runId })}
          onFailed={(runId) => setPhase({ stage: 'done', runId })}
        />
      )

    case 'reviewing':
      return (
        <WorkflowActionsReview
          runId={phase.runId}
          onExecute={(runId) => setPhase({ stage: 'executing', runId })}
          onCancel={() => setPhase({ stage: 'idle' })}
        />
      )

    case 'executing':
      return (
        <WorkflowRunPoller
          runId={phase.runId}
          phase="executing"
          onAwaiting={() => {}}
          onDone={(runId) => setPhase({ stage: 'done', runId })}
          onFailed={(runId) => setPhase({ stage: 'done', runId })}
        />
      )

    case 'done':
      return (
        <WorkflowExecutionSummary
          runId={phase.runId}
          onReset={() => setPhase({ stage: 'idle' })}
        />
      )
  }
}
