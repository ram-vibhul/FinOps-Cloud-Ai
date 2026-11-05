{{- define "costlens-agent.name" -}}costlens-agent{{- end -}}
{{- define "costlens-agent.labels" -}}
app.kubernetes.io/name: costlens-agent
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}
{{- define "costlens-agent.secretName" -}}
{{- if .Values.costlens.existingSecret -}}{{ .Values.costlens.existingSecret }}{{- else -}}{{ .Release.Name }}-token{{- end -}}
{{- end -}}
