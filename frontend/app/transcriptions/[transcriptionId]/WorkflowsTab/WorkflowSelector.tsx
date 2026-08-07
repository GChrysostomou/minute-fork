'use client'

import {
  githubStatusAuthGithubStatusGetOptions,
  listWorkflowsWorkflowsGetOptions,
} from '@/lib/client/@tanstack/react-query.gen'
import { WorkflowMetadata } from '@/lib/client'
import { API_PROXY_PATH } from '@/lib/api-proxy-path'
import { useQuery } from '@tanstack/react-query'
import { Loader2 } from 'lucide-react'
import { usePathname } from 'next/navigation'

// The only workflow today that calls out to an external service on the
// user's behalf, so it's the only one gated on a connected GitHub account.
const GITHUB_WORKFLOW_NAME = 'github_projects'

interface WorkflowSelectorProps {
  onSelect: (
    workflowName: string,
    configSchema: Record<string, unknown>
  ) => void
}

export function WorkflowSelector({ onSelect }: WorkflowSelectorProps) {
  const pathname = usePathname()
  const {
    data: workflows = [],
    isLoading,
    isError,
  } = useQuery({
    ...listWorkflowsWorkflowsGetOptions(),
    staleTime: Infinity,
  })

  const needsGithubAuth = workflows.some((w) => w.name === GITHUB_WORKFLOW_NAME)
  const { data: githubStatus } = useQuery({
    ...githubStatusAuthGithubStatusGetOptions(),
    enabled: needsGithubAuth,
  })

  function handleSelect(workflow: WorkflowMetadata) {
    if (
      workflow.name === GITHUB_WORKFLOW_NAME &&
      !githubStatus?.authenticated
    ) {
      window.location.href = `${API_PROXY_PATH}/auth/github?return_to=${encodeURIComponent(pathname)}`
      return
    }
    onSelect(workflow.name, workflow.config_schema as Record<string, unknown>)
  }

  if (isLoading) {
    return (
      <div className="flex items-center gap-2">
        <Loader2 className="animate-spin" />
        <p className="govuk-body govuk-!-margin-bottom-0">
          Loading workflows...
        </p>
      </div>
    )
  }

  if (isError) {
    return (
      <p className="govuk-error-message">
        Failed to load workflows. Please try again.
      </p>
    )
  }

  if (workflows.length === 0) {
    return <p className="govuk-body">No workflows are available.</p>
  }

  return (
    <div>
      <h2 className="govuk-heading-m">Select a workflow</h2>
      <div className="govuk-grid-row">
        {workflows.map((workflow: WorkflowMetadata) => (
          <div
            key={workflow.name}
            className="govuk-grid-column-one-half govuk-!-margin-bottom-4"
          >
            <div className="govuk-summary-card">
              <div className="govuk-summary-card__title-wrapper">
                <h2 className="govuk-summary-card__title">
                  {workflow.display_name}
                </h2>
              </div>
              <div className="govuk-summary-card__content">
                <p className="govuk-body">{workflow.description}</p>
                <button
                  className="govuk-button govuk-button--secondary govuk-!-margin-bottom-0"
                  onClick={() => handleSelect(workflow)}
                >
                  {workflow.name === GITHUB_WORKFLOW_NAME &&
                  !githubStatus?.authenticated
                    ? 'Connect GitHub'
                    : 'Select'}
                </button>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
