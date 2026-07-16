'use client'

import {
  deleteWorkflowRunWorkflowsRunIdDeleteMutation,
  executeWorkflowRunWorkflowsRunIdExecutePostMutation,
  getWorkflowRunWorkflowsRunIdGetOptions,
} from '@/lib/client/@tanstack/react-query.gen'
import { WorkflowActionResponse } from '@/lib/client'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Loader2 } from 'lucide-react'
import { useState } from 'react'

function actionTypeLabel(actionType: string): string {
  switch (actionType) {
    case 'create_ticket':
      return 'Create ticket'
    case 'update_ticket':
      return 'Update ticket'
    case 'close_ticket':
      return 'Close ticket'
    case 'move_ticket':
      return 'Move ticket'
    default:
      return actionType.replace(/_/g, ' ')
  }
}

interface WorkflowActionsReviewProps {
  runId: string
  onExecute: (runId: string) => void
  onCancel: () => void
}

export function WorkflowActionsReview({
  runId,
  onExecute,
  onCancel,
}: WorkflowActionsReviewProps) {
  const {
    data: run,
    isLoading,
    isError,
  } = useQuery({
    ...getWorkflowRunWorkflowsRunIdGetOptions({ path: { run_id: runId } }),
  })

  const [decisions, setDecisions] = useState<Record<string, boolean>>({})

  const { mutate: executeRun, isPending: isExecuting } = useMutation({
    ...executeWorkflowRunWorkflowsRunIdExecutePostMutation(),
    onSuccess: () => onExecute(runId),
  })

  const { mutate: deleteRun, isPending: isDeleting } = useMutation({
    ...deleteWorkflowRunWorkflowsRunIdDeleteMutation(),
    onSuccess: () => onCancel(),
  })

  if (isLoading) {
    return (
      <div className="flex items-center gap-2">
        <Loader2 className="animate-spin" />
        <p className="govuk-body govuk-!-margin-bottom-0">
          Loading proposed actions...
        </p>
      </div>
    )
  }

  if (isError || !run) {
    return (
      <p className="govuk-error-message">
        Failed to load proposed actions. Please refresh.
      </p>
    )
  }

  const actions = [...run.actions].sort((a, b) => a.position - b.position)

  // Default: all approved (true); only override if user has toggled
  const isApproved = (id: string) => decisions[id] ?? true

  const anyApproved = actions.some((a) => isApproved(a.id))

  const handleSubmit = () => {
    executeRun({
      path: { run_id: runId },
      body: {
        decisions: actions.map((a: WorkflowActionResponse) => ({
          action_id: a.id,
          approved: isApproved(a.id),
        })),
      },
    })
  }

  const handleCancel = () => {
    deleteRun({ path: { run_id: runId } })
  }

  return (
    <div>
      <h2 className="govuk-heading-m">Review proposed changes</h2>
      <p className="govuk-body">
        Nothing will be changed until you confirm below. Uncheck any actions you
        do not want to apply.
      </p>

      <div className="govuk-checkboxes govuk-!-margin-bottom-6">
        {actions.map((action: WorkflowActionResponse) => (
          <div className="govuk-checkboxes__item" key={action.id}>
            <input
              className="govuk-checkboxes__input"
              type="checkbox"
              id={action.id}
              checked={isApproved(action.id)}
              onChange={(e) =>
                setDecisions((prev) => ({
                  ...prev,
                  [action.id]: e.target.checked,
                }))
              }
            />
            <label className="govuk-checkboxes__label" htmlFor={action.id}>
              <strong>{actionTypeLabel(action.action_type)}</strong>
              {' — '}
              {action.description}
            </label>
          </div>
        ))}
      </div>

      {!anyApproved && (
        <p className="govuk-hint govuk-!-margin-bottom-3">
          Select at least one action to apply.
        </p>
      )}

      <div className="govuk-button-group">
        <button
          className="govuk-button"
          type="button"
          disabled={!anyApproved || isExecuting || isDeleting}
          onClick={handleSubmit}
        >
          {isExecuting ? 'Applying...' : 'Apply selected changes'}
        </button>
        <button
          className="govuk-button govuk-button--secondary"
          type="button"
          disabled={isExecuting || isDeleting}
          onClick={handleCancel}
        >
          {isDeleting ? 'Cancelling...' : 'Cancel'}
        </button>
      </div>
    </div>
  )
}
