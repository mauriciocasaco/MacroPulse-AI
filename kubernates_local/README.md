# Local Kubernetes & Observability Lab

This folder documents an **experimental Windows 11 / Docker Desktop / kind lab** used to run and observe MacroPulse AI's FastAPI backend. It is **not** the application's production infrastructure: the public demo uses Cloudflare Workers and Render.

## Architecture and observed deployment

- **Orchestrator:** Kubernetes in Docker (kind), context `kind-macropulse-lab`; Docker container `macropulse-lab-control-plane`.
- **Application namespace:** `k-lab`, deployment `macropulse-api`, with **3 ready replicas** and service `macropulse-api:8000`.
- **Monitoring namespace:** `monitoring`, with Grafana, Prometheus server and kube-state-metrics.
- **Telemetry:** Prometheus scrapes the local cluster; Grafana dashboards can plot container CPU and working-set memory per pod.
- **Access:** temporary `kubectl port-forward` connections bound to local loopback (`127.0.0.1`).

These component names and readiness counts were observed in the live local lab on **2026-11-17**. They describe a workstation experiment, not an HA or production-certified deployment.

## Prerequisites

Windows 11, Docker Desktop with a running Linux container engine, PowerShell 5.1+ (7 recommended), Docker CLI, `kubectl`, and the **already-created** kind cluster `macropulse-lab`.

**Scope:** the included startup scripts restart and inspect this existing cluster; they **do not recreate a cluster from an empty machine**. The original installation manifests, exact image build and Helm values have not yet been audited/exported to this repository. Reproducibility of the complete installation requires those reviewed manifests and pinned versions; this folder currently provides reproducible day-to-day operations on the established lab.

## Start and stop (Windows)

From `kubernates_local/`, double-click `Start-KLab.cmd`, or run:

~~~powershell
./kubernates_local/Start-KLab.ps1
~~~

The script starts Docker Desktop if needed, starts the existing kind control-plane container, waits for Kubernetes, checks the API/Grafana/Prometheus deployments and launches loopback-only port forwards in the background. It leaves unrelated processes alone and warns if a local port is occupied. Managed process identifiers and diagnostics are kept **outside Git** in `%LOCALAPPDATA%\MacroPulse-AI\k-lab\`.

| Tool | Local address | Forwarded service |
| --- | --- | --- |
| Grafana | http://127.0.0.1:3000 | `monitoring/grafana:80` |
| FastAPI Swagger | http://127.0.0.1:8000/docs | `k-lab/macropulse-api:8000` |
| Prometheus | http://127.0.0.1:9090 | `monitoring/prometheus-server:80` |

To shut down the managed forwards and the kind node (not Docker Desktop), double-click `Stop-KLab.cmd`, or run:

~~~powershell
./kubernates_local/Stop-KLab.ps1
~~~

These scripts were derived from the locally used startup helpers; because the cluster is on a separate Windows machine, GitHub changes alone do not constitute an end-to-end local execution test.

## Inspect the cluster

Run these commands in PowerShell from any folder:

~~~powershell
kubectl --context kind-macropulse-lab -n k-lab get pods,deployments,services
kubectl --context kind-macropulse-lab -n monitoring get pods,deployments,services
kubectl --context kind-macropulse-lab -n k-lab logs deployment/macropulse-api --tail=50
kubectl --context kind-macropulse-lab -n k-lab rollout status deployment/macropulse-api
~~~

To manually expose the dashboards (keep each `port-forward` terminal open):

~~~powershell
kubectl --context kind-macropulse-lab -n monitoring port-forward service/grafana 3000:80
kubectl --context kind-macropulse-lab -n monitoring port-forward service/prometheus-server 9090:80
kubectl --context kind-macropulse-lab -n k-lab port-forward service/macropulse-api 8000:8000
~~~

To demonstrate **manual** horizontal scaling of the workload:

~~~powershell
kubectl --context kind-macropulse-lab -n k-lab scale deployment/macropulse-api --replicas=5
kubectl --context kind-macropulse-lab -n k-lab rollout status deployment/macropulse-api
kubectl --context kind-macropulse-lab -n k-lab get pods
# Restore the documented baseline:
kubectl --context kind-macropulse-lab -n k-lab scale deployment/macropulse-api --replicas=3
~~~

Manual scaling is not proof of autoscaling, throughput gains or load balancing under traffic. Verify request behavior and metrics separately before making those claims.

## Observe with Grafana / Prometheus

In **Grafana → Explore → Prometheus → Code**, choose **Last 15 minutes** and run:

**Scrape targets (`1` = target responds):**

~~~promql
up
~~~

**FastAPI memory working set by pod (MiB):**

~~~promql
sum by (pod) (
  container_memory_working_set_bytes{
    namespace="k-lab",
    pod=~"macropulse-api-.*",
    container!="",
    container!="POD"
  }
) / 1024 / 1024
~~~

**FastAPI CPU consumption by pod (millicores):**

~~~promql
sum by (pod) (
  rate(container_cpu_usage_seconds_total{
    namespace="k-lab",
    pod=~"macropulse-api-.*",
    container!="",
    container!="POD"
  }[5m])
) * 1000
~~~

If a saved Grafana dashboard says **No data**, first confirm its time picker is not pinned to an earlier date, then test `up` in Explore. A restart of non-persistent Prometheus storage may have discarded earlier samples. `up=1` shows an individual scrape target is reachable; it does not by itself prove that every application metric or custom panel query is valid.

## Evidence and screenshots

The intended portfolio evidence is **two unedited screenshots from the actual workstation**:

1. `screenshots/grafana-monitoring.png` — Grafana dashboard with three current per-pod CPU / memory time series and a visible relative time range.
2. `screenshots/kubernetes-pods.png` — PowerShell with `kubectl get pods,deployments,services` showing the running application replicas and monitoring services.

See [screenshots/README.md](screenshots/README.md) for capture instructions. **Screenshots are not yet committed**, because the live Grafana interface and Windows desktop are not remotely accessible from GitHub. Do not substitute the previously captured **No data** dashboard or a recreated/mock terminal image for real evidence.

## Security and scope

Keep API keys, private environment variables, PostgreSQL connection strings, Kubernetes Secrets, local kubeconfig files and unrestricted `kubectl get ... -o yaml` dumps **out of Git**. Screenshots should show no private credentials or personal paths. Service port forwards intentionally bind to `127.0.0.1` only.

This experiment demonstrates containerized workload operation, replica inspection and CPU/memory observability. It does not assert Kubernetes is used by the public deployment, nor that there is auto-scaling, high availability, production SLO alerting or a complete infrastructure-as-code bootstrap.
