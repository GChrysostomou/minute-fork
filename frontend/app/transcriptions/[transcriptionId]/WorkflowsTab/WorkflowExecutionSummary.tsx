'use client'

import { getWorkflowRunWorkflowsRunIdGetOptions } from '@/lib/client/@tanstack/react-query.gen'
import { WorkflowActionResponse } from '@/lib/client'
import { useQuery } from '@tanstack/react-query'
import { Loader2 } from 'lucide-react'

interface WorkflowExecutionSummaryProps {
  runId: string
  onReset: () => void
}

export function WorkflowExecutionSummary({
  runId,
  onReset,
}: WorkflowExecutionSummaryProps) {
  const {
    data: run,
    isLoading,
    isError,
  } = useQuery({
    ...getWorkflowRunWorkflowsRunIdGetOptions({ path: { run_id: runId } }),
  })

  if (isLoading) {
    return (
      <div className="flex items-center gap-2">
        <Loader2 className="animate-spin" />
        <p className="govuk-body govuk-!-margin-bottom-0">Loading results...</p>
      </div>
    )
  }

  if (isError || !run) {
    return (
      <p className="govuk-error-message">
        Failed to load workflow results. Please refresh.
      </p>
    )
  }

  const isRunFailed = run.status === 'failed'
  const executedActions = [...run.actions]
    .filter((a) => a.status === 'completed' || a.status === 'failed')
    .sort((a, b) => a.position - b.position)

  if (isRunFailed) {
    return (
      <div>
        <div className="govuk-warning-text">
          <span className="govuk-warning-text__icon" aria-hidden="true">
            !
          </span>
          <strong className="govuk-warning-text__text">
            <span className="govuk-visually-hidden">Warning</span>
            Workflow failed{run.error ? `: ${run.error}` : '.'}
          </strong>
        </div>
        <div className="govuk-button-group">
          <button
            className="govuk-button govuk-button--secondary"
            type="button"
            onClick={onReset}
          >
            Try again
          </button>
        </div>
      </div>
    )
  }

  return (
    <div>
      <div className="govuk-panel govuk-panel--confirmation govuk-!-margin-bottom-6">
        <h2 className="govuk-panel__title">Workflow completed</h2>
        <div className="govuk-panel__body">
          {executedActions.length} change
          {executedActions.length !== 1 ? 's' : ''} applied
        </div>
      </div>

      {executedActions.length > 0 && (
        <dl className="govuk-summary-list govuk-!-margin-bottom-6">
          {executedActions.map((action: WorkflowActionResponse) => {
            const succeeded = action.status === 'completed'
            return (
              <div className="govuk-summary-list__row" key={action.id}>
                <dt
                  className="govuk-summary-list__key"
                  style={{ width: '2rem' }}
                >
                  {succeeded ? (
                    <span style={{ color: '#00703c' }} aria-label="Success">
                      &#10003;
                    </span>
                  ) : (
                    <span style={{ color: '#d4351c' }} aria-label="Failed">
                      &#10007;
                    </span>
                  )}
                </dt>
                <dd className="govuk-summary-list__value">
                  <p className="govuk-body govuk-!-margin-bottom-0">
                    {action.description}
                  </p>
                  {action.result_url && (
                    <a
                      className="govuk-link"
                      href={action.result_url}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      {action.result_url}
                    </a>
                  )}
                  {!succeeded && action.error && (
                    <p className="govuk-error-message govuk-!-margin-top-1 govuk-!-margin-bottom-0">
                      {action.error}
                    </p>
                  )}
                </dd>
              </div>
            )
          })}
        </dl>
      )}

      <div className="govuk-button-group">
        <button
          className="govuk-button govuk-button--secondary"
          type="button"
          onClick={onReset}
        >
          Run another workflow
        </button>
      </div>
    </div>
  )
}
