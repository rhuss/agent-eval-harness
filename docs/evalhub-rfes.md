# EvalHub / TrustyAI Operator RFEs

Gaps discovered during deployment of the agent-eval provider on RHOAI 3.4.

---

## RFE 1: Parameter Passthrough to Benchmark Pods

**Summary:** The `parameters{}` field in job submission requests is not forwarded to benchmark pod job specs.

**Impact:** Custom providers cannot receive runtime configuration from the job submission. Parameters such as API keys, model names, and dataset paths have no way to reach the benchmark pod at runtime.

**Current workaround:** All configuration must be baked into the container image or mounted via ConfigMap, which defeats the purpose of the `parameters{}` field and makes the same provider image unusable across different evaluation scenarios without rebuilding.

**Proposed solution:** Forward the `parameters{}` map from the job submission request into the benchmark pod's job spec (e.g., as environment variables or a mounted JSON/YAML file) so that custom providers can read runtime configuration without image rebuilds.

---

## RFE 2: Volume Mount Support in Provider Spec

**Summary:** There is no way to declare volumes in the provider spec or EvalHub CR for custom providers.

**Impact:** Configuration files (eval.yaml, test case datasets) must be baked into the container image instead of being mounted at runtime. This makes iteration slow and couples image builds to specific evaluation configurations.

**Note:** The operator already mounts an `emptyDir` volume at `/data`, which overwrites any files baked into the image at that path. This forces providers to use alternative paths (e.g., `/app/eval-config/`) for baked-in config, adding fragility.

**Current workaround:** Bake all config files into the container image at a path other than `/data` (e.g., `/app/eval-config/`), and rebuild the image for every config change.

**Proposed solution:** Allow volume specifications (ConfigMap, Secret, PVC) in the provider definition or EvalHub CR. These volumes should be injected into benchmark pods at operator-managed mount points, enabling runtime config without image rebuilds.

---

## RFE 3: Per-Job Environment Variable Injection

**Summary:** The EvalHub CR `spec.env[]` sets environment variables on the EvalHub pod itself, not on benchmark pods. There is no mechanism to inject per-job environment variables into benchmark pods at submission time.

**Impact:** Benchmark pods that need credentials (e.g., `ANTHROPIC_API_KEY`, `CLAUDE_CODE_USE_VERTEX`, `GOOGLE_APPLICATION_CREDENTIALS`) or runtime configuration as env vars have no way to receive them. The provider spec `env[]` field exists but only controls global env vars baked into the provider definition — it cannot vary per job submission.

**Current workaround:** Bake credentials into the container image (security risk) or use a Kyverno mutating admission policy to inject secrets into pods matching a label selector (complex, cluster-admin-only).

**Proposed solution:** Allow job submissions to specify environment variables (or Secret references) that get injected into the benchmark pod. This would enable per-job credential injection without image rebuilds or external admission controllers.
