# Screenshot evidence (to be captured on the workstation)

This directory intentionally contains instructions **rather than simulated images**. Add two genuine PNG files after capturing the running lab:

1. **grafana-monitoring.png** — open Grafana at http://127.0.0.1:3000, select *MacroPulse AI — Infrastructure*, switch the time range to *Last 15 minutes* (or *Last 30 minutes*), refresh, and wait for actual CPU and memory plots for the three `macropulse-api` pods. If a panel says *No data*, fix the panel/time range before taking the capture. Avoid showing passwords or access tokens.
2. **kubernetes-pods.png** — open Windows PowerShell and run:

~~~powershell
kubectl --context kind-macropulse-lab -n k-lab get pods,deployments,services
kubectl --context kind-macropulse-lab -n monitoring get pods,deployments,services
~~~

Capture the successful output with **Win+Shift+S** and save it as `screenshots/kubernetes-pods.png`. Redact any sensitive values if shown (ordinary pod names and service IPs from this lab are generally fine).

After these files are committed, embed them from the parent [README](../README.md):

~~~markdown
![Live Grafana CPU and memory monitoring for local MacroPulse AI pods](screenshots/grafana-monitoring.png)
![Three running Kubernetes backend replicas and monitoring services](screenshots/kubernetes-pods.png)
~~~

The images must reflect a real session; never manufacture a monitoring screenshot solely for portfolio presentation.
