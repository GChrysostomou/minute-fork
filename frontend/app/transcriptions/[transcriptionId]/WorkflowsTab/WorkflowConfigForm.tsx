'use client'

import { createWorkflowRunWorkflowsPostMutation } from '@/lib/client/@tanstack/react-query.gen'
import { useMutation } from '@tanstack/react-query'
import { useForm, SubmitHandler } from 'react-hook-form'

interface JsonSchemaProperty {
  type: string
  title?: string
  description?: string
}

interface JsonSchema {
  type?: string
  properties?: Record<string, JsonSchemaProperty>
  required?: string[]
}

interface WorkflowConfigFormProps {
  transcriptionId: string
  workflowName: string
  configSchema: Record<string, unknown>
  onBack: () => void
  onRunCreated: (runId: string) => void
}

type FormValues = Record<string, string | number | boolean>

export function WorkflowConfigForm({
  transcriptionId,
  workflowName,
  configSchema,
  onBack,
  onRunCreated,
}: WorkflowConfigFormProps) {
  const schema = configSchema as JsonSchema
  const properties = schema.properties ?? {}
  const required = schema.required ?? []

  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<FormValues>()

  const { mutate, isPending, isError, error } = useMutation({
    ...createWorkflowRunWorkflowsPostMutation(),
    onSuccess: (data) => {
      onRunCreated(data.id)
    },
  })

  const onSubmit: SubmitHandler<FormValues> = (values) => {
    // Coerce integer fields from the string input values
    const config: Record<string, unknown> = {}
    for (const [key, prop] of Object.entries(properties)) {
      const raw = values[key]
      if (prop.type === 'integer' && raw !== undefined && raw !== '') {
        config[key] = Number(raw)
      } else if (prop.type === 'boolean') {
        config[key] = Boolean(raw)
      } else {
        config[key] = raw
      }
    }

    mutate({
      body: {
        transcription_id: transcriptionId,
        workflow_name: workflowName,
        config,
      },
    })
  }

  return (
    <div>
      <button
        className="govuk-back-link"
        style={{ background: 'none', border: 'none', cursor: 'pointer' }}
        onClick={onBack}
        type="button"
      >
        Back
      </button>

      <h2 className="govuk-heading-m govuk-!-margin-top-4">
        Configure workflow
      </h2>

      {isError && (
        <div className="govuk-error-summary" role="alert">
          <h2 className="govuk-error-summary__title">There was a problem</h2>
          <p className="govuk-body">
            {(error as Error)?.message ??
              'Failed to start the workflow. Please try again.'}
          </p>
        </div>
      )}

      <form onSubmit={handleSubmit(onSubmit)} noValidate>
        {Object.entries(properties).map(([key, prop]) => {
          const isRequired = required.includes(key)
          const fieldError = errors[key]
          const fieldId = `workflow-config-${key}`

          return (
            <div
              key={key}
              className={`govuk-form-group${fieldError ? 'govuk-form-group--error' : ''}`}
            >
              <label className="govuk-label" htmlFor={fieldId}>
                {prop.title ?? key}
                {isRequired && (
                  <span className="govuk-visually-hidden"> (required)</span>
                )}
              </label>
              {prop.description && (
                <div className="govuk-hint">{prop.description}</div>
              )}
              {fieldError && (
                <p className="govuk-error-message" id={`${fieldId}-error`}>
                  <span className="govuk-visually-hidden">Error: </span>
                  {String(fieldError.message)}
                </p>
              )}

              {prop.type === 'boolean' ? (
                <div className="govuk-checkboxes__item">
                  <input
                    className="govuk-checkboxes__input"
                    id={fieldId}
                    type="checkbox"
                    aria-describedby={
                      fieldError ? `${fieldId}-error` : undefined
                    }
                    {...register(key)}
                  />
                  <label
                    className="govuk-label govuk-checkboxes__label"
                    htmlFor={fieldId}
                  >
                    {prop.title ?? key}
                  </label>
                </div>
              ) : (
                <input
                  className={`govuk-input${fieldError ? 'govuk-input--error' : ''}`}
                  id={fieldId}
                  type={prop.type === 'integer' ? 'number' : 'text'}
                  aria-describedby={fieldError ? `${fieldId}-error` : undefined}
                  {...register(key, {
                    required: isRequired
                      ? `${prop.title ?? key} is required`
                      : false,
                    valueAsNumber: prop.type === 'integer',
                  })}
                />
              )}
            </div>
          )
        })}

        <div className="govuk-button-group">
          <button className="govuk-button" type="submit" disabled={isPending}>
            {isPending ? 'Starting...' : 'Start workflow'}
          </button>
          <button
            className="govuk-button govuk-button--secondary"
            type="button"
            onClick={onBack}
          >
            Cancel
          </button>
        </div>
      </form>
    </div>
  )
}
