{{- define "cloudcostradar-agent.name" -}}cloudcostradar-agent{{- end -}}
{{- define "cloudcostradar-agent.labels" -}}
app.kubernetes.io/name: cloudcostradar-agent
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}
{{- define "cloudcostradar-agent.secretName" -}}
{{- if .Values.ccradar.existingSecret -}}{{ .Values.ccradar.existingSecret }}{{- else -}}{{ .Release.Name }}-token{{- end -}}
{{- end -}}
