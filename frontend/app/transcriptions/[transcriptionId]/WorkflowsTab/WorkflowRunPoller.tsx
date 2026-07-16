'use client'

import { getWorkflowRunWorkflowsRunIdGetOptions } from '@/lib/client/@tanstack/react-query.gen'
import { useQuery } from '@tanstack/react-query'
import { Loader2 } from 'lucide-react'

const TERMINAL_STATUSES = ['awaiting_confirmation', 'completed', 'failed']

interface WorkflowRunPollerProps {
  runId: string
  phase: 'preparing' | 'executing'
  onAwaiting: (runId: string) => void
  onDone: (runId: string) => void
  onFailed: (runId: string) => void
}

export function WorkflowRunPoller({
  runId,
  phase,
  onAwaiting,
  onDone,
  onFailed,
}: WorkflowRunPollerProps) {
  const { data: run, isError } = useQuery({
    ...getWorkflowRunWorkflowsRunIdGetOptions({ path: { run_id: runId } }),
    refetchInterval: (query) => {
      const status = query.state.data?.status
      if (!status || TERMINAL_STATUSES.includes(status)) return false
      return 2000
    },
    select: (data) => {
      if (data.status === 'awaiting_confirmation') {
        onAwaiting(data.id)
      } else if (data.status === 'completed') {
        onDone(data.id)
      } else if (data.status === 'failed') {
        onFailed(data.id)
      }
      return data
    },
  })

  if (isError) {
    return (
      <p className="govuk-error-message">
        Failed to fetch workflow status. Please refresh the page.
      </p>
    )
  }

  const statusMessage =
    phase === 'preparing'
      ? 'Preparing workflow — analysing minutes and fetching project data...'
      : 'Executing actions...'

  const status = run?.status

  if (status === 'failed') {
    return (
      <div className="govuk-warning-text">
        <span className="govuk-warning-text__icon" aria-hidden="true">
          !
        </span>
        <strong className="govuk-warning-text__text">
          <span className="govuk-visually-hidden">Warning</span>
          Workflow failed{run?.error ? `: ${run.error}` : '.'}
        </strong>
      </div>
    )
  }

  return (
    <div className="govuk-!-padding-top-4">
      <div className="flex items-center gap-3">
        <Loader2 className="animate-spin" size={20} />
        <p className="govuk-body govuk-!-margin-bottom-0">{statusMessage}</p>
      </div>
      {status && (
        <p className="govuk-hint govuk-!-margin-top-2">Status: {status}</p>
      )}
    </div>
  )
}
